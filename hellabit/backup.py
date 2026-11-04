"""Authenticated encrypted snapshots; restore always targets a new database."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from .ledger import Error,canonical

LIMIT=64*1024*1024
AAD=b"hellabit.sqlite-backup.v1"

def _key(key):
    if not isinstance(key,bytes) or len(key)!=32:raise Error("backup_key_requires_32_bytes",400)

def _write_new(path,data):
    path=Path(path).absolute()
    if path.exists() or path.is_symlink():raise Error("backup_target_exists",409)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(prefix=".hellabit-write-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
        try:os.link(temp,path)
        except FileExistsError:raise Error("backup_target_exists",409) from None
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(temp).unlink(missing_ok=True)

def create(book,destination,key,timeout=10):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    if type(timeout) not in (int,float) or not 0<timeout<=60:raise Error("invalid_backup_timeout",400)
    started=time.monotonic()
    def progress(_status,_remaining,_total):
        if time.monotonic()-started>timeout:raise Error("backup_deadline_exceeded",503)
        if _total*page_size>LIMIT:raise Error("backup_size_limit",413)
    with tempfile.TemporaryDirectory(prefix="hellabit-snapshot-",dir=book.path.parent) as tmp:
        path=Path(tmp)/"snapshot.sqlite3"
        source=book.db();target=sqlite3.connect(path)
        page_size=source.execute("PRAGMA page_size").fetchone()[0]
        path.chmod(0o600)
        try:source.backup(target,pages=100,progress=progress,sleep=0.01)
        finally:target.close();source.close()
        if path.stat().st_size>LIMIT:raise Error("backup_size_limit",413)
        plaintext=path.read_bytes()
    nonce=secrets.token_bytes(12)
    encrypted=AESGCM(key).encrypt(nonce,plaintext,AAD)
    envelope=canonical({"schema":AAD.decode(),"nonce":base64.b64encode(nonce).decode(),
                        "ciphertext":base64.b64encode(encrypted).decode()}).encode()
    _write_new(destination,envelope)
    return {"schema":"hellabit.backup-result.v1","encrypted":True,"bytes":len(envelope),
            "ciphertext_sha256":hashlib.sha256(envelope).hexdigest(),"financial_readiness_verified":False}

def restore(source,destination,key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    source=Path(source);destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise Error("restore_target_exists",409)
    if source.is_symlink() or not source.is_file() or source.stat().st_size>LIMIT*2:
        raise Error("invalid_backup_source",400)
    try:
        body=json.loads(source.read_bytes())
        if set(body)!={"schema","nonce","ciphertext"} or body["schema"]!=AAD.decode():raise ValueError()
        nonce=base64.b64decode(body["nonce"],validate=True)
        ciphertext=base64.b64decode(body["ciphertext"],validate=True)
        if len(nonce)!=12 or len(ciphertext)>LIMIT+16:raise ValueError()
        plaintext=AESGCM(key).decrypt(nonce,ciphertext,AAD)
    except (InvalidTag,ValueError,TypeError,KeyError):
        raise Error("backup_authentication_failed",403) from None
    if not plaintext.startswith(b"SQLite format 3\x00") or len(plaintext)>LIMIT:
        raise Error("backup_database_invalid",400)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix="hellabit-restore-",dir=destination.parent) as tmp:
        path=Path(tmp)/"verify.sqlite3";path.write_bytes(plaintext);path.chmod(0o600)
        try:
            with sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise Error("backup_database_invalid",400)
                required={"tenants","journals","postings","operations","quotes","settings"}
                if not required<={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    raise Error("backup_database_schema_mismatch",400)
        except sqlite3.DatabaseError:raise Error("backup_database_invalid",400) from None
        _write_new(destination,plaintext)
    return {"restored":True,"destination_existed":False,"source_deleted":False,
            "requires_operator_acceptance_before_promotion":True}

def recovery_drill(book):
    """A local rehearsal is evidence of recovery mechanics, not key custody."""
    from .ledger import Ledger
    from .control import audit,ensure_schema,verify_audit
    ensure_schema(book,"audit-only","")
    key=secrets.token_bytes(32)
    with tempfile.TemporaryDirectory(prefix="hellabit-drill-",dir=book.path.parent) as tmp:
        encrypted=Path(tmp)/"snapshot.hbbackup";target=Path(tmp)/"restored.sqlite3"
        backup=create(book,encrypted,key);restore(encrypted,target,key)
        recovered=Ledger(target)
        with recovered.db() as db:tenants=[r[0] for r in db.execute("SELECT id FROM tenants")]
        checks=[recovered.reconcile(t) for t in tenants]
        if not all(c["ok"] for c in checks) or not all(verify_audit(recovered,t)["ok"] for t in tenants):
            raise Error("restored_ledger_check_failed",500)
        result={"schema":"hellabit.recovery-drill.v1","ok":True,"scope":"local-rehearsal",
                "tenants_checked":len(tenants),"journal_count":sum(c["journal_count"] for c in checks),
                "encrypted":True,"control_audit_verified":True,"snapshot_ciphertext_hash":backup["ciphertext_sha256"],
                "production_key_custody_established":False,"offsite_retention_established":False,
                "observed_at":book.clock()}
    ensure_schema(book,"audit-only","")
    with book.transaction() as db:
        for tenant in tenants:audit(book,db,tenant,"local-recovery-drill",backup["ciphertext_sha256"],
                                   {"ok":True,"scope":"local-rehearsal","production_key_custody_established":False})
    return result
