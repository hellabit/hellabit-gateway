"""An independent synthetic provider store, never an institution connector."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import secrets
from contextlib import closing
from .ledger import Error, canonical, digest

class SandboxProvider:
    mode = "sandbox"
    binding_key = 'sandbox_provider_binding'

    def __init__(self, path, after_accept=None, *, require_existing=False):
        self.path, self.after_accept = Path(path).absolute(), after_accept
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise Error("unsafe_fixture_path", 400)
        self.path=self.path.resolve()
        if require_existing and not self.path.is_file():raise Error('bound_provider_store_missing',423)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS accepted(key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,receipt TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS corrections(key TEXT NOT NULL,event_id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            for table in ('accepted','corrections'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append_only'); END")
            db.execute("CREATE TABLE IF NOT EXISTS writer_authority(id INTEGER PRIMARY KEY CHECK(id=1),generation TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO writer_authority VALUES(1,?)",(secrets.token_hex(24),))
            db.execute('CREATE TABLE IF NOT EXISTS store_identity(id INTEGER PRIMARY KEY CHECK(id=1),identity TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO store_identity VALUES(1,?)',('fixture_'+secrets.token_hex(24),))
            self.identity=db.execute('SELECT identity FROM store_identity WHERE id=1').fetchone()[0]
            db.execute('CREATE TABLE IF NOT EXISTS recovery_fences(job TEXT PRIMARY KEY,previous_generation TEXT NOT NULL,generation TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS sealed_checkpoints(job TEXT PRIMARY KEY,archive_hash TEXT NOT NULL UNIQUE,
                generation TEXT NOT NULL,destination TEXT NOT NULL,candidate_sha TEXT,activation_claimed INTEGER NOT NULL DEFAULT 0)''')
            self.generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
        os.chmod(self.path, 0o600)

    @classmethod
    def read_binding(cls,db):
        row=db.execute('SELECT value FROM settings WHERE key=?',(cls.binding_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def binding(self,generation=None,*,ledger):
        return {'identity':self.identity,'path':str(self.path),'generation':generation or self.generation,'ledger':str(ledger)}

    def validate_binding(self,binding,*,ledger):
        if binding and binding!=self.binding(ledger=ledger):raise Error('provider_binding_or_generation_mismatch',423)

    @classmethod
    def _open_bound(cls,binding,ledger,after_accept):
        if set(binding)!={'identity','path','generation','ledger'} or binding['ledger']!=str(ledger):
            raise Error('provider_binding_or_generation_mismatch',423)
        path=Path(binding['path'])
        if not path.is_absolute() or path.is_symlink() or not path.is_file():raise Error('bound_provider_store_missing',423)
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
                db.execute('BEGIN')
                identity=db.execute('SELECT identity FROM store_identity WHERE id=1').fetchone()
                generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()
        except sqlite3.Error:raise Error('bound_provider_store_unavailable',423) from None
        if not identity or not generation:raise Error('bound_provider_store_unavailable',423)
        provider=object.__new__(cls)
        provider.path,provider.after_accept,provider.identity,provider.generation=path,after_accept,identity[0],generation[0]
        provider.validate_binding(binding,ledger=ledger)
        return provider

    @classmethod
    def for_ledger(cls,book,after_accept=None):
        """Only a new synthetic ledger may implicitly create its first store."""
        with book.db() as db:
            db.execute('BEGIN');book.assert_payment_writes(db)
            binding=cls.read_binding(db)
        if binding:return cls._open_bound(binding,book.path,after_accept)
        with book.transaction() as db:
            book.assert_payment_writes(db)
            binding=cls.read_binding(db)
            if binding:return cls._open_bound(binding,book.path,after_accept)
            path=book.path.with_name(book.path.name+'.sandbox-provider.sqlite3')
            if not path.is_file() and db.execute('SELECT 1 FROM execution_intents WHERE attempts>0 LIMIT 1').fetchone():
                raise Error('existing_payment_provider_store_required',423)
            provider=cls(path,after_accept)
            db.execute('INSERT INTO settings VALUES(?,?)',(cls.binding_key,canonical(provider.binding(ledger=book.path))))
            return provider

    def fence_for_handover(self,job,previous_generation):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
            old=db.execute('SELECT previous_generation,generation FROM recovery_fences WHERE job=?',(job,)).fetchone()
            if old:
                if old[0]!=previous_generation or old[1]!=current:raise Error('handover_provider_fence_changed',423)
                return old[1]
            if current!=previous_generation:raise Error('handover_provider_fence_changed',423)
            generation=secrets.token_hex(24)
            db.execute('UPDATE writer_authority SET generation=? WHERE id=1',(generation,))
            db.execute('INSERT INTO recovery_fences VALUES(?,?,?)',(job,previous_generation,generation))
            return generation

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

