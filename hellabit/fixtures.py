"""An independent synthetic provider store, never an institution connector."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import secrets
from .ledger import Error, canonical, digest

class SandboxProvider:
    mode = "sandbox"

    def __init__(self, path, after_accept=None):
        self.path, self.after_accept = Path(path).absolute(), after_accept
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise Error("unsafe_fixture_path", 400)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accepted(key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,receipt TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS corrections(key TEXT NOT NULL,event_id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS writer_authority(id INTEGER PRIMARY KEY CHECK(id=1),generation TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO writer_authority VALUES(1,?)",(secrets.token_hex(24),))
            self.generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
        os.chmod(self.path, 0o600)

    def db(self):
        from .ledger import Connection
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None, factory=Connection)
        db.row_factory = sqlite3.Row
        return db

    def lookup(self, key):
        with self.db() as db: row = db.execute("SELECT receipt FROM accepted WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def submit(self, job):
        receipt = {"mode": "sandbox", "key": job["external_key"], "request_hash": job["request_hash"],
                   "status": "settled", "reference": "fixture-" + digest(job["external_key"])[:32],
                   "amount": job["amount"], "asset": job["asset"], "recipient": job["recipient"]}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._writer(db)
            old = db.execute("SELECT * FROM accepted WHERE key=?", (job["external_key"],)).fetchone()
            if old and old["request_hash"] != job["request_hash"]:
                db.rollback(); raise Error("provider_idempotency_conflict")
            if not old:
                db.execute("INSERT INTO accepted VALUES(?,?,?)", (job["external_key"], job["request_hash"], canonical(receipt)))
            else: receipt = json.loads(old["receipt"])
            db.commit()
        if self.after_accept: self.after_accept()
        return receipt

    def accepted_count(self):
        with self.db() as db:
            return sum(json.loads(r[0])["status"]!="rejected" for r in db.execute("SELECT receipt FROM accepted"))

    def close_without_effect(self, job):
        # The same transaction competes with submit(). An existing acceptance
        # wins; otherwise this tombstone permanently prevents a future send.
        receipt={"mode":"sandbox","key":job["external_key"],"request_hash":job["request_hash"],
                 "status":"rejected","reference":"closed-"+digest(job["external_key"])[:32],
                 "amount":job["amount"],"asset":job["asset"],"recipient":job["recipient"]}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._writer(db)
            old=db.execute("SELECT request_hash,receipt FROM accepted WHERE key=?",(job["external_key"],)).fetchone()
            if old and old[0]!=job["request_hash"]:raise Error("provider_idempotency_conflict")
            if old:receipt=json.loads(old[1])
            else:db.execute("INSERT INTO accepted VALUES(?,?,?)",(job["external_key"],job["request_hash"],canonical(receipt)))
            db.commit()
        return receipt

    def return_payment(self, key, event_id, amount, status="returned"):
        from .ledger import identifier,units
        identifier(event_id);units(amount)
        receipt=self.lookup(key)
        if not receipt or receipt["status"]!="settled" or status not in ("returned","reversed"):
            raise Error("settled_payment_required")
        event={**receipt,"status":status,"amount":amount,"event_id":event_id,
               "original_reference":receipt["reference"],"reference":"return-"+digest([key,event_id])[:32]}
        with self.db() as db:
            old=db.execute("SELECT body FROM corrections WHERE event_id=?",(event_id,)).fetchone()
            if old and old[0]!=canonical(event):raise Error("provider_adjustment_conflict")
            db.execute("INSERT OR IGNORE INTO corrections VALUES(?,?,?)",(key,event_id,canonical(event)))
        return event

    def adjustments(self,key):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM corrections WHERE key=? ORDER BY rowid",(key,))]

    def _writer(self,db):
        if db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]!=self.generation:
            raise Error('provider_writer_fenced',423)

    def recovery_history(self,fence=False):
        """Complete synthetic history; never a declaration about a real bank."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if fence:
                self._writer(db)
                db.execute('UPDATE writer_authority SET generation=? WHERE id=1',(secrets.token_hex(24),))
            receipts=[json.loads(r[0]) for r in db.execute('SELECT receipt FROM accepted ORDER BY rowid LIMIT 10001')]
            corrections=[json.loads(r[0]) for r in db.execute('SELECT body FROM corrections ORDER BY rowid LIMIT 10001')]
            if len(receipts)>10000 or len(corrections)>10000:raise Error('recovery_history_exceeds_bound',423)
            db.commit()
        return {'mode':'sandbox','retention':'complete-independent-fixture-store','receipts':receipts,'corrections':corrections}
