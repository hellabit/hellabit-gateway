from __future__ import annotations
import argparse
import fcntl
import os
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from urllib.parse import urlsplit

from .ledger import Error, Ledger, canonical, identifier
from .issuer import Issuer
from .rails import SandboxBank, sandbox_usdc_funding
from .payouts import Payouts
from .operations import Operations
from .readiness import Readiness
from . import __version__

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 8192


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Error("duplicate_json_key", 400)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict): raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise Error("invalid_json", 400) from None


def fields(body, required, optional=()):
    if set(body) - set(required) - set(optional) or set(required) - set(body):
        raise Error("invalid_request_fields", 400)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, book, port=0):
        self.book, self.session, self.csrf = book, secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        lock_path = book.path.with_suffix(".server.lock")
        self.writer_lock = os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600), "a")
        try:
            fcntl.flock(self.writer_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            Operations(book)
            Readiness(book)
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            self.writer_lock.close(); raise
        self.origins = {f"http://127.0.0.1:{self.server_port}", f"http://localhost:{self.server_port}"}

    def server_close(self):
        super().server_close()
        if not self.writer_lock.closed: self.writer_lock.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *_args):
        pass

    def _guard_host(self):
        host = self.headers.get("Host", "")
        if len(self.headers.get_all("Host", [])) != 1 or "http://" + host not in self.server.origins:
            raise Error("host_not_allowed", 403)
        if self.headers.get("Origin") and self.headers["Origin"] not in self.server.origins:
            raise Error("origin_not_allowed", 403)
        if self.client_address[0] != "127.0.0.1":
            raise Error("loopback_only", 403)

    def _owner(self):
        jar = cookies.SimpleCookie()
        try: jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError: return False
        value = jar.get("hb_owner")
        return bool(value and secrets.compare_digest(value.value, self.server.session))

    def _authorize(self, tenant=None, owner_only=False):
        if self._owner():
            if self.command == "POST" and (self.headers.get("Origin") not in self.server.origins or not secrets.compare_digest(self.headers.get("X-Hellabit-CSRF", ""), self.server.csrf)):
                raise Error("csrf_required", 403)
            return {"tenant": tenant, "role": "owner", "customer": None}
        if owner_only:
            raise Error("owner_session_required", 403)
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise Error("unauthorized", 401)
        principal = self.server.book.principal(auth[7:])
        if not tenant or not secrets.compare_digest(principal["tenant"], tenant):
            raise Error("workspace_forbidden", 403)
        return principal

    def _reply(self, value, status=200, content_type="application/json; charset=utf-8", *, owner_cookie=False):
        body = value if isinstance(value, bytes) else canonical(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if owner_cookie:
            self.send_header("Set-Cookie", f"hb_owner={self.server.session}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise Error("content_length_required", 411)
        if not re.fullmatch(r"[0-9]{1,8}", self.headers.get("Content-Length", "")):
            raise Error("invalid_content_length", 400)
        size = int(self.headers["Content-Length"])
        if size > MAX_BODY: raise Error("body_too_large", 413)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise Error("json_required", 415)
        raw = self.rfile.read(size)
        if len(raw) != size: raise Error("incomplete_body", 400)
        return strict_json(raw)

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        with self.server.book.request_authority(self._write_authority):self._dispatch(True)

    def _write_authority(self,db):
        if self._owner():return
        auth=self.headers.get('Authorization','')
        if not auth.startswith('Bearer '):raise Error('unauthorized',401)
        self.server.book.key_principal(db,auth[7:])

    def _dispatch(self, mutation):
        try:
            self._guard_host()
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment or "%" in parsed.path or "//" in parsed.path:
                raise Error("exact_route_required", 400)
            route = parsed.path
            if not mutation and route in ("/", "/app.js", "/app.css", "/favicon.svg"):
                filename = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/favicon.svg": "favicon.svg"}[route]
                payload = (ROOT / "web" / filename).read_bytes()
                if route == "/": payload = payload.replace(b"__CSRF__", self.server.csrf.encode())
                mime = {"/": "text/html", "/app.js": "text/javascript", "/app.css": "text/css", "/favicon.svg": "image/svg+xml"}[route]
                self._reply(payload, content_type=mime + "; charset=utf-8", owner_cookie=route == "/")
                return
            if not mutation and route == "/api/health":
                self._reply({"service": "hellabit-gateway", "mode": "sandbox", "live_enabled": False}); return
            base = "/api/v1/workspaces"
            if route == '/api/v1/connection-plan' and not mutation:
                self._authorize(owner_only=True)
                self._reply(json.loads((ROOT / 'docs' / 'CONNECTIONS.json').read_text())); return
            if route == base:
                self._authorize(owner_only=True)
                if mutation:
                    body = self._body(); fields(body, ["name"], ["country"])
                    self._reply(self.server.book.create_tenant(body["name"], body.get("country", "CA")), 201)
                else:
                    with self.server.book.db() as db:
                        rows = [dict(x) for x in db.execute("SELECT id,name,country,created FROM tenants ORDER BY created,rowid")]
                    self._reply({"workspaces": rows})
                return
            if not route.startswith(base + "/"): raise Error("route_not_found", 404)
            rest = route[len(base) + 1:].split("/"); tenant = identifier(rest[0]); tail = "/".join(rest[1:])
            owner_only = tail.startswith("sandbox/") or tail.startswith("issuer/") or tail.startswith("enroll/") or tail.startswith("payouts") or tail.startswith("merchant-keys") or tail.startswith("ops/") or tail.startswith("readiness/") or tail in ("keys", 'diagnostics','ops','readiness')
            principal = self._authorize(tenant, owner_only=owner_only)
            book, issuer = self.server.book, Issuer(self.server.book)
            if principal["role"] == "agent" and tail:
                if tail == 'operations/lookup' and mutation:
                    pass
                elif tail.startswith("operations/") or tail.startswith("deliveries/"):
                    op = book.operation(tenant, tail.split("/")[1])
                    if op["customer"] != principal["customer"]:
                        raise Error("customer_forbidden", 403)
                    if mutation and not (tail.startswith("operations/") and tail.endswith("/run")):
                        raise Error("agent_scope_forbidden", 403)
                elif not (mutation and tail == "operations"):
                    raise Error("agent_scope_forbidden", 403)
            if not mutation:
                if not tail:
                    if principal["role"]=="agent":
                        self._reply(book.agent_state(tenant,principal["customer"]));return
                    snapshot={**book.snapshot(tenant),"issuer":issuer.state(tenant)}
                    if principal["role"]=="owner":
                        Operations(book)
                        with book.db() as db:
                            ids=[p["id"] for p in snapshot["payouts"]]
                            marks=",".join("?" for _ in ids)
                            intents={r["payout"]:dict(r) for r in db.execute(
                                "SELECT payout,id,state FROM execution_intents WHERE tenant=? AND payout IN("+marks+")",
                                (tenant,*ids))} if ids else {}
                        for payout in snapshot["payouts"]:
                            info=intents.get(payout["id"],{})
                            payout["intent_id"]=info.get("id");payout["intent_state"]=info.get("state")
                    self._reply(snapshot); return
                if tail=='ops':self._reply(Operations(book).list(tenant));return
                if tail=='readiness':self._reply(Readiness(book).report(tenant));return
                if tail=='readiness/packet':self._reply(Readiness(book).packet(tenant));return
                if tail == "reconcile": self._reply(book.reconcile(tenant)); return
                if tail == 'keys': self._reply(book.keys(tenant)); return
                if tail == 'diagnostics':
                    check = book.reconcile(tenant)
                    self._reply({'schema':'hellabit.redacted-diagnostics.v1','service_version':__version__,'mode':'sandbox','live_payments_enabled':False,'runtime':{'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version},'integer_decimals':{'CAD':2,'USD':2,'USDC-testnet':6,'workspace-token':6},'internal_check':{'ok':check['ok'],'journal_count':check['journal_count'],'scope':check['scope']},'external_settlement_verified':False,'personal_data_included':False}); return
                if tail.startswith('activity/') and tail.count('/') == 1:
                    asset = tail.split('/')[1]
                    self._reply(book.activity(tenant, None if asset == 'all' else asset)); return
                if tail.startswith("operations/") and tail.count("/") == 1:
                    self._reply(book.operation(tenant, tail.split("/")[1])); return
                if tail.startswith("deliveries/") and tail.count("/") == 1:
                    self._reply(book.delivery(tenant, tail.split("/")[1])); return
                raise Error("route_not_found", 404)
            body = self._body()
            principal=self._authorize(tenant,owner_only=owner_only)
            if principal["role"] == "agent" and tail == "operations" and body.get("customer") != principal["customer"]:
                raise Error("customer_forbidden", 403)
            if tail == "customers":
                fields(body, ["customer"]); result = book.add_customer(tenant, body["customer"])
            elif tail == "merchant-keys":
                fields(body, []); result = book.issue_merchant_key(tenant)
            elif tail.startswith('merchant-keys/') and tail.count('/') == 2 and tail.endswith('/revoke'):
                fields(body, []); result = book.revoke_merchant_key(tenant, tail.split('/')[1])
            elif tail == "agent-keys":
                fields(body, ["customer"]); result = book.issue_agent_key(tenant, body["customer"])
            elif tail.startswith("agent-keys/") and tail.count("/") == 2 and tail.endswith("/revoke"):
                fields(body, []); result = book.revoke_agent_key(tenant, tail.split("/")[1])
            elif tail == "quotes":
                fields(body, ["asset", "amount", "fee"], ["resource", 'input'])
                result = book.create_quote(tenant, body["asset"], body["amount"], body["fee"], body.get("resource", "report"), body.get('input'))
            elif tail == "operations":
                fields(body, ["customer", "quote", "key"])
                result = book.reserve(tenant, body["customer"], body["quote"], body["key"])
            elif tail == 'operations/lookup':
                fields(body, ['key']); result = book.lookup_operation(tenant, body['key'])
                if principal['role'] == 'agent' and result['customer'] != principal['customer']:
                    raise Error('customer_forbidden', 403)
            elif tail.startswith("operations/") and tail.count("/") == 2:
                _, op, action = tail.split("/"); identifier(op)
                if action == "run": fields(body, []); result = book.run_report(tenant, op)
                elif action == "refund": fields(body, []); result = book.refund(tenant, op)
                elif action in ("uncertain", "release", "complete"):
                    fields(body, [], ["result_hash"])
                    result = book.resolve(tenant, op, {"release": "released", "complete": "committed"}.get(action, action), body.get("result_hash"))
                elif action in ("reconcile-complete", "reconcile-release"):
                    self._authorize(tenant, owner_only=True)
                    fields(body, ["evidence_reference"])
                    identifier(body["evidence_reference"])
                    from .ledger import digest
                    result = book.resolve(tenant, op, "committed" if action.endswith("complete") else "released", digest(body["evidence_reference"]) if action.endswith("complete") else None, reconciled=True)
                else: raise Error("route_not_found", 404)
            elif tail == "sandbox/fund":
                fields(body, ["customer", "asset", "amount", "reference"])
                with book.db() as db: asset = book.asset(db, tenant, body["asset"])
                if asset["rail"] == "bank": result = SandboxBank(book).fund(tenant, body["customer"], body["asset"], body["amount"], body["reference"])
                elif asset["rail"] == "usdc": result = sandbox_usdc_funding(book, tenant, body["customer"], body["amount"], body["reference"])
                else: raise Error("issuer_requires_attestation_and_mint")
            elif tail == "issuer/configure":
                fields(body, ["name", "symbol", "currency"]); result = issuer.configure(tenant, **body)
            elif tail in ("issuer/attest","issuer/attest-balance"):
                fields(body, ["fiat_minor_units", "reference"])
                state = issuer.state(tenant)
                bank=SandboxBank(book)
                event = bank.balance_event(tenant,state['currency'],body['fiat_minor_units'],body['reference']) if tail.endswith('balance') else bank.deposit_event(tenant,'issuer-reserve',state['currency'],body['fiat_minor_units'],body['reference'])
                result = issuer.attest_balance(tenant,event) if tail.endswith("balance") else issuer.attest(tenant, event)
            elif tail in ("issuer/mint", "issuer/redeem"):
                fields(body, ["customer", "amount", "reference"],[] if tail.endswith("mint") else ["source"])
                if tail.endswith("mint"):result=issuer.mint(tenant,body["customer"],body["amount"],body["reference"])
                else:result=issuer.request_redemption(tenant,body["customer"],body["amount"],body["reference"],body.get("source","customer"))
            elif tail == "issuer/payout":
                fields(body, ["redemption", "payment_reference"])
                state = issuer.state(tenant)
                row = next((x for x in state["redemptions"] if x["id"] == body["redemption"]), None)
                if not row: raise Error("redemption_not_found", 404)
                event = SandboxBank(book).event(tenant, "redemption:" + row["id"], state["currency"], row["amount"] // 10_000, body["payment_reference"], "debit")
                result = issuer.confirm_redemption(tenant, row["id"], event)
            elif tail == "payouts":
                fields(body, ["asset", "amount", "recipient", "key"], ["source"])
                result = Payouts(book).request(tenant, **body)
            elif tail.startswith("ops/") and tail.count("/")==2:
                fields(body,[]);_,intent,action=tail.split("/");identifier(intent)
                if action not in ("process","interrupt","reconcile","cancel"):raise Error("route_not_found",404)
                operations=Operations(book);operations.get(tenant,intent)
                from .fixtures import SandboxProvider
                def lost_reply():raise TimeoutError("Synthetic reply interrupted after acceptance")
                provider=SandboxProvider(book.path.with_name(book.path.name+".sandbox-provider.sqlite3"),
                    after_accept=lost_reply if action=="interrupt" else None)
                if action in ("process","interrupt"):result=operations.process_one(tenant,provider,intent=intent)
                elif action=="reconcile":result=operations.reconcile(tenant,intent,provider)
                elif action=="cancel":result=operations.cancel_unknown(tenant,intent,provider)
                else:raise Error("route_not_found",404)
            elif tail=='readiness/recovery-drill':
                fields(body,[])
                try:
                    import cryptography
                    from .backup import recovery_drill
                except ImportError:raise Error("optional_api_tools_not_configured",423) from None
                result=recovery_drill(book)
            elif tail=='readiness/evidence':
                fields(body,["control","reference","document_hash","expires"])
                result=Readiness(book).record(tenant,**body)
            elif tail.startswith("payouts/") and tail.count("/") == 2 and tail.endswith("/confirm"):
                fields(body, ["payment_reference"])
                result = Payouts(book).sandbox_confirm(tenant, tail.split("/")[1], body["payment_reference"])
            elif tail.startswith("enroll/") and tail.count("/") == 1:
                result = book.prepare_enrollment(tenant, tail.split("/")[1], body)
            else: raise Error("route_not_found", 404)
            self._reply(result)
        except Error as e:
            self._reply({"error": e.code}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._reply({"error": "internal_error"}, 500)


def main():
    parser = argparse.ArgumentParser(description="Hellabit private sandbox console")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state", type=Path, default=ROOT / ".private" / "ledger.sqlite3")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    book = Ledger(args.state)
    if args.demo:
        with book.db() as db: exists = db.execute("SELECT 1 FROM tenants LIMIT 1").fetchone()
        if not exists: book.create_tenant("Example Merchant")
    server = Server(book, args.port)
    print(canonical({"url": f"http://127.0.0.1:{server.server_port}", "mode": "sandbox", "live_enabled": False}), flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
