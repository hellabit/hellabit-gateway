"""Durable payout intent. Network work happens outside accounting transactions."""
from __future__ import annotations
import hmac
import json
import secrets
from .control import audit, ensure_schema
from .ledger import Error, canonical, digest, identifier

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_intents(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,payout TEXT NOT NULL,rail TEXT NOT NULL,
 asset TEXT NOT NULL,amount INTEGER NOT NULL,recipient TEXT NOT NULL,
 external_key TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','submitting','unknown','confirmed','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,lease_hash TEXT,lease_until INTEGER,
 reference TEXT,evidence_hash TEXT,last_error TEXT,created INTEGER NOT NULL,updated INTEGER NOT NULL,
 UNIQUE(tenant,payout),FOREIGN KEY(payout) REFERENCES payouts(id));
CREATE TRIGGER IF NOT EXISTS intent_terms_immutable
 BEFORE UPDATE OF tenant,payout,rail,asset,amount,recipient,external_key,request_hash ON execution_intents
 BEGIN SELECT RAISE(ABORT,'intent_terms_are_immutable'); END;
CREATE TABLE IF NOT EXISTS execution_observations(
 tenant TEXT NOT NULL,intent TEXT NOT NULL,proof_hash TEXT NOT NULL,body TEXT NOT NULL,
 source TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(tenant,intent,proof_hash));
CREATE TRIGGER IF NOT EXISTS observation_no_update BEFORE UPDATE ON execution_observations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS observation_no_delete BEFORE DELETE ON execution_observations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

