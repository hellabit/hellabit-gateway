"""Shared schema and append-only operational evidence in the existing database."""
from __future__ import annotations
import threading
from contextlib import nullcontext
from .ledger import canonical, digest

_schema_lock = threading.RLock()
AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_audit(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,kind TEXT NOT NULL,
 subject TEXT NOT NULL,body TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS control_audit_no_update BEFORE UPDATE ON control_audit
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS control_audit_no_delete BEFORE DELETE ON control_audit
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

def ensure_schema(book, name, sql):
    with _schema_lock:
        attr = "_hellabit_schema_" + name
        if getattr(book, attr, False):
            return
        with book.db() as db:
            db.executescript(AUDIT_SCHEMA + sql)
        setattr(book, attr, True)

def audit(book, db, tenant, kind, subject, details):
    previous = db.execute("SELECT digest FROM control_audit WHERE tenant=? ORDER BY seq DESC LIMIT 1", (tenant,)).fetchone()
    previous = previous[0] if previous else "0" * 64
    now = book.clock()
    body = canonical({"kind": kind, "subject": subject, "details": details, "created": now})
    hash_ = digest({"previous": previous, "body": body})
    db.execute("INSERT INTO control_audit(tenant,kind,subject,body,previous,digest,created) VALUES(?,?,?,?,?,?,?)",
               (tenant, kind, subject, body, previous, hash_, now))

def verify_audit(book,tenant,*,db=None):
    """Check the retained operational chain independently of its SQL triggers."""
    previous,count="0"*64,0
    with book.db() if db is None else nullcontext(db) as db:
        book.tenant(db,tenant)
        for row in db.execute("SELECT body,previous,digest FROM control_audit WHERE tenant=? ORDER BY seq",(tenant,)):
            if row["previous"]!=previous or digest({"previous":previous,"body":row["body"]})!=row["digest"]:
                return {"ok":False,"entries_checked":count}
            previous=row["digest"];count+=1
    return {"ok":True,"entries_checked":count}
