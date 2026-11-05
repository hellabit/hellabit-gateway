"""Durable payout intent. Network work happens outside accounting transactions."""
from __future__ import annotations
import hmac
import json
import secrets
from .control import audit, ensure_schema
from .ledger import Error, canonical, digest, identifier, units

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
CREATE TABLE IF NOT EXISTS payment_adjustments(
 tenant TEXT NOT NULL,intent TEXT NOT NULL,event_id TEXT NOT NULL,kind TEXT NOT NULL,
 amount INTEGER NOT NULL,reference TEXT NOT NULL,proof_hash TEXT NOT NULL,body TEXT NOT NULL,
 created INTEGER NOT NULL,event_key TEXT NOT NULL,connection TEXT NOT NULL,journal_reference TEXT NOT NULL,
 PRIMARY KEY(tenant,event_key));
CREATE TRIGGER IF NOT EXISTS payment_adjustment_v6_no_update BEFORE UPDATE ON payment_adjustments
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS payment_adjustment_v6_no_delete BEFORE DELETE ON payment_adjustments
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE INDEX IF NOT EXISTS intent_pending ON execution_intents(tenant,state,created);
CREATE TABLE IF NOT EXISTS execution_retries(
 tenant TEXT NOT NULL,intent TEXT NOT NULL,lookups INTEGER NOT NULL,last_lookup INTEGER NOT NULL,
 next_lookup INTEGER NOT NULL,PRIMARY KEY(tenant,intent));