class Operations:
    def __init__(self, book):
        self.book = book
        ensure_schema(book, "operations", SCHEMA)
        if not getattr(book, "_payout_backfill_complete", False):
            with book.transaction() as db:
                rows = db.execute("""SELECT p.* FROM payouts p LEFT JOIN execution_intents e
                    ON e.tenant=p.tenant AND e.payout=p.id
                    WHERE p.state='pending' AND e.id IS NULL LIMIT 10001""").fetchall()
                if len(rows) > 10000:
                    raise Error("payout_migration_requires_bounded_batch", 423)
                for row in rows:
                    self.enqueue(db, row["tenant"], row["id"], row["asset"], row["amount"], row["recipient"], migrated=True)
            book._payout_backfill_complete = True

    def enqueue(self, db, tenant, payout, asset, amount, recipient, migrated=False):
        rail = self.book.asset(db, tenant, asset)["rail"]
        id_ = "intent_" + secrets.token_hex(12)
        body = {"payout": payout, "rail": rail, "asset": asset, "amount": amount, "recipient": recipient}
        now = self.book.clock()
        state = "unknown" if migrated else "prepared"
        error = "migrated_payout_requires_reconciliation" if migrated else None
        db.execute("""INSERT INTO execution_intents
            (id,tenant,payout,rail,asset,amount,recipient,external_key,request_hash,state,last_error,created,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (id_, tenant, payout, rail, asset, amount, recipient, "hb-" + id_, digest(body), state, error, now, now))
        audit(self.book, db, tenant, "intent-created", id_, {"state": state, "payout": payout})
        return id_

    @staticmethod
    def public(row):
        return {k: v for k, v in dict(row).items() if k not in ("lease_hash",)}

    def get(self, tenant, intent):
        identifier(intent)
        with self.book.db() as db:
            self.book.tenant(db, tenant)
            row = db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?", (tenant, intent)).fetchone()
            if not row: raise Error("intent_not_found", 404)
            return self.public(row)

    def list(self, tenant):
        with self.book.db() as db:
            self.book.tenant(db, tenant)
            rows = db.execute("""SELECT * FROM execution_intents WHERE tenant=?
                ORDER BY CASE state WHEN 'unknown' THEN 0 WHEN 'submitting' THEN 1 WHEN 'prepared' THEN 2 ELSE 3 END,
                created DESC,rowid DESC LIMIT 30""", (tenant,)).fetchall()
            counts = {r["state"]: r["n"] for r in db.execute(
                "SELECT state,COUNT(*) n FROM execution_intents WHERE tenant=? GROUP BY state", (tenant,))}
        return {"intents": [self.public(r) for r in rows], "counts": counts, "mode": "sandbox",
                "live_submission_implemented": False, "observed_at": self.book.clock()}

    def _expire(self, db, tenant):
        for row in db.execute("""SELECT id FROM execution_intents WHERE tenant=?
                AND state='submitting' AND lease_until<=?""", (tenant, self.book.clock())).fetchall():
            db.execute("""UPDATE execution_intents SET state='unknown',lease_hash=NULL,lease_until=NULL,
                last_error='worker_lease_expired',updated=? WHERE id=?""", (self.book.clock(), row["id"]))
            audit(self.book, db, tenant, "submission-unknown", row["id"], {"reason": "worker_lease_expired"})

    def claim(self, tenant, lease_seconds=60, intent=None):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise Error("invalid_worker_lease", 400)
        if intent is not None: identifier(intent)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            self._expire(db, tenant)
            row = db.execute("""SELECT e.* FROM execution_intents e WHERE e.tenant=? AND e.state='prepared'
                AND (? IS NULL OR e.id=?)
                AND NOT EXISTS(SELECT 1 FROM execution_intents uncertain
                  WHERE uncertain.tenant=e.tenant AND uncertain.rail=e.rail
                    AND uncertain.state IN ('unknown','submitting'))
                ORDER BY e.created,e.rowid LIMIT 1""", (tenant,intent,intent)).fetchone()
            if not row: return None
            lease = secrets.token_hex(24)
            now=self.book.clock();until=now+lease_seconds
            db.execute("""UPDATE execution_intents SET state='submitting',attempts=attempts+1,
                lease_hash=?,lease_until=?,updated=? WHERE id=?""",
                (digest(lease), until, now, row["id"]))
            audit(self.book, db, tenant, "submission-claimed", row["id"], {"attempt": row["attempts"] + 1})
            return {**self.public(row), "state": "submitting", "lease": lease,
                    "attempts":row["attempts"]+1,"lease_until":until,"updated":now}

    def _check_claim(self,tenant,intent,lease):
        """Recheck ownership after a network read, before starting a submission."""
        with self.book.transaction() as db:
            self._expire(db,tenant)
            row=db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?",(tenant,intent)).fetchone()
            if (not row or row["state"]!="submitting" or
                    not hmac.compare_digest(row["lease_hash"] or "",digest(lease))):
                return False
            return True

    def _unknown(self, tenant, intent, lease, code):
        with self.book.transaction() as db:
            row = db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?", (tenant, intent)).fetchone()
            if not row or row["state"] != "submitting" or not hmac.compare_digest(row["lease_hash"] or "", digest(lease)):
                raise Error("worker_fenced", 409)
            db.execute("""UPDATE execution_intents SET state='unknown',lease_hash=NULL,lease_until=NULL,
                last_error=?,updated=? WHERE id=?""", (code, self.book.clock(), intent))
            audit(self.book, db, tenant, "submission-unknown", intent, {"reason": code})
        return self.get(tenant, intent)

    @staticmethod
    def _adapter(adapter):
        if getattr(adapter, "mode", None) != "sandbox":
            raise Error("live_connection_not_enrolled", 423)

    def finish(self, tenant, intent, receipt, *, lease=None, source="lookup", verified_event=None):
        required = {"mode", "key", "request_hash", "status", "reference", "amount", "asset", "recipient"}
        if not isinstance(receipt, dict) or set(receipt) != required or receipt["mode"] != "sandbox":
            raise Error("invalid_provider_receipt", 403)
        if receipt["status"] not in ("settled", "rejected", "pending"):
            raise Error("invalid_provider_status", 403)
        identifier(receipt["reference"])
        proof = digest(receipt)
        with self.book.transaction() as db:
            row = db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?", (tenant, intent)).fetchone()
            if not row: raise Error("intent_not_found", 404)
            expected = {"key": row["external_key"], "request_hash": row["request_hash"], "amount": row["amount"],
                        "asset": row["asset"], "recipient": row["recipient"]}
            if any(type(receipt[k]) is not type(v) or receipt[k] != v for k, v in expected.items()):
                raise Error("provider_receipt_binding_mismatch", 403)
            if row["state"] in ("confirmed", "failed"):
                if row["evidence_hash"] != proof: raise Error("settlement_evidence_conflict", 409)
                return {**self.public(row), "replayed": True}
            if lease is not None:
                if row["state"] != "submitting" or not hmac.compare_digest(row["lease_hash"] or "", digest(lease)):
                    raise Error("worker_fenced", 409)
                if row["lease_until"] <= self.book.clock(): raise Error("worker_lease_expired", 409)
            elif source == "verified-inbox":
                if not isinstance(verified_event, tuple) or len(verified_event) != 2:
                    raise Error("verified_provider_event_required", 403)
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"provider_inbox","provider_bindings"}<=tables:
                    raise Error("verified_provider_event_required",403)
                binding=db.execute("SELECT provider FROM provider_bindings WHERE tenant=? AND rail=?",(tenant,row["rail"])).fetchone()
                if not binding or binding["provider"]!=verified_event[0]:
                    raise Error("provider_connection_binding_mismatch",403)
                event = db.execute("SELECT body FROM provider_inbox WHERE tenant=? AND provider=? AND id=?",
                                   (tenant, *verified_event)).fetchone()
                if not event or json.loads(event["body"])["receipt"] != receipt:
                    raise Error("verified_provider_event_required", 403)
            elif row["state"] != "unknown":
                raise Error("reconciliation_not_available", 409)
            db.execute("INSERT OR IGNORE INTO execution_observations VALUES(?,?,?,?,?,?)",
                       (tenant, intent, proof, canonical(receipt), source, self.book.clock()))
            from .payouts import Payouts
            if receipt["status"] == "settled":
                Payouts(self.book)._paid_tx(db, tenant, row["payout"], receipt["reference"], proof)
                state = "confirmed"
            elif receipt["status"] == "rejected":
                Payouts(self.book)._failed_tx(db, tenant, row["payout"], receipt["reference"], proof)
                state = "failed"
            else:
                state = "unknown"
            db.execute("""UPDATE execution_intents SET state=?,reference=?,evidence_hash=?,
                lease_hash=NULL,lease_until=NULL,last_error=?,updated=? WHERE id=?""",
                (state, receipt["reference"], proof, "provider_pending" if state == "unknown" else None,
                 self.book.clock(), intent))
            audit(self.book, db, tenant, "provider-observed", intent,
                  {"state": state, "proof_hash": proof, "source": source, "mode": "sandbox"})
        return self.get(tenant, intent)

    def process_one(self, tenant, adapter, intent=None):
        self._adapter(adapter)
        job = self.claim(tenant,intent=intent)
        if job is None: return {"processed": False, "mode": "sandbox"}
        try:
            # Read before submitting also protects a restored older database.
            receipt = adapter.lookup(job["external_key"])
            if receipt is None:
                if not self._check_claim(tenant,job["id"],job["lease"]):
                    return {"processed":True,"intent":self.get(tenant,job["id"]),"mode":"sandbox","submission_started":False}
                receipt = adapter.submit(job)
            result = self.finish(tenant, job["id"], receipt, lease=job["lease"], source="worker")
        except Exception:
            try:result = self._unknown(tenant, job["id"], job["lease"], "provider_outcome_unknown")
            except Error as error:
                if error.code!="worker_fenced":raise
                # A verified callback or reconciliation may have won the race.
                result=self.get(tenant,job["id"])
        return {"processed": True, "intent": result, "mode": "sandbox"}

    def reconcile(self, tenant, intent, adapter):
        self._adapter(adapter)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            self._expire(db, tenant)
            row = db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?", (tenant, intent)).fetchone()
            if not row: raise Error("intent_not_found", 404)
            if row["state"] in ("confirmed", "failed"): return self.public(row)
            if row["state"] != "unknown": raise Error("submission_still_in_progress", 409)
            key = row["external_key"]
        receipt = adapter.lookup(key)
        # Absence is inconclusive. It never releases funds or queues another send.
        if receipt is None: return {**self.get(tenant, intent), "lookup": "not-found-inconclusive"}
        return self.finish(tenant, intent, receipt, source="lookup")
