"""Optional ASGI API. Owner bootstrap and synthetic funding are never served here."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit
from . import __version__
from .ledger import Error,Ledger,canonical,identifier
from .identity import Identity
from .rpc import _strict_object,_invalid_constant
BODY_TIMEOUT=5

class Api:
    def __init__(self, book, identity, *, allowed_hosts):
        if (not isinstance(allowed_hosts,(list,tuple,set,frozenset)) or not 1<=len(allowed_hosts)<=16 or
                any(not isinstance(h,str) or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\])(?::[0-9]{1,5})?",h) for h in allowed_hosts)):
            raise Error("invalid_api_host_binding",400)
        for host in allowed_hosts:
            try:port=urlsplit("https://"+host).port
            except ValueError:raise Error("invalid_api_host_binding",400) from None
            if port is not None and port<1:raise Error("invalid_api_host_binding",400)
        self.book,self.identity,self.allowed_hosts=book,identity,frozenset(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"]=="lifespan":
            while True:
                message=await receive()
                if message["type"]=="lifespan.startup":await send({"type":"lifespan.startup.complete"})
                elif message["type"]=="lifespan.shutdown":
                    await send({"type":"lifespan.shutdown.complete"});return
        if scope["type"]!="http":return
        status=200
        try:
            pairs=scope.get("headers",[])
            headers={}
            for key,value in pairs:
                key=key.lower()
                if key in headers:raise Error("duplicate_http_header",400)
                headers[key]=value.decode("latin1")
            if headers.get(b"host") not in self.allowed_hosts:raise Error("api_host_forbidden",403)
            if b"origin" in headers or b"cookie" in headers:raise Error("server_to_server_api_required",403)
            path=scope.get("path","")
            raw=scope.get("raw_path",path.encode())
            if scope.get("query_string") or b"%" in raw or "//" in path or len(raw)>1024:
                raise Error("exact_route_required",400)
            method=scope.get("method")
            if method!="POST" and (headers.get(b"content-length","0")!="0" or b"transfer-encoding" in headers):
                raise Error("unexpected_request_body",400)
            if path=="/api/health" and method=="GET":
                result={"service":"hellabit-gateway","version":__version__,"mode":"deployment-foundation",
                        "live_payments_enabled":False,"check":"liveness"}
            else:
                auth=headers.get(b"authorization","")
                if not auth.startswith("Bearer "):raise Error("unauthorized",401)
                principal=await asyncio.to_thread(self.identity.authenticate,auth[7:])
                if method not in ("GET","POST"):raise Error("method_not_allowed",405)
                if method=="POST" and principal["role"] in ("read-only","operator"):
                    raise Error("principal_write_forbidden",403)
                body={}
                if method=="POST":
                    if headers.get(b"content-type","").split(";")[0].strip()!="application/json":
                        raise Error("json_required",415)
                    length=headers.get(b"content-length")
                    if length is None or not re.fullmatch(r"[0-9]{1,5}",length):raise Error("content_length_required",411)
                    if int(length)>8192:raise Error("body_too_large",413)
                    chunks=bytearray()
                    deadline=asyncio.get_running_loop().time()+BODY_TIMEOUT
                    while True:
                        remaining=deadline-asyncio.get_running_loop().time()
                        if remaining<=0:raise asyncio.TimeoutError()
                        message=await asyncio.wait_for(receive(),timeout=remaining)
                        if message["type"]=="http.disconnect":raise Error("client_disconnected",400)
                        if message["type"]!="http.request":raise Error("invalid_http_body",400)
                        chunks.extend(message.get("body",b""))
                        if len(chunks)>8192:raise Error("body_too_large",413)
                        if not message.get("more_body",False):break
                    if len(chunks)!=int(length):raise Error("incomplete_body",400)
                    try:body=json.loads(chunks,object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                    except (ValueError,UnicodeError,RecursionError):raise Error("invalid_json",400) from None
                    if not isinstance(body,dict):raise Error("json_object_required",400)
                result=await asyncio.to_thread(self.identity.dispatch,principal,self.route,method,path,body)
        except Error as error:
            status,result=error.status,{"error":error.code}
        except asyncio.TimeoutError:
            status,result=408,{"error":"request_body_timeout"}
        except Exception as error:
            correlation=secrets.token_hex(12)
            # Never include the exception message, URL, token, claims or body.
            logging.getLogger("hellabit.api").error(canonical({"event":"request-failed",
                "correlation_id":correlation,"exception_type":type(error).__name__}))
            status,result=500,{"error":"internal_error","correlation_id":correlation}
        encoded=canonical(result).encode()
        headers=[(b"content-type",b"application/json; charset=utf-8"),(b"content-length",str(len(encoded)).encode()),
                 (b"cache-control",b"no-store"),(b"x-content-type-options",b"nosniff"),
                 (b"content-security-policy",b"default-src 'none'; frame-ancestors 'none'"),
                 (b"referrer-policy",b"no-referrer")]
        if status>=400:headers.append((b"connection",b"close"))
        if status==429:headers.append((b"retry-after",b"60"))
        await send({"type":"http.response.start","status":status,"headers":headers})
        await send({"type":"http.response.body","body":encoded})

    def route(self, principal, method, path, body):
        prefix="/api/v1/workspaces/"+principal["tenant"]
        if not (path==prefix or path.startswith(prefix+"/")):raise Error("workspace_forbidden",403)
        tail=path[len(prefix):].lstrip("/")
        book,tenant=self.book,principal["tenant"]
        if method=="GET" and tail=="ready":
            with book.db() as db:
                if db.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone() is None:
                    raise Error("database_not_ready",503)
                quarantined=bool(db.execute("SELECT 1 FROM settings WHERE key='recovery_quarantine'").fetchone())
            if quarantined:raise Error("restored_database_quarantined",503)
            return {"database_ready":True,"live_payments_enabled":False,"scope":"database-access-and-recovery-gate"}
        def fields(required,optional=()):
            if set(body)-set(required)-set(optional) or any(k not in body for k in required):
                raise Error("invalid_fields",400)
        if method=="GET" and tail=="":
            if principal["role"]=="agent":return book.agent_state(tenant,principal["customer"])
            return book.snapshot(tenant)
        if principal["role"]=="operator":
            if method=="GET" and tail=="ops":
                from .operations import Operations
                return Operations(book).list(tenant)
            if method=="GET" and tail=="readiness":
                from .readiness import Readiness
                return Readiness(book).report(tenant)
            raise Error("operator_scope_forbidden",403)
        if method=="GET" and tail.startswith(("operations/","deliveries/")) and tail.count("/")==1:
            id_=identifier(tail.split("/")[1]);operation=book.operation(tenant,id_)
            if principal["role"]=="agent" and operation["customer"]!=principal["customer"]:
                raise Error("customer_forbidden",403)
            return book.delivery(tenant,id_) if tail.startswith("deliveries/") else operation
        if method=="POST" and tail=="quotes":
            if principal["role"]!="merchant":raise Error("merchant_scope_required",403)
            fields(("asset","amount","fee"),("resource","input"))
            return book.create_quote(tenant,body["asset"],body["amount"],body["fee"],body.get("resource","report"),body.get("input"))
        if method=="POST" and tail=="operations":
            fields(("customer","quote","key"))
            if principal["role"]=="agent" and body["customer"]!=principal["customer"]:
                raise Error("customer_forbidden",403)
            return book.reserve(tenant,body["customer"],body["quote"],body["key"],actor=principal)
        if method=="POST" and tail=="operations/lookup":
            fields(("key",));operation=book.lookup_operation(tenant,body["key"])
            if principal["role"]=="agent" and operation["customer"]!=principal["customer"]:
                raise Error("customer_forbidden",403)
            return operation
        if method=="POST" and tail.startswith("operations/") and tail.endswith("/run") and tail.count("/")==2:
            fields(());id_=identifier(tail.split("/")[1]);operation=book.operation(tenant,id_)
            if principal["role"]=="agent" and operation["customer"]!=principal["customer"]:
                raise Error("customer_forbidden",403)
            return book.run_report(tenant,id_,actor=principal)
        if method=="POST" and tail.startswith("operations/") and tail.endswith("/refund") and tail.count("/")==2:
            if principal["role"]!="merchant":raise Error("merchant_scope_required",403)
            fields(());return book.refund(tenant,identifier(tail.split("/")[1]))
        raise Error("api_capability_not_exposed",403)

def _read_trust_file(path, maximum, code, *, private=False):
    path=Path(os.path.abspath(path))
    uid=os.getuid()
    if len(path.parts)>2 and path.parts[1] in ('var','tmp','etc'):
        alias=Path('/')/path.parts[1]
        info=alias.lstat()
        if stat.S_ISLNK(info.st_mode) and info.st_uid==0:
            path=alias.resolve(strict=True).joinpath(*path.parts[2:])
    flags=os.O_RDONLY|os.O_NOFOLLOW
    directory=os.open('/',flags|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,flags|os.O_DIRECTORY,dir_fd=directory)
            os.close(directory)
            directory=child
            info=os.fstat(directory)
            sticky_root=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
            if info.st_uid not in (0,uid) or (info.st_mode&0o022 and not sticky_root):
                raise Error(code,400)
        fd=os.open(path.name,flags|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as file:
            info=os.fstat(file.fileno())
            mode=stat.S_IMODE(info.st_mode)
            if (not stat.S_ISREG(info.st_mode) or info.st_size>maximum or
                (private and (info.st_uid!=uid or mode!=0o600)) or
                (not private and (info.st_uid not in (0,uid) or mode&0o022))):
                raise Error(code,400)
            data=file.read(maximum+1)
            if len(data)>maximum:raise Error(code,400)
            return data
    except OSError:
        raise Error(code,400) from None
    finally:
        os.close(directory)


def load_config(config_path):
    config_path=Path(config_path)
    raw=_read_trust_file(config_path,32768,'owner_api_configuration_required',private=True)
    config=json.loads(raw,object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
    required={"state","issuer","audience","public_keys","allowed_hosts"}
    if not isinstance(config,dict) or not required<=set(config) or set(config)-required-{"bind","port","tls_cert","tls_key"}:
        raise Error("invalid_api_configuration",400)
    if not isinstance(config["public_keys"],dict) or not 1<=len(config["public_keys"])<=8:
        raise Error("invalid_identity_keys",400)
    if type(config.get("port",8080)) is not int or not 0<=config.get("port",8080)<=65535:
        raise Error("invalid_api_port",400)
    if bool(config.get("tls_cert"))!=bool(config.get("tls_key")):
        raise Error("api_tls_pair_required",400)
    def path(value):
        if not isinstance(value,str) or not value:raise Error("invalid_api_configuration",400)
        p=Path(value)
        return p if p.is_absolute() else config_path.absolute().parent/p
    config["state"]=path(config["state"])
    keys={}
    for kid,value in config["public_keys"].items():
        keys[kid]=_read_trust_file(path(value),16384,'invalid_identity_public_key')
    config["public_keys"]=keys
    for name in ("tls_cert","tls_key"):
        if name in config:config[name]=str(path(config[name]))
    return config

def main():
    parser=argparse.ArgumentParser(description="Hellabit authenticated API foundation")
    parser.add_argument("--config",type=Path,required=True);args=parser.parse_args()
    try:config=load_config(args.config)
    except (Error,ValueError,TypeError,OSError):raise SystemExit("API configuration is invalid; no credentials were logged.") from None
    book=Ledger(Path(config["state"]))
    identity=Identity(book,issuer=config["issuer"],audience=config["audience"],
                      public_keys=config["public_keys"])
    host=config.get("bind","127.0.0.1")
    if host not in ("127.0.0.1","::1") and not (config.get("tls_cert") and config.get("tls_key")):
        raise SystemExit("An external bind requires enrolled TLS certificate and private key.")
    if config.get("tls_key"):
        key=Path(config["tls_key"])
        if key.is_symlink() or not key.is_file() or key.stat().st_uid!=os.getuid() or stat.S_IMODE(key.stat().st_mode)!=0o600:
            raise SystemExit("TLS key must be an owner-only regular file.")
    import uvicorn
    uvicorn.run(Api(book,identity,allowed_hosts=config["allowed_hosts"]),host=host,port=config.get("port",8080),
                ssl_certfile=config.get("tls_cert"),ssl_keyfile=config.get("tls_key"),
                proxy_headers=False,access_log=False,server_header=False,log_config=None,
                limit_concurrency=100,timeout_keep_alive=5,backlog=128)

if __name__=="__main__":main()
