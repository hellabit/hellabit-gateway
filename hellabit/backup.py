"""Authenticated encrypted snapshots; restore always targets a new database."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import struct
import tempfile
import time
from contextlib import closing
from .ledger import Error,canonical,bind_gateway_writer

LIMIT=1024*1024*1024
LEGACY_LIMIT=64*1024*1024
CHUNK=1024*1024
MAGIC=b"HellabitBackup\x00\x02"
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

def _publish_file(source,destination):
    destination=Path(destination).absolute()
    try:os.link(source,destination)
    except FileExistsError:raise Error("backup_target_exists",409) from None
    directory=os.open(destination.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)

def _working_bytes(database_bytes):
    chunks = (database_bytes + CHUNK - 1) // CHUNK
    return 2 * database_bytes + len(MAGIC) + 36 + 16 * chunks

def capacity(book, directory=None):
    with book.db() as db:
        size=db.execute("PRAGMA page_count").fetchone()[0]*db.execute("PRAGMA page_size").fetchone()[0]
    available=shutil.disk_usage(directory or book.path.parent).free
    working=_working_bytes(size)
    return {"database_bytes":size,"backup_limit_bytes":LIMIT,
            "minimum_working_bytes":working,"available_working_bytes":available,
            "has_minimum_working_space":available>=working,
            "remaining_bytes":max(0,LIMIT-size),"status":"limit-exceeded" if size>LIMIT else "capacity-warning" if size>=LIMIT*0.8 else "within-limit"}

def _cipher(key,salt):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    derived=HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=b"hellabit.backup.v2").derive(key)
    return AESGCM(derived)

def create(book,destination,key,timeout=10):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise Error("backup_target_exists",409)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if type(timeout) not in (int,float) or not 0<timeout<=60:raise Error("invalid_backup_timeout",400)
    headroom=capacity(book,destination.parent)
    if headroom["status"]=="limit-exceeded":raise Error("backup_size_limit",413)
    if not headroom["has_minimum_working_space"]:raise Error("backup_insufficient_working_space",507)
    started=time.monotonic()
    def progress(_status,_remaining,_total):
        if time.monotonic()-started>timeout:raise Error("backup_deadline_exceeded",503)
        if _total*page_size>LIMIT:raise Error("backup_size_limit",413)
    with tempfile.TemporaryDirectory(prefix="hellabit-snapshot-",dir=destination.parent) as tmp:
        path=Path(tmp)/"snapshot.sqlite3"
        source=book.db();target=sqlite3.connect(path)
        page_size=source.execute("PRAGMA page_size").fetchone()[0]
        path.chmod(0o600)
        try:source.backup(target,pages=100,progress=progress,sleep=0.01)
        finally:target.close();source.close()
        if path.stat().st_size>LIMIT:raise Error("backup_size_limit",413)
        salt,prefix=secrets.token_bytes(16),secrets.token_bytes(8)
        header=MAGIC+salt+prefix+struct.pack(">QI",path.stat().st_size,CHUNK)
        cipher=_cipher(key,salt);encrypted_path=Path(tmp)/"encrypted.hbbackup"
        checksum=hashlib.sha256()
        with path.open("rb") as source,encrypted_path.open("xb") as output:
            encrypted_path.chmod(0o600);output.write(header);checksum.update(header)
            index=0
            while data:=source.read(CHUNK):
                if time.monotonic()-started>timeout:raise Error("backup_deadline_exceeded",503)
                counter=index.to_bytes(4,"big")
                encrypted=cipher.encrypt(prefix+counter,data,header+counter)
                output.write(encrypted);checksum.update(encrypted);index+=1
            output.flush();os.fsync(output.fileno())
        size=encrypted_path.stat().st_size
        _publish_file(encrypted_path,destination)
    return {"schema":"hellabit.backup-result.v2","encrypted":True,"bytes":size,
            "ciphertext_sha256":checksum.hexdigest(),"financial_readiness_verified":False,
            "format_version":2,"bounded_memory_bytes":CHUNK*3}

def restore(source,destination,key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _key(key)
    source=Path(source);destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise Error("restore_target_exists",409)
    if source.is_symlink() or not source.is_file() or source.stat().st_size>LIMIT*2:
        raise Error("invalid_backup_source",400)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix="hellabit-restore-",dir=destination.parent) as tmp:
        path=Path(tmp)/"verify.sqlite3"
        try:
            with source.open("rb") as encrypted,path.open("xb") as output:
                path.chmod(0o600)
                magic=encrypted.read(len(MAGIC))
                if magic==MAGIC:
                    rest=encrypted.read(36)
                    if len(rest)!=36:raise ValueError()
                    salt,prefix=rest[:16],rest[16:24]
                    size,chunk=struct.unpack(">QI",rest[24:])
                    if not 16<=size<=LIMIT or chunk!=CHUNK:raise ValueError()
                    header=magic+rest;cipher=_cipher(key,salt)
                    for index,start in enumerate(range(0,size,CHUNK)):
                        counter=index.to_bytes(4,"big");length=min(CHUNK,size-start)
                        data=encrypted.read(length+16)
                        if len(data)!=length+16:raise ValueError()
                        output.write(cipher.decrypt(prefix+counter,data,header+counter))
                    if encrypted.read(1):raise ValueError()
                else:
                    if source.stat().st_size>LEGACY_LIMIT*2:raise ValueError()
                    encrypted.seek(0);body=json.load(encrypted)
                    if set(body)!={"schema","nonce","ciphertext"} or body["schema"]!=AAD.decode():raise ValueError()
                    nonce=base64.b64decode(body["nonce"],validate=True)
                    ciphertext=base64.b64decode(body["ciphertext"],validate=True)
                    if len(nonce)!=12 or len(ciphertext)>LEGACY_LIMIT+16:raise ValueError()
                    output.write(AESGCM(key).decrypt(nonce,ciphertext,AAD))
                output.flush();os.fsync(output.fileno())
        except (InvalidTag,ValueError,TypeError,KeyError):
            raise Error("backup_authentication_failed",403) from None
        with path.open("rb") as f:
            if f.read(16)!=b"SQLite format 3\x00":raise Error("backup_database_invalid",400)
        try:
            with closing(sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise Error("backup_database_invalid",400)
                required={"tenants","journals","postings","operations","quotes","settings"}
                if not required<={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    raise Error("backup_database_schema_mismatch",400)
        except sqlite3.DatabaseError:raise Error("backup_database_invalid",400) from None
        # This marker is installed before the destination becomes visible. Even
        # a snapshot taken before a command existed cannot authorize a replay.
        db=bind_gateway_writer(sqlite3.connect(path))
        try:
            # Publish a self-contained file. A WAL sidecar in the temporary
            # directory must never contain the quarantine marker at promotion.
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("INSERT OR REPLACE INTO settings VALUES('recovery_quarantine',?)",(str(int(time.time())),))
            db.commit()
        finally:db.close()
        with path.open("rb") as f:os.fsync(f.fileno())
        _publish_file(path,destination)
    return {"restored":True,"destination_existed":False,"source_deleted":False,
            "requires_operator_acceptance_before_promotion":True,"payment_execution_blocked":True,
            "recovery_quarantine":True}

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
