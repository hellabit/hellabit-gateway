"""An independent synthetic provider store, never an institution connector."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
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
        with self.db() as db: return db.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]