"""

class Operations:
    def __init__(self, book):
        self.book = book
        ensure_schema(book, "operations", SCHEMA)

    def enqueue(self, db, tenant, payout, asset, amount, recipient, migrated=False):
        rail = self.book.asset(db, tenant, asset)["rail"]
        id_ = "intent_" + digest(["payout-intent-v2", tenant, payout])[:48]
        body = {"payout": payout, "rail": rail, "asset": asset, "amount": amount, "recipient": recipient}
        # This release has one synthetic account per rail/asset. A real adapter
        # must bind its actual provider/account scope before it can be enabled.
        connection = "sandbox:" + rail + ":" + asset
        body["connection"]=connection
        body["command_hash"]=db.execute("SELECT request_hash FROM payouts WHERE tenant=? AND id=?",(tenant,payout)).fetchone()[0]
        now = self.book.clock()
        state = "unknown" if migrated else "prepared"
        error = "migrated_payout_requires_reconciliation" if migrated else None
        db.execute("""INSERT INTO execution_intents
            (id,tenant,payout,rail,asset,amount,recipient,external_key,request_hash,state,last_error,created,updated,connection)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (id_, tenant, payout, rail, asset, amount, recipient, "hb-" + id_, digest(body), state, error, now, now,connection))
        audit(self.book, db, tenant, "intent-created", id_, {"state": state, "payout": payout})
        return id_

    def public(self,row,db=None):
        from .outcomes import intent_view
        if db is not None:return intent_view(db,row)
        with self.book.db() as connection:return intent_view(connection,row)

    def get(self, tenant, intent):
        identifier(intent)
        with self.book.db() as db:
            self.book.tenant(db, tenant)
            row = db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?", (tenant, intent)).fetchone()
            if not row: raise Error("intent_not_found", 404)
            return self.public(row,db)

    def list(self, tenant):
        with self.book.db() as db:
            self.book.tenant(db, tenant)
            rows = db.execute("""SELECT * FROM execution_intents WHERE tenant=?
                ORDER BY CASE state WHEN 'unknown' THEN 0 WHEN 'submitting' THEN 1 WHEN 'prepared' THEN 2 ELSE 3 END,
                created DESC,rowid DESC LIMIT 30""", (tenant,)).fetchall()
            counts = {r["state"]: r["n"] for r in db.execute(
                "SELECT state,COUNT(*) n FROM execution_intents WHERE tenant=? GROUP BY state", (tenant,))}
            projected=[self.public(r,db) for r in rows]
        return {"intents": projected, "counts": counts, "mode": "sandbox",
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
            self.book.assert_payment_writes(db)
            self._expire(db, tenant)
            row = db.execute("""SELECT e.* FROM execution_intents e WHERE e.tenant=? AND e.state='prepared'
                AND (? IS NULL OR e.id=?)
                AND NOT EXISTS(SELECT 1 FROM execution_intents uncertain
                  WHERE uncertain.tenant=e.tenant AND uncertain.connection=e.connection
                    AND uncertain.state IN ('unknown','submitting'))
                ORDER BY e.created,e.rowid LIMIT 1""", (tenant,intent,intent)).fetchone()
            if not row: return None
            lease = secrets.token_hex(24)
            now=self.book.clock();until=now+lease_seconds
            db.execute("""UPDATE execution_intents SET state='submitting',attempts=attempts+1,
                lease_hash=?,lease_until=?,updated=? WHERE id=?""",
                (digest(lease), until, now, row["id"]))
            audit(self.book, db, tenant, "submission-claimed", row["id"], {"attempt": row["attempts"] + 1})
            return {**self.public(row,db), "state": "submitting", "effective_outcome":"submitting","lease": lease,
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
                return {**self.public(row,db), "replayed": True}
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
            terminal = row["state"] in ("confirmed", "failed")
            if terminal:
                key = row["external_key"]
            elif row["state"] != "unknown": raise Error("submission_still_in_progress", 409)
            key = row["external_key"]
        if terminal:
            if row["state"] == "confirmed" and callable(getattr(adapter,"adjustments",None)):
                for event in adapter.adjustments(key):
                    self.adjust(tenant,intent,event)
            return self.get(tenant,intent)
        receipt = adapter.lookup(key)
        # Absence is inconclusive. It never releases funds or queues another send.
        if receipt is None: return {**self.get(tenant, intent), "lookup": "not-found-inconclusive"}
        return self.finish(tenant, intent, receipt, source="lookup")

    def tick(self,tenant,adapter,intent=None):
        """Bounded reconciliation and progress on an unrelated eligible account."""
        self._adapter(adapter)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant);self.book.assert_payment_writes(db);self._expire(db,tenant)
        if intent is not None:
            row=self.get(tenant,intent)
            if row['state']!='prepared':
                result=self.reconcile(tenant,intent,adapter)
                return {"processed":False,"reconciled_original":intent,"state":result['state'],"new_submission":False}
            reply=self.process_one(tenant,adapter,intent=intent)
            return {"processed":reply['processed'],"intent":intent,
                    "state":reply['intent']['state'] if reply['processed'] else row['state']}
        with self.book.db() as db:
            due=db.execute("""SELECT e.id FROM execution_intents e LEFT JOIN execution_retries r
                ON r.tenant=e.tenant AND r.intent=e.id WHERE e.tenant=? AND e.state='unknown'
                AND COALESCE(r.next_lookup,0)<=? ORDER BY COALESCE(r.last_lookup,0),e.created,e.rowid LIMIT 1""",
                (tenant,self.book.clock())).fetchone()
        observation={}
        if due:
            try:
                result=self.reconcile(tenant,due['id'],adapter)
                observation={"reconciled_original":due['id'],"reconciled_state":result['state']}
            except Error as error:observation={"reconciled_original":due['id'],"lookup_error":error.code}
            except (OSError,TimeoutError):observation={"reconciled_original":due['id'],"lookup_error":"provider_lookup_unavailable"}
            with self.book.transaction() as db:
                old=db.execute("SELECT lookups FROM execution_retries WHERE tenant=? AND intent=?",(tenant,due['id'])).fetchone()
                attempts=(old[0] if old else 0)+1;delay=min(300,2**min(attempts,9))
                db.execute("INSERT OR REPLACE INTO execution_retries VALUES(?,?,?,?,?)",
                           (tenant,due['id'],attempts,self.book.clock(),self.book.clock()+delay))
        reply=self.process_one(tenant,adapter)
        result={"processed":reply['processed'],**observation}
        if reply['processed']:result.update({"intent":reply['intent']['id'],"state":reply['intent']['state']})
        return result

    def cancel_unknown(self, tenant, intent, adapter):
        """Only an authoritative, permanent no-effect record can release a hold."""
        self._adapter(adapter)
        row=self.get(tenant,intent)
        with self.book.db() as db:self.book.assert_payment_writes(db)
        if row["state"]!="unknown":raise Error("unknown_payment_required",409)
        if row["last_error"]=="migrated_payout_requires_reconciliation":
            raise Error("legacy_payment_identity_requires_external_reconciliation",423)
        close=getattr(adapter,"close_without_effect",None)
        if not callable(close):raise Error("authoritative_cancellation_not_supported",423)
        receipt=close(row)
        return self.finish(tenant,intent,receipt,source="lookup")

    def adjust(self, tenant, intent, event, *, verified_event=None):
        """Append a later returned movement; never rewrite the original settlement."""
        required={"mode","key","request_hash","status","reference","amount","asset",
                  "recipient","event_id","original_reference"}
        if (not isinstance(event,dict) or set(event)!=required or event["mode"]!="sandbox" or
                event["status"] not in ("returned","reversed")):
            raise Error("invalid_payment_adjustment",403)
        identifier(event["event_id"]);identifier(event["reference"]);units(event["amount"])
        proof=digest(event)
        with self.book.transaction() as db:
            row=db.execute("SELECT * FROM execution_intents WHERE tenant=? AND id=?",(tenant,intent)).fetchone()
            if not row or row["state"]!="confirmed":raise Error("confirmed_payment_required",409)
            expected={"key":row["external_key"],"request_hash":row["request_hash"],"asset":row["asset"],
                      "recipient":row["recipient"],"original_reference":row["reference"]}
            if any(type(event[k]) is not type(v) or event[k]!=v for k,v in expected.items()):
                raise Error("adjustment_binding_mismatch",403)
            from .events import event_key
            identity=event_key(tenant,row['connection'],event['event_id'])
            if verified_event is not None:
                binding=db.execute("SELECT provider FROM provider_bindings WHERE tenant=? AND rail=?",(tenant,row["rail"])).fetchone()
                saved=db.execute("SELECT body FROM provider_inbox WHERE tenant=? AND provider=? AND id=?",(tenant,*verified_event)).fetchone()
                if not binding or binding[0]!=verified_event[0] or not saved or json.loads(saved[0])["receipt"]!=event:
                    raise Error("verified_provider_event_required",403)
            old=db.execute("SELECT proof_hash FROM payment_adjustments WHERE tenant=? AND event_key=?",(tenant,identity)).fetchone()
            if old:
                if old[0]!=proof:raise Error("adjustment_identity_conflict",409)
                return {"event":event["event_id"],"replayed":True}
            total=db.execute("SELECT COALESCE(SUM(amount),0) FROM payment_adjustments WHERE tenant=? AND intent=?",(tenant,intent)).fetchone()[0]
            if total+event["amount"]>row["amount"]:raise Error("adjustment_exceeds_payment",409)
            payout=db.execute("SELECT source FROM payouts WHERE tenant=? AND id=?",(tenant,row["payout"])).fetchone()
            self.book.consume_rail_event(db,tenant,row["rail"],identity,"payout-adjustment",proof)
            self.book._post(db,tenant,"payout-"+event["status"],identity,row["asset"],
                            [("clearing:"+row["rail"],-event["amount"]),(payout[0],event["amount"])],
                            {"original_intent":intent,"proof_hash":proof,"mode":"sandbox"})
            db.execute("INSERT INTO payment_adjustments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (tenant,intent,event["event_id"],event["status"],event["amount"],event["reference"],proof,canonical(event),self.book.clock(),identity,row['connection'],identity))
            audit(self.book,db,tenant,"payout-adjusted",intent,{"event":event["event_id"],"proof_hash":proof})
        return {"event":event["event_id"],"event_key":identity,"connection":row['connection'],"amount":event["amount"],"replayed":False}
