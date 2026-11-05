from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

MAX_AMOUNT = 10**12
MAX_BALANCE = 10**15
USDC_TEST = "usdc:84532:0x036cbd53842c5426634e7929541ec2318f3dcf7e"


class Error(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require_safe_sqlite(version=None):
    version = tuple(version or sqlite3.sqlite_version_info)
    patched = version >= (3, 51, 3) or ((3, 44, 6) <= version < (3, 45, 0)) or ((3, 50, 7) <= version < (3, 51, 0))
    if not patched:
        raise Error('sqlite_runtime_requires_wal_fix', 500)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value):
        raise Error("invalid_identifier", 400)
    return value


def units(value, *, zero=False):
    if type(value) is not int or value < (0 if zero else 1) or value > MAX_AMOUNT:
        raise Error("invalid_integer_units", 400)
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,country TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,created INTEGER NOT NULL,bootstrap_revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS customers(tenant TEXT NOT NULL,id TEXT NOT NULL,
 PRIMARY KEY(tenant,id),FOREIGN KEY(tenant) REFERENCES tenants(id));
CREATE TABLE IF NOT EXISTS assets(tenant TEXT NOT NULL,id TEXT NOT NULL,symbol TEXT NOT NULL,
 decimals INTEGER NOT NULL,rail TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS journals(seq INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,
 kind TEXT NOT NULL,reference TEXT NOT NULL,asset TEXT NOT NULL,body TEXT NOT NULL,
 previous TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,created INTEGER NOT NULL,
 UNIQUE(tenant,kind,reference));
CREATE TABLE IF NOT EXISTS postings(journal INTEGER NOT NULL,account TEXT NOT NULL,delta INTEGER NOT NULL CHECK(delta<>0),
 FOREIGN KEY(journal) REFERENCES journals(seq));
CREATE INDEX IF NOT EXISTS posting_account ON postings(account,journal);
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS posting_no_update BEFORE UPDATE ON postings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS posting_no_delete BEFORE DELETE ON postings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TABLE IF NOT EXISTS quotes(tenant TEXT NOT NULL,id TEXT NOT NULL,asset TEXT NOT NULL,
 resource TEXT NOT NULL,amount INTEGER NOT NULL,fee INTEGER NOT NULL,expires INTEGER NOT NULL,
 work_input TEXT,input_hash TEXT,
 PRIMARY KEY(tenant,id));
CREATE TRIGGER IF NOT EXISTS quote_no_update BEFORE UPDATE ON quotes BEGIN SELECT RAISE(ABORT,'price_is_immutable'); END;
CREATE TRIGGER IF NOT EXISTS quote_no_delete BEFORE DELETE ON quotes BEGIN SELECT RAISE(ABORT,'price_is_immutable'); END;
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,customer TEXT NOT NULL,
 asset TEXT NOT NULL,quote TEXT NOT NULL,amount INTEGER NOT NULL,fee INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','uncertain','committed','released')),
 request_hash TEXT NOT NULL,key_hash TEXT NOT NULL,result_hash TEXT,created INTEGER NOT NULL,
 UNIQUE(tenant,key_hash));
CREATE TRIGGER IF NOT EXISTS operation_terms_immutable BEFORE UPDATE OF tenant,customer,asset,quote,amount,fee,request_hash,key_hash ON operations
 BEGIN SELECT RAISE(ABORT,'operation_terms_are_immutable'); END;
CREATE TABLE IF NOT EXISTS settlements(tenant TEXT NOT NULL,rail TEXT NOT NULL,reference TEXT NOT NULL,
 request_hash TEXT NOT NULL,journal INTEGER NOT NULL,PRIMARY KEY(tenant,rail,reference));
