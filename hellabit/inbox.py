"""A signed reference webhook inbox. No named bank/Circle protocol is implied."""
from __future__ import annotations
import hashlib
import hmac
import json
import re
from .control import audit, ensure_schema
from .ledger import Error, canonical, digest, identifier
from .rpc import _strict_object, _invalid_constant

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_bindings(
 tenant TEXT NOT NULL,rail TEXT NOT NULL,provider TEXT NOT NULL,mode TEXT NOT NULL,
 PRIMARY KEY(tenant,rail));
CREATE TRIGGER IF NOT EXISTS provider_binding_immutable BEFORE UPDATE ON provider_bindings
 BEGIN SELECT RAISE(ABORT,'provider_binding_immutable'); END;
CREATE TRIGGER IF NOT EXISTS provider_binding_no_delete BEFORE DELETE ON provider_bindings
 BEGIN SELECT RAISE(ABORT,'provider_binding_immutable'); END;
CREATE TABLE IF NOT EXISTS provider_inbox(
 tenant TEXT NOT NULL,provider TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,
 proof_hash TEXT NOT NULL,occurred INTEGER NOT NULL,received INTEGER NOT NULL,
 PRIMARY KEY(tenant,provider,id));
CREATE TRIGGER IF NOT EXISTS provider_inbox_no_update BEFORE UPDATE ON provider_inbox
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS provider_inbox_no_delete BEFORE DELETE ON provider_inbox
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

class ProviderInbox:
    def __init__(self, book, provider, secret):
        identifier(provider)
        if not isinstance(secret, bytes) or len(secret) < 32: raise Error("invalid_webhook_key", 400)
        self.book, self.provider, self._secret = book, provider, secret
        ensure_schema(book, "provider-inbox", SCHEMA)
        from .operations import Operations
        Operations(book)

    def bind(self,tenant,rail):
        if rail not in ("bank","usdc"):raise Error("invalid_provider_rail",400)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            old=db.execute("SELECT provider FROM provider_bindings WHERE tenant=? AND rail=?",(tenant,rail)).fetchone()
            if old and old["provider"]!=self.provider:raise Error("provider_binding_immutable",409)
            if not old:
                db.execute("INSERT INTO provider_bindings VALUES(?,?,?,'sandbox')",(tenant,rail,self.provider))
                audit(self.book,db,tenant,"provider-bound",self.provider,{"rail":rail,"mode":"sandbox"})
        return {"bound":True,"mode":"sandbox","live_payments_enabled":False}

    def signature(self,secret,timestamp,raw):
        message=b"hellabit.provider-event.v1\x00"+self.provider.encode()+b"\x00"+str(timestamp).encode()+b"."+raw
        return hmac.new(secret,message,hashlib.sha256).hexdigest()

    def receive(self, tenant, raw, timestamp, signature):
        if not isinstance(raw, bytes) or len(raw) > 8192: raise Error("invalid_webhook_body", 400)
        if not isinstance(timestamp,str) or not re.fullmatch(r"[0-9]{1,12}",timestamp):
            raise Error("invalid_webhook_timestamp", 403)
        if abs(int(timestamp) - self.book.clock()) > 300: raise Error("webhook_timestamp_expired", 403)
        if not isinstance(signature,str) or not re.fullmatch(r"[0-9a-f]{64}",signature):
            raise Error("invalid_webhook_signature", 403)
        if not hmac.compare_digest(self.signature(self._secret,timestamp,raw),signature):
            raise Error("invalid_webhook_signature", 403)
        try: body=json.loads(raw,object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
        except (ValueError,UnicodeError): raise Error("invalid_webhook_json",400) from None
        if not isinstance(body,dict) or set(body)!={"id","tenant","occurred","receipt"}:
            raise Error("invalid_webhook_shape",400)
        identifier(body["id"])
        if body["tenant"]!=tenant: raise Error("webhook_workspace_mismatch",403)
        if type(body["occurred"]) is not int or not 0 <= body["occurred"] <= self.book.clock()+300:
            raise Error("invalid_webhook_time",400)
        receipt=body["receipt"]
        if not isinstance(receipt,dict) or receipt.get("mode")!="sandbox":
            raise Error("live_webhook_adapter_not_enrolled",423)
        proof=digest(body)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            old=db.execute("SELECT proof_hash FROM provider_inbox WHERE tenant=? AND provider=? AND id=?",
                           (tenant,self.provider,body["id"])).fetchone()
            if old:
                if old["proof_hash"]!=proof: raise Error("webhook_identity_conflict",409)
                return {"event":body["id"],"replayed":True,"financial_postings":0}
            db.execute("INSERT INTO provider_inbox VALUES(?,?,?,?,?,?,?)",
                       (tenant,self.provider,body["id"],canonical(body),proof,body["occurred"],self.book.clock()))
            audit(self.book,db,tenant,"provider-event-authenticated",body["id"],{"provider":self.provider,"proof_hash":proof,"mode":"sandbox"})
        return {"event":body["id"],"replayed":False,"financial_postings":0}

    def apply(self, tenant, event_id):
        identifier(event_id)
        with self.book.db() as db:
            row=db.execute("SELECT body FROM provider_inbox WHERE tenant=? AND provider=? AND id=?",
                           (tenant,self.provider,event_id)).fetchone()
            if not row: raise Error("provider_event_not_found",404)
            body=json.loads(row["body"]);receipt=body["receipt"]
            intent=db.execute("SELECT * FROM execution_intents WHERE tenant=? AND external_key=?",
                              (tenant,receipt.get("key"))).fetchone()
            if not intent: return {"event":event_id,"state":"unmatched","financial_postings":0}
            if intent["state"] in ("confirmed","failed") and receipt.get("status")=="pending":
                return {"event":event_id,"state":"ignored-after-terminal","financial_postings":0}
        from .operations import Operations
        return Operations(self.book).finish(tenant,intent["id"],receipt,source="verified-inbox",
                                            verified_event=(self.provider,event_id))