CREATE TABLE IF NOT EXISTS issuer(tenant TEXT PRIMARY KEY,name TEXT NOT NULL,symbol TEXT NOT NULL,
 currency TEXT NOT NULL,reserve INTEGER NOT NULL DEFAULT 0,valid_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reserve_proofs(tenant TEXT NOT NULL,reference TEXT NOT NULL,request_hash TEXT NOT NULL,
 units INTEGER NOT NULL,PRIMARY KEY(tenant,reference));
CREATE TABLE IF NOT EXISTS rail_events(tenant TEXT NOT NULL,rail TEXT NOT NULL,reference TEXT NOT NULL,
 purpose TEXT NOT NULL,request_hash TEXT NOT NULL,PRIMARY KEY(tenant,rail,reference));
CREATE TABLE IF NOT EXISTS redemptions(tenant TEXT NOT NULL,id TEXT NOT NULL,customer TEXT NOT NULL,
 amount INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','paid')),payment_reference TEXT,
 request_hash TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS issuer_requests(tenant TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,
 request_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments(tenant TEXT NOT NULL,rail TEXT NOT NULL,body TEXT NOT NULL,
 updated INTEGER NOT NULL,PRIMARY KEY(tenant,rail));
CREATE TABLE IF NOT EXISTS deliveries(tenant TEXT NOT NULL,operation TEXT NOT NULL,body TEXT NOT NULL,
 result_hash TEXT NOT NULL,PRIMARY KEY(tenant,operation));
CREATE TABLE IF NOT EXISTS payouts(tenant TEXT NOT NULL,id TEXT PRIMARY KEY,asset TEXT NOT NULL,
 amount INTEGER NOT NULL,source TEXT NOT NULL,recipient TEXT NOT NULL,state TEXT NOT NULL,
 key_hash TEXT NOT NULL,request_hash TEXT NOT NULL,payment_reference TEXT,UNIQUE(tenant,key_hash));
CREATE TABLE IF NOT EXISTS agent_keys(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,subject TEXT NOT NULL,
 key_hash TEXT NOT NULL UNIQUE,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS merchant_keys(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,
 key_hash TEXT NOT NULL UNIQUE,revoked INTEGER NOT NULL DEFAULT 0);
"""


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Ledger:
    def __init__(self, path, clock=None):
        require_safe_sqlite()
        raw = Path(path).absolute()
        if raw.is_symlink() or (raw.exists() and not raw.is_file()):
            raise Error("unsafe_database_path", 400)
        self.path = raw.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock = clock or (lambda: int(time.time()))
        old = os.umask(0o077)
        try:
            with self.db() as db:
                db.executescript(SCHEMA)
                if 'bootstrap_revoked' not in {row[1] for row in db.execute('PRAGMA table_info(tenants)')}:
                    db.execute('ALTER TABLE tenants ADD COLUMN bootstrap_revoked INTEGER NOT NULL DEFAULT 0')
                columns = {row[1] for row in db.execute('PRAGMA table_info(quotes)')}
                for name in ['work_input', 'input_hash']:
                    if name not in columns:
                        db.execute('ALTER TABLE quotes ADD COLUMN ' + name + ' TEXT')
                db.execute("INSERT OR IGNORE INTO settings VALUES('sandbox_rail_key',?)", (secrets.token_hex(32),))
                db.execute("INSERT OR IGNORE INTO settings VALUES('schema_version','1')")
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=Connection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        db = self.db()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def tenant(self, db, tenant):
        identifier(tenant)
        row = db.execute("SELECT id,name,country,created FROM tenants WHERE id=?", (tenant,)).fetchone()
        if not row:
            raise Error("workspace_not_found", 404)
        return dict(row)

    def assert_payment_writes(self, db):
        """A restored snapshot cannot authorize new financial effects."""
        if db.execute("SELECT 1 FROM settings WHERE key='recovery_quarantine'").fetchone():
            raise Error("restored_database_quarantined", 423)

    def create_tenant(self, name, country="CA"):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .&'-]{1,59}", name):
            raise Error("invalid_workspace_name", 400)
        if country not in ("CA", "US"):
            raise Error("unsupported_onboarding_country", 400)
        tenant, token = "hb_" + secrets.token_hex(8), "hb_test_" + secrets.token_hex(24)
        with self.transaction() as db:
            db.execute("INSERT INTO tenants(id,name,country,token_hash,created) VALUES(?,?,?,?,?)", (tenant, name, country, hashlib.sha256(token.encode()).hexdigest(), self.clock()))
            db.execute("INSERT INTO customers VALUES(?, 'agent-01')", (tenant,))
            for asset, symbol, decimals, rail in [("CAD", "CAD", 2, "bank"), ("USD", "USD", 2, "bank"), (USDC_TEST, "USDC", 6, "usdc"), ("issued:" + tenant, "HUSD", 6, "issuer")]:
                db.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (tenant, asset, symbol, decimals, rail))
            db.execute("INSERT INTO issuer(tenant,name,symbol,currency) VALUES(?,?,?,?)", (tenant, name + " Dollar", "HUSD", "USD"))
        return {"tenant": tenant, "api_key": token, "mode": "sandbox"}

    def authenticate(self, token):
        return self.principal(token)["tenant"]

    def principal(self, token):
        if not isinstance(token, str) or len(token) > 200:
            raise Error("unauthorized", 401)
        with self.db() as db:
            row = db.execute("SELECT id FROM tenants WHERE token_hash=? AND bootstrap_revoked=0", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row:
                return {"tenant": row[0], "role": "merchant", "customer": None}
            row = db.execute("SELECT tenant FROM merchant_keys WHERE key_hash=? AND revoked=0", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row:
                return {"tenant": row[0], "role": "merchant", "customer": None}
            row = db.execute("SELECT tenant,subject FROM agent_keys WHERE key_hash=? AND revoked=0", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row:
                return {"tenant": row[0], "role": "agent", "customer": row[1]}
            raise Error("unauthorized", 401)

    def issue_merchant_key(self, tenant):
        key, id_ = "hb_test_" + secrets.token_hex(24), "key_" + secrets.token_hex(12)
        with self.transaction() as db:
            self.tenant(db, tenant)
            db.execute("INSERT INTO merchant_keys VALUES(?,?,?,0)", (id_, tenant, hashlib.sha256(key.encode()).hexdigest()))
        return {"id": id_, "api_key": key, "scope": ["merchant-application"], "owner_authority": False}

    def issue_agent_key(self, tenant, customer):
        key, id_ = "hb_agent_test_" + secrets.token_hex(24), "key_" + secrets.token_hex(12)
        with self.transaction() as db:
            self.tenant(db, tenant); self.customer(db, tenant, customer)
            db.execute("INSERT INTO agent_keys VALUES(?,?,?,?,0)", (id_, tenant, customer, hashlib.sha256(key.encode()).hexdigest()))
        return {"id": id_, "api_key": key, "customer": customer, "scope": ["own-balance", "reserve", "own-service-run", "own-delivery"]}

    def keys(self, tenant):
        """Metadata only. Secrets and their hashes never leave this boundary."""
        with self.db() as db:
            self.tenant(db, tenant)
            initial = db.execute('SELECT bootstrap_revoked FROM tenants WHERE id=?', (tenant,)).fetchone()[0]
            result = [{"id": "bootstrap", "role": "merchant", "customer": None, "revoked": bool(initial)}]
            for role, table in [('merchant', 'merchant_keys'), ('agent', 'agent_keys')]:
                columns = 'id,revoked,subject' if role == 'agent' else 'id,revoked'
                for row in db.execute(f'SELECT {columns} FROM {table} WHERE tenant=? ORDER BY rowid DESC', (tenant,)):
                    result.append({"id": row['id'], "role": role, "customer": row['subject'] if role == 'agent' else None, "revoked": bool(row['revoked'])})
            return {"keys": result}

    def revoke_merchant_key(self, tenant, key_id):
        identifier(key_id)
        with self.transaction() as db:
            self.tenant(db, tenant)
            if key_id == 'bootstrap':
                db.execute('UPDATE tenants SET bootstrap_revoked=1 WHERE id=?', (tenant,))
            else:
                if not db.execute('SELECT 1 FROM merchant_keys WHERE tenant=? AND id=?', (tenant, key_id)).fetchone():
                    raise Error('key_not_found', 404)
                db.execute('UPDATE merchant_keys SET revoked=1 WHERE tenant=? AND id=?', (tenant, key_id))
        return {"revoked": True}

    def revoke_agent_key(self, tenant, key_id):
        identifier(key_id)
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM agent_keys WHERE tenant=? AND id=?", (tenant, key_id)).fetchone():
                raise Error("key_not_found", 404)
            db.execute("UPDATE agent_keys SET revoked=1 WHERE tenant=? AND id=?", (tenant, key_id))
        return {"revoked": True}

    def agent_state(self, tenant, customer):
        with self.db() as db:
            info = self.tenant(db, tenant); self.customer(db, tenant, customer)
            assets = []
            for row in db.execute("SELECT id,symbol,decimals FROM assets WHERE tenant=?", (tenant,)):
                a = dict(row); a["balance"] = self._balance(db, tenant, a["id"], "customer:" + customer)
                a["held"] = self._held(db, tenant, a["id"], customer); a["available"] = a["balance"] - a["held"]; assets.append(a)
            return {"workspace": {"id": tenant, "name": info["name"]}, "customer": customer, "assets": assets, "mode": "sandbox"}

    def add_customer(self, tenant, customer):
        identifier(customer)
        with self.transaction() as db:
            self.tenant(db, tenant)
            db.execute("INSERT OR IGNORE INTO customers VALUES(?,?)", (tenant, customer))
        return {"customer": customer}

    def customer(self, db, tenant, customer):
        identifier(customer)
        if not db.execute("SELECT 1 FROM customers WHERE tenant=? AND id=?", (tenant, customer)).fetchone():
            raise Error("customer_not_found", 404)

    def asset(self, db, tenant, asset):
        if not isinstance(asset, str) or len(asset) > 128:
            raise Error("invalid_asset_identifier", 400)
        row = db.execute("SELECT * FROM assets WHERE tenant=? AND id=?", (tenant, asset)).fetchone()
        if not row:
            raise Error("asset_not_supported", 400)
        return dict(row)

    def _balance(self, db, tenant, asset, account):
        return db.execute("SELECT COALESCE(SUM(p.delta),0) FROM postings p JOIN journals j ON p.journal=j.seq WHERE j.tenant=? AND j.asset=? AND p.account=?", (tenant, asset, account)).fetchone()[0]

    def _held(self, db, tenant, asset, customer):
        return db.execute("SELECT COALESCE(SUM(amount),0) FROM operations WHERE tenant=? AND asset=? AND customer=? AND state IN ('reserved','uncertain')", (tenant, asset, customer)).fetchone()[0]

    def _post(self, db, tenant, kind, reference, asset, entries, metadata=None):
        self.assert_payment_writes(db)
        entries = sorted([[a, n] for a, n in entries if n])
        if not entries or sum(n for _, n in entries) or any(type(n) is not int or abs(n) > MAX_BALANCE for _, n in entries):
            raise Error("unbalanced_posting", 500)
        if len({a for a, _ in entries}) != len(entries):
            raise Error("duplicate_posting_account", 500)
        body = {"tenant": tenant, "kind": kind, "reference": reference, "asset": asset, "entries": entries, "metadata": metadata or {}, "created": self.clock()}
        previous = db.execute("SELECT digest FROM journals WHERE tenant=? ORDER BY seq DESC LIMIT 1", (tenant,)).fetchone()
        previous = previous[0] if previous else "0" * 64
        hash_ = digest({"previous": previous, "body": body})
        cur = db.execute("INSERT INTO journals(tenant,kind,reference,asset,body,previous,digest,created) VALUES(?,?,?,?,?,?,?,?)", (tenant, kind, reference, asset, canonical(body), previous, hash_, body["created"]))
        db.executemany("INSERT INTO postings VALUES(?,?,?)", [(cur.lastrowid, a, n) for a, n in entries])
        return cur.lastrowid

    def credit_settlement(self, tenant, customer, asset, amount, rail, reference, proof):
        """Called only by a trusted server-side rail verifier, never HTTP input."""
        units(amount); identifier(reference)
        if rail not in ("bank", "usdc") or not isinstance(proof, str) or not re.fullmatch(r"[0-9a-f]{64}", proof):
            raise Error("invalid_settlement_evidence", 400)
        request = digest([customer, asset, amount, proof])
        with self.transaction() as db:
            self.tenant(db, tenant); self.customer(db, tenant, customer)
            if self.asset(db, tenant, asset)["rail"] != rail:
                raise Error("rail_asset_mismatch", 400)
            old = db.execute("SELECT * FROM settlements WHERE tenant=? AND rail=? AND reference=?", (tenant, rail, reference)).fetchone()
            if old:
                if old["request_hash"] != request:
                    raise Error("settlement_replay_mismatch")
                return {"journal": old["journal"], "replayed": True}
            if self._balance(db, tenant, asset, "customer:" + customer) + amount > MAX_BALANCE:
                raise Error("balance_limit")
            self.consume_rail_event(db, tenant, rail, reference, "funding", proof)
            seq = self._post(db, tenant, "funding", rail + ":" + reference, asset, [("clearing:" + rail, -amount), ("customer:" + customer, amount)], {"proof_hash": proof, "rail": rail})
            db.execute("INSERT INTO settlements VALUES(?,?,?,?,?)", (tenant, rail, reference, request, seq))
            return {"journal": seq, "replayed": False}

    def consume_rail_event(self, db, tenant, rail, reference, purpose, proof_hash):
        if db.execute("SELECT 1 FROM rail_events WHERE tenant=? AND rail=? AND reference=?", (tenant, rail, reference)).fetchone():
            raise Error("rail_event_already_consumed")
        db.execute("INSERT INTO rail_events VALUES(?,?,?,?,?)", (tenant, rail, reference, purpose, proof_hash))

    def create_quote(self, tenant, asset, amount, fee, resource="report", work_input=None):
        units(amount); units(fee, zero=True); identifier(resource)
        from .services import prepare_input
        normalized = prepare_input(resource, work_input)
        input_hash = digest(normalized) if normalized is not None else None
        if fee > amount:
            raise Error("fee_exceeds_price", 400)
        quote = "q_" + secrets.token_hex(12)
        with self.transaction() as db:
            self.tenant(db, tenant); self.asset(db, tenant, asset)
            db.execute("INSERT INTO quotes(tenant,id,asset,resource,amount,fee,expires,work_input,input_hash) VALUES(?,?,?,?,?,?,?,?,?)", (tenant, quote, asset, resource, amount, fee, self.clock() + 900, canonical(normalized) if normalized is not None else None, input_hash))
        return {"quote": quote, "asset": asset, "amount": amount, "fee": fee, "resource": resource, "input_hash": input_hash, "expires_in": 900}

    def reserve(self, tenant, customer, quote, key):
        identifier(key); identifier(quote)
        request = digest([customer, quote]); key_hash = digest(key)
        with self.transaction() as db:
            self.assert_payment_writes(db)
            self.tenant(db, tenant); self.customer(db, tenant, customer)
            old = db.execute("SELECT * FROM operations WHERE tenant=? AND key_hash=?", (tenant, key_hash)).fetchone()
            if old:
                if old["request_hash"] != request:
                    raise Error("idempotency_conflict")
                return {**dict(old), "replayed": True}
            q = db.execute("SELECT * FROM quotes WHERE tenant=? AND id=?", (tenant, quote)).fetchone()
            if not q or q["expires"] <= self.clock():
                raise Error("quote_expired_or_missing")
            available = self._balance(db, tenant, q["asset"], "customer:" + customer) - self._held(db, tenant, q["asset"], customer)
            if available < q["amount"]:
                raise Error("insufficient_available_credits", 402)
            op = "op_" + secrets.token_hex(12)
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (op, tenant, customer, q["asset"], quote, q["amount"], q["fee"], "reserved", request, key_hash, None, self.clock()))
            return self._operation(db, tenant, op)

    def _operation(self, db, tenant, op):
        identifier(op)
        row = db.execute("SELECT * FROM operations WHERE tenant=? AND id=?", (tenant, op)).fetchone()
        if not row:
            raise Error("operation_not_found", 404)
        return dict(row)

    def operation(self, tenant, op):
        with self.db() as db:
            return self._operation(db, tenant, op)

    def resolve(self, tenant, op, outcome, result_hash=None, reconciled=False):
        if outcome not in ("committed", "released", "uncertain"):
            raise Error("invalid_operation_outcome", 400)
        if result_hash is not None and (not isinstance(result_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", result_hash)):
            raise Error("invalid_result_hash", 400)
        with self.transaction() as db:
            row = self._operation(db, tenant, op)
            self.assert_payment_writes(db)
            if row["state"] == outcome:
                if outcome == "committed" and row["result_hash"] != result_hash:
                    raise Error("result_conflict")
                return {**row, "replayed": True}
            if row["state"] in ("committed", "released"):
                raise Error("operation_terminal")
            if row["state"] == "uncertain" and not reconciled:
                raise Error("reconciliation_required")
            if outcome == "committed":
                if not result_hash:
                    raise Error("completion_evidence_required", 400)
                self._post(db, tenant, "charge", op, row["asset"], [("customer:" + row["customer"], -row["amount"]), ("merchant", row["amount"] - row["fee"]), ("platform-fees", row["fee"])], {"result_hash": result_hash})
            db.execute("UPDATE operations SET state=?,result_hash=? WHERE id=?", (outcome, result_hash, op))
            return self._operation(db, tenant, op)

    def refund(self, tenant, op):
        with self.transaction() as db:
            row = self._operation(db, tenant, op)
            if row["state"] != "committed":
                raise Error("only_completed_work_refundable")
            old = db.execute("SELECT seq FROM journals WHERE tenant=? AND kind='refund' AND reference=?", (tenant, op)).fetchone()
            if old:
                return {"journal": old[0], "replayed": True}
            if self._balance(db, tenant, row["asset"], "merchant") < row["amount"] - row["fee"] or self._balance(db, tenant, row["asset"], "platform-fees") < row["fee"]:
                raise Error("refund_requires_funded_reversal")
            seq = self._post(db, tenant, "refund", op, row["asset"], [("customer:" + row["customer"], row["amount"]), ("merchant", -(row["amount"] - row["fee"])), ("platform-fees", -row["fee"])])
            return {"journal": seq, "replayed": False}

    def lookup_operation(self, tenant, key):
        identifier(key)
        with self.db() as db:
            self.tenant(db, tenant)
            row = db.execute('SELECT id FROM operations WHERE tenant=? AND key_hash=?', (tenant, digest(key))).fetchone()
            if not row:
                raise Error('operation_not_found', 404)
            return self._operation(db, tenant, row['id'])

    def run_report(self, tenant, op):
        """Reference paid API: result and debit commit in the same transaction."""
        with self.transaction() as db:
            row = self._operation(db, tenant, op)
            delivery = db.execute("SELECT * FROM deliveries WHERE tenant=? AND operation=?", (tenant, op)).fetchone()
            if delivery:
                return {"operation": op, "result_hash": delivery["result_hash"], "replayed": True}
            if row["state"] != "reserved":
                raise Error("job_requires_reserved_operation")
            quote = db.execute("SELECT resource,work_input,input_hash FROM quotes WHERE tenant=? AND id=?", (tenant, row["quote"])).fetchone()
            resource = quote['resource']
            work_input = json.loads(quote['work_input']) if quote['work_input'] is not None else None
            if (digest(work_input) if work_input is not None else None) != quote['input_hash']:
                raise Error('quoted_input_digest_mismatch')
            from .services import run_service
            result = run_service(resource, work_input)
            body = {"schema": "hellabit.reference-report.v1" if resource == 'report' else 'hellabit.purchased-result.v1', "operation": op, "asset": row["asset"],
                    "charge_units": row["amount"], "fee_units": row["fee"], "mode": "sandbox",
                    "resource": resource, "input_hash": quote['input_hash'], "result": result}
            result_hash = digest(body)
            self._post(db, tenant, "charge", op, row["asset"], [("customer:" + row["customer"], -row["amount"]), ("merchant", row["amount"] - row["fee"]), ("platform-fees", row["fee"])], {"result_hash": result_hash})
            db.execute("UPDATE operations SET state='committed',result_hash=? WHERE id=?", (result_hash, op))
            db.execute("INSERT INTO deliveries VALUES(?,?,?,?)", (tenant, op, canonical(body), result_hash))
            return {"operation": op, "result_hash": result_hash, "replayed": False}

    def delivery(self, tenant, op):
        with self.db() as db:
            self._operation(db, tenant, op)
            if db.execute("SELECT 1 FROM journals WHERE tenant=? AND kind='refund' AND reference=?", (tenant, op)).fetchone():
                raise Error("delivery_access_revoked", 403)
            row = db.execute("SELECT body FROM deliveries WHERE tenant=? AND operation=?", (tenant, op)).fetchone()
            if not row:
                raise Error("delivery_not_found", 404)
            return json.loads(row[0])

    def prepare_enrollment(self, tenant, rail, body):
        allowed = {"bank": {"institution_reference", "account_reference", "currency"},
                   "usdc": {"receiver", "signer_reference", "chain_id"},
                   "issuer": {"legal_operator_reference", "reserve_custodian_reference"}}
        if rail not in allowed or not isinstance(body, dict) or set(body) != allowed[rail]:
            raise Error("invalid_enrollment_fields", 400)
        if rail == "bank":
            identifier(body["institution_reference"]); identifier(body["account_reference"])
            if body["currency"] not in ("CAD", "USD"):
                raise Error("unsupported_bank_currency", 400)
        elif rail == "usdc":
            from .rails import address
            body = {**body, "receiver": address(body["receiver"])}
            identifier(body["signer_reference"])
            if type(body["chain_id"]) is not int or body["chain_id"] != 84532:
                raise Error("only_base_sepolia_preparation_supported", 400)
        else:
            for value in body.values(): identifier(value)
        with self.transaction() as db:
            self.tenant(db, tenant)
            db.execute("INSERT INTO enrollments VALUES(?,?,?,?) ON CONFLICT(tenant,rail) DO UPDATE SET body=excluded.body,updated=excluded.updated", (tenant, rail, canonical(body), self.clock()))
        return {"rail": rail, "status": "prepared", "externally_verified": False, "live_enabled": False}

    def reconcile(self, tenant):
        with self.db() as db:
            self.tenant(db, tenant)
            previous, failures, count = "0" * 64, [], 0
            for row in db.execute("SELECT * FROM journals WHERE tenant=? ORDER BY seq", (tenant,)):
                body = json.loads(row["body"])
                entries = sorted([[p[0], p[1]] for p in db.execute("SELECT account,delta FROM postings WHERE journal=?", (row["seq"],))])
                bound = all(body.get(key) == row[key] for key in ("tenant", "kind", "reference", "asset", "created"))
                if not bound or sum(n for _, n in entries) or entries != body["entries"] or row["previous"] != previous or digest({"previous": previous, "body": body}) != row["digest"]:
                    failures.append(row["seq"])
                previous = row["digest"]; count += 1
            for c in db.execute("SELECT id FROM customers WHERE tenant=?", (tenant,)):
                for a in db.execute("SELECT id FROM assets WHERE tenant=?", (tenant,)):
                    balance = self._balance(db, tenant, a[0], "customer:" + c[0])
                    if balance < self._held(db, tenant, a[0], c[0]) or balance < 0:
                        failures.append("available_balance")
            return {"ok": not failures, "journal_count": count, "head": previous, "failures": failures, "scope": "internal-ledger", "external_settlement_verified": False}

    def snapshot(self, tenant):
        with self.db() as db:
            info = self.tenant(db, tenant)
            assets = [dict(x) for x in db.execute("SELECT * FROM assets WHERE tenant=?", (tenant,))]
            customer = "agent-01"
            for a in assets:
                a["balance"] = self._balance(db, tenant, a["id"], "customer:" + customer)
                a["held"] = self._held(db, tenant, a["id"], customer)
                a["available"] = a["balance"] - a["held"]
                a["merchant"] = self._balance(db, tenant, a["id"], "merchant")
                a["fees"] = self._balance(db, tenant, a["id"], "platform-fees")
                a['payout_held'] = db.execute("SELECT COALESCE(SUM(amount),0) FROM payouts WHERE tenant=? AND asset=? AND source='merchant' AND state='pending'", (tenant, a['id'])).fetchone()[0]
            ops = [dict(x) for x in db.execute("SELECT id,customer,asset,amount,fee,state,result_hash,created FROM operations WHERE tenant=? ORDER BY created DESC,rowid DESC LIMIT 30", (tenant,))]
            for op in ops:
                op['refunded'] = bool(db.execute("SELECT 1 FROM journals WHERE tenant=? AND kind='refund' AND reference=?", (tenant, op['id'])).fetchone())
                op['has_delivery'] = bool(db.execute('SELECT 1 FROM deliveries WHERE tenant=? AND operation=?', (tenant, op['id'])).fetchone()) and not op['refunded']
            events = []
            for x in db.execute("SELECT * FROM journals WHERE tenant=? ORDER BY seq DESC LIMIT 30", (tenant,)):
                # Gross movement is the positive side of the balanced posting set.
                amount = db.execute('SELECT COALESCE(SUM(delta),0) FROM postings WHERE journal=? AND delta>0', (x['seq'],)).fetchone()[0]
                events.append({"seq": x["seq"], "kind": x["kind"], "asset": x["asset"], "amount": amount, "reference": x["reference"], "digest": x["digest"], "created": x["created"]})
            enrollments = [{"rail": x["rail"], "fields": json.loads(x["body"]), "status": "prepared", "externally_verified": False} for x in db.execute("SELECT * FROM enrollments WHERE tenant=?", (tenant,))]
            payouts = [dict(x) for x in db.execute("SELECT id,asset,amount,source,recipient,state,payment_reference FROM payouts WHERE tenant=? ORDER BY rowid DESC LIMIT 20", (tenant,))]
            counts = {x['state']: x['count'] for x in db.execute('SELECT state,COUNT(*) AS count FROM operations WHERE tenant=? GROUP BY state', (tenant,))}
            return {"workspace": info, "mode": "sandbox", "customer": customer, "assets": assets, "operations": ops, "operation_counts": counts, "journal": events, "journal_count": db.execute('SELECT COUNT(*) FROM journals WHERE tenant=?', (tenant,)).fetchone()[0], "observed_at": self.clock(), "enrollments": enrollments, "payouts": payouts, "readiness": {"bank": "institution-enrollment-required", "usdc": "network-and-signer-enrollment-required", "issuer": "legal-issuer-and-reserves-required"}, "live_payments_enabled": False}

    def activity(self, tenant, asset=None):
        """Filter before the bounded page; unresolved work always comes first."""
        with self.db() as db:
            self.tenant(db, tenant)
            if asset is not None:
                self.asset(db, tenant, asset)
            where = 'tenant=?' + (' AND asset=?' if asset is not None else '')
            params = (tenant, asset) if asset is not None else (tenant,)
            ops = [dict(row) for row in db.execute('SELECT id,customer,asset,amount,fee,state,result_hash,created FROM operations WHERE '+where+" ORDER BY CASE state WHEN 'uncertain' THEN 0 WHEN 'reserved' THEN 1 ELSE 2 END,created DESC,rowid DESC LIMIT 30", params)]
            for op in ops:
                op['refunded'] = bool(db.execute("SELECT 1 FROM journals WHERE tenant=? AND kind='refund' AND reference=?", (tenant, op['id'])).fetchone())
                op['has_delivery'] = bool(db.execute('SELECT 1 FROM deliveries WHERE tenant=? AND operation=?', (tenant, op['id'])).fetchone()) and not op['refunded']
                q = db.execute('SELECT resource,input_hash FROM quotes WHERE tenant=? AND id=(SELECT quote FROM operations WHERE id=?)', (tenant, op['id'])).fetchone()
                op['resource'], op['input_hash'] = q['resource'], q['input_hash']
            events = []
            for row in db.execute('SELECT * FROM journals WHERE '+where+' ORDER BY seq DESC LIMIT 30', params):
                event = {k:row[k] for k in ['seq','kind','asset','reference','digest','created']}
                event['amount'] = db.execute('SELECT COALESCE(SUM(delta),0) FROM postings WHERE journal=? AND delta>0', (row['seq'],)).fetchone()[0]
                events.append(event)
            return {'scope':'selected-asset' if asset else 'all-assets', 'asset':asset, 'operations':ops, 'journal':events, 'journal_count':db.execute('SELECT COUNT(*) FROM journals WHERE '+where,params).fetchone()[0], 'operation_count':db.execute('SELECT COUNT(*) FROM operations WHERE '+where,params).fetchone()[0], 'page_limit':30, 'observed_at':self.clock()}
