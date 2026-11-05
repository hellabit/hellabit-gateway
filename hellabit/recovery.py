"""Restartable, fenced synthetic handover on one host.

The preserved primary remains the authoritative command history. The provider
store independently owns its identity, fence generation and retained receipts.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
from .backup import LIMIT,_publish_file,create,restore
from .control import audit,verify_audit
from .ledger import Ledger,Error,canonical,digest,bind_gateway_writer,ledger_writer_lock

JOB='recovery_handover'
STAGE='recovery_candidate_job'
PENDING='recovery_publish_pending'
ACTIVE='recovery_active_job'
SEAL='recovery_seal_manifest'
BATCH=256


def _setting(db,key):
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return row[0] if row else None


def _set(db,key,value):
    db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))


def _checkpoint(book,job,*,retire=False):
    with book.db() as db:
        db.execute('BEGIN IMMEDIATE')
        _set(db,JOB,canonical(job))
        if retire:_set(db,'recovery_retired',job['id'])
        db.commit()


def _stream(db,table,field,through,after=0):
    cursor=db.execute(f'SELECT rowid,{field} FROM {table} WHERE rowid>? AND rowid<=? ORDER BY rowid',(after,through))
    while rows:=cursor.fetchmany(BATCH):
        yield [(row[0],json.loads(row[1])) for row in rows]


def _watermarks(db):
    return {table:db.execute('SELECT COALESCE(MAX(rowid),0) FROM '+table).fetchone()[0] for table in ('accepted','corrections')}


def _intent(db,receipt):
    row=db.execute('SELECT * FROM execution_intents WHERE external_key=?',(receipt['key'],)).fetchone()
    if not row:raise Error('recovery_missing_authoritative_command',423)
    return row


def _coverage(book,external,watermarks):
    """Check complete history in bounded pages; no retained-row-count ceiling."""
    hashed=hashlib.sha256();counts={'accepted':0,'corrections':0}
    with book.db() as db:
        db.execute('BEGIN')
        for batch in _stream(external,'accepted','receipt',watermarks['accepted']):
            for _,receipt in batch:
                row=_intent(db,receipt)
                if any(type(receipt[k]) is not type(row[k]) or receipt[k]!=row[k] for k in ('request_hash','asset','amount','recipient')):
                    raise Error('recovery_command_terms_conflict',423)
                if row['accepted_reference'] and row['accepted_reference']!=receipt['reference']:
                    raise Error('recovery_provider_reference_conflict',423)
                hashed.update((canonical(['receipt',receipt])+'\n').encode());counts['accepted']+=1
        for row in db.execute("""SELECT external_key FROM execution_intents
            WHERE state IN ('confirmed','failed') OR accepted_reference IS NOT NULL"""):
            if not external.execute('SELECT 1 FROM accepted WHERE key=? AND rowid<=?',(row[0],watermarks['accepted'])).fetchone():
                raise Error('recovery_provider_history_incomplete',423)
        for batch in _stream(external,'corrections','body',watermarks['corrections']):
            for _,event in batch:
                if not external.execute('SELECT 1 FROM accepted WHERE key=? AND rowid<=?',(event['key'],watermarks['accepted'])).fetchone():
                    raise Error('recovery_orphan_correction',423)
                hashed.update((canonical(['correction',event])+'\n').encode());counts['corrections']+=1
    return counts,hashed.hexdigest()


def _scope(book):
    with book.db() as db:return [r[0] for r in db.execute('SELECT id FROM tenants ORDER BY id')]


def _verify(book):
    tenants=_scope(book)
    if not tenants or any(not book.reconcile(t)['ok'] or not verify_audit(book,t)['ok'] for t in tenants):
        raise Error('recovery_history_integrity_failed',423)
    return tenants


def _preflight(restored,source,provider):
    with restored.db() as old,source.db() as current:
        if not _setting(old,'recovery_quarantine'):raise Error('quarantined_snapshot_required',423)
        if _setting(current,'recovery_quarantine') or _setting(current,'recovery_retired'):
            raise Error('current_authoritative_primary_required',423)
        provider.validate_binding(provider.read_binding(current),ledger=source.path)
        for table in ('journals','control_audit'):
            for row in old.execute('SELECT seq,digest FROM '+table):
                match=current.execute('SELECT digest FROM '+table+' WHERE seq=?',(row['seq'],)).fetchone()
                if not match or match[0]!=row['digest']:raise Error('authoritative_history_does_not_extend_snapshot',423)
    _verify(restored);_verify(source)
    with provider.db() as db:
        db.execute('BEGIN')
        _coverage(source,db,_watermarks(db))


def _hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024*1024):h.update(block)
    return h.hexdigest()


def _standalone(book):
    with book.db() as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        db.execute('PRAGMA journal_mode=DELETE').fetchall()
    with book.path.open('rb') as f:os.fsync(f.fileno())


def _stage(source,path,job,provider):
    if path.is_symlink():raise Error('unsafe_recovery_candidate_path',423)
    if not path.exists():
        with tempfile.TemporaryDirectory(prefix='.hellabit-stage-',dir=path.parent) as tmp:
            copy=Path(tmp)/'candidate.sqlite3'
            target=sqlite3.connect(copy)
            try:
                with source.db() as current:current.backup(target)
            finally:target.close()
            copy.chmod(0o600);candidate=Ledger(copy,clock=source.clock)
            with candidate.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("DELETE FROM settings WHERE key IN ('recovery_retired','recovery_quarantine','recovery_handover','recovery_active_job','recovery_cursor_accepted','recovery_cursor_corrections')")
                _set(db,STAGE,job['id'])
                _set(db,provider.binding_key,canonical(provider.binding(job['generation'],ledger=job['destination'])))
                db.execute("UPDATE execution_intents SET state='unknown',lease_hash=NULL,lease_until=NULL,last_error='recovery_outcome_unknown' WHERE state='submitting'")
                db.commit()
            _standalone(candidate)
            _publish_file(copy,path)
    return Ledger(path,clock=source.clock,_recovery_candidate=True)


def _apply(candidate,provider,job):
    from .operations import Operations
    with candidate.db() as db:
        if _setting(db,STAGE)!=job['id']:raise Error('recovery_candidate_identity_mismatch',423)
        if _setting(db,PENDING)==job['id']:return
    ops=Operations(candidate)
    with provider.db() as external:
        external.execute('BEGIN')
        counts,history_hash=_coverage(candidate,external,job['watermarks'])
        for table,field in (('accepted','receipt'),('corrections','body')):
            key='recovery_cursor_'+table
            with candidate.db() as db:cursor=int(_setting(db,key) or '0')
            for batch in _stream(external,table,field,job['watermarks'][table],cursor):
                for _,value in batch:
                    with candidate.db() as db:row=_intent(db,value)
                    if table=='accepted':
                        if row['state']=='prepared':
                            with candidate.transaction() as db:
                                db.execute("UPDATE execution_intents SET state='unknown',last_error='recovery_observation_required' WHERE id=?",(row['id'],))
                        ops.finish(row['tenant'],row['id'],value,source='lookup')
                    else:ops.adjust(row['tenant'],row['id'],value)
                with candidate.transaction() as db:_set(db,key,str(batch[-1][0]))
    tenants=_verify(candidate)
    with candidate.transaction() as db:
        for row in db.execute('SELECT * FROM api_principals').fetchall():
            grant='grant_'+secrets.token_hex(24)
            db.execute('UPDATE api_principals SET revoked=1,grant_id=? WHERE id=?',(grant,row['id']))
            terms={k:row[k] for k in ('role','customer','expires','rate_limit','valid_after')}
            terms.update(revoked=1,grant_id=grant)
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM api_grants WHERE principal=?',(row['id'],)).fetchone()[0]
            db.execute("INSERT INTO api_grants VALUES(?,?,'recovery-revoked',?,?)",(row['id'],version,canonical(terms),candidate.clock()))
        db.execute('UPDATE tenants SET bootstrap_revoked=1')
        db.execute('UPDATE agent_keys SET revoked=1');db.execute('UPDATE merchant_keys SET revoked=1')
        db.execute('UPDATE issuer SET valid_until=0')
        for tenant in tenants:
            audit(candidate,db,tenant,'recovery-handover',job['id'],{'provider_history_hash':history_hash,
                  'complete_primary_retained':True,'previous_access_revoked':True,'provider_watermarks':job['watermarks']})
        _set(db,'recovery_movement_counts',canonical(counts))
        if job.get('sealed_archive'):
            _set(db,SEAL,canonical({k:job[k] for k in ('id','destination','provider','provider_path','generation')}))
        _set(db,PENDING,job['id'])


def _publish(path,destination,job):
    # Keep the retained candidate on a distinct inode from the active database.
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='.hellabit-publish-',dir=destination.parent) as tmp:
            copy=Path(tmp)/'resumed.sqlite3'
            shutil.copyfile(path,copy);copy.chmod(0o600)
            with copy.open('rb') as f:os.fsync(f.fileno())
            _publish_file(copy,destination)
    if destination.is_symlink() or _hash(destination)!=job['candidate_sha256']:
        raise Error('recovery_destination_does_not_match_candidate',423)
    fd=os.open(destination.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _activation_state(destination,job):
    if destination.is_symlink() or not destination.is_file():return None
    db=sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True)
    try:
        if _setting(db,ACTIVE)==job['id']:return 'active'
        if _setting(db,PENDING)==job['id']:return 'pending'
        return None
    finally:db.close()


def _activate(destination,job):
    db=bind_gateway_writer(sqlite3.connect(destination,timeout=15))
    try:
        db.execute('BEGIN IMMEDIATE')
        if _setting(db,PENDING)!=job['id']:raise Error('recovery_activation_binding_changed',423)
        db.execute('DELETE FROM settings WHERE key=?',(PENDING,))
        _set(db,ACTIVE,job['id']);db.commit()
    finally:db.close()


def _check_seal(path,job):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        record=json.loads(_setting(db,SEAL) or '{}')
        if any(record.get(k)!=job[k] for k in ('id','destination','provider','provider_path','generation')) or _setting(db,PENDING)!=job['id']:
            raise Error('sealed_checkpoint_identity_mismatch',423)
    finally:db.close()


def _seal(path,archive,key,provider,job):
    class ImmutableCandidate:
        def db(self):return sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    if archive.exists():
        with tempfile.TemporaryDirectory(prefix='hellabit-seal-check-') as tmp:
            check=Path(tmp)/'candidate.sqlite3';restore(archive,check,key);_check_seal(check,job)
    else:
        create(ImmutableCandidate(),archive,key)
    sha=_hash(archive)
    with provider.db() as db:
        db.execute('BEGIN IMMEDIATE')
        generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
        if generation!=job['generation']:raise Error('handover_provider_fence_changed',423)
        old=db.execute('SELECT archive_hash,destination,generation FROM sealed_checkpoints WHERE job=?',(job['id'],)).fetchone()
        if old and tuple(old)!=(sha,job['destination'],generation):raise Error('sealed_checkpoint_conflict',423)
        db.execute('INSERT OR IGNORE INTO sealed_checkpoints(job,archive_hash,generation,destination) VALUES(?,?,?,?)',
                   (job['id'],sha,generation,job['destination']))
    return sha


def handover(restored,source,provider,destination,*,sealed_archive=None,key=None):
    from .fixtures import SandboxProvider
    if type(provider) is not SandboxProvider:raise Error('recovery_profile_not_enrolled',423)
    destination=Path(destination).absolute()
    if destination.is_symlink():raise Error('recovery_target_exists',409)
    destination=destination.parent.resolve()/destination.name
    sealed_archive=Path(sealed_archive).absolute() if sealed_archive is not None else None
    if source.path==restored.path or destination in (source.path,restored.path,provider.path):
        raise Error('independent_authoritative_primary_required',423)
    if sealed_archive and (sealed_archive.is_symlink() or sealed_archive in (source.path,restored.path,provider.path,destination) or not isinstance(key,bytes) or len(key)!=32):
        raise Error('independent_sealed_archive_and_key_required',423)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ExitStack() as stack:
        for book in sorted((source,restored),key=lambda b:str(b.path)):
            stack.enter_context(book.writer_lock(exclusive=True))
        with source.db() as db:previous=_setting(db,JOB)
        scope={'source':str(source.path),'restored':str(restored.path),'destination':str(destination),'provider':provider.identity,'provider_path':str(provider.path),
               'sealed_archive':str(sealed_archive) if sealed_archive else None}
        if previous:
            job=json.loads(previous)
            if any(job.get(k)!=v for k,v in scope.items()):raise Error('resume_exact_existing_handover',423)
        else:
            if destination.exists() or destination.is_symlink():raise Error('recovery_target_exists',409)
            if sealed_archive and sealed_archive.exists():raise Error('sealed_archive_target_exists',409)
            _preflight(restored,source,provider)
            job={**scope,'previous_generation':provider.generation,'phase':'source-retired'}
            job['id']='handover_'+digest(job)[:40]
            _checkpoint(source,job,retire=True)
        state=_activation_state(destination,job)
        if state!='active':
            generation=provider.fence_for_handover(job['id'],job['previous_generation'])
            if job.get('generation') not in (None,generation):raise Error('handover_provider_fence_changed',423)
            if 'watermarks' not in job:
                with provider.db() as external:
                    external.execute('BEGIN');watermarks=_watermarks(external)
                job.update(generation=generation,watermarks=watermarks,phase='provider-fenced')
                _checkpoint(source,job)
            path=destination.parent/('.hellabit-'+job['id']+'.sqlite3')
            try:
                if 'candidate_sha256' not in job:
                    candidate=_stage(source,path,job,provider)
                    _apply(candidate,provider,job)
                    _standalone(candidate)
                    job.update(phase='candidate-ready',candidate_sha256=_hash(path));_checkpoint(source,job)
                if path.is_symlink() or not path.is_file() or _hash(path)!=job['candidate_sha256']:
                    raise Error('recovery_candidate_changed',423)
                if sealed_archive:
                    sha=_seal(path,sealed_archive,key,provider,job)
                    job.update(phase='sealed',archive_sha256=sha);_checkpoint(source,job)
                    return {'sealed':True,'handover_id':job['id'],'archive_sha256':sha,'old_primary_retired':True,
                            'activation_pending':True,'live_payments_enabled':False}
                _publish(path,destination,job)
                _activate(destination,job)
            except BaseException:
                with source.db() as db:_set(db,'recovery_quarantine','resume-exact-handover-after-fence')
                raise
        job['phase']='complete';_checkpoint(source,job)
        db=sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True)
        try:counts=json.loads(_setting(db,'recovery_movement_counts'))
        finally:db.close()
    return {'resumed':True,'handover_id':job['id'],'profile':'intact-primary-single-host-synthetic',
            'old_primary_retired':True,'old_provider_workers_fenced':True,'provider_binding_preserved':True,
            'all_previous_credentials_revoked':True,'complete_command_history_retained':True,
            'provider_movements':counts['accepted'],'later_corrections':counts['corrections'],
            'fresh_access_enrollment_required':True,'catastrophic_archive_recovery_verified':False,'live_payments_enabled':False}


def resume_sealed(archive,key,provider,destination):
    """Resume a fenced checkpoint once, without either original ledger file."""
    archive=Path(archive).absolute();destination=Path(destination).absolute()
    if destination.is_symlink():raise Error('recovery_target_exists',409)
    destination=destination.parent.resolve()/destination.name
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size>LIMIT*2:
        raise Error('sealed_checkpoint_unavailable',423)
    sha=_hash(archive)
    with provider.db() as db:
        record=db.execute('SELECT * FROM sealed_checkpoints WHERE archive_hash=?',(sha,)).fetchone()
        current=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
    if not record or record['destination']!=str(destination):raise Error('sealed_checkpoint_binding_mismatch',423)
    if record['generation']!=current:raise Error('sealed_checkpoint_fence_changed',423)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ledger_writer_lock(destination, exclusive=True):
        return _finish_sealed(archive,key,provider,destination,sha)


def _finish_sealed(archive,key,provider,destination,sha):
    with provider.db() as db:
        record=db.execute('SELECT * FROM sealed_checkpoints WHERE archive_hash=?',(sha,)).fetchone()
        current=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
    if not record or record['destination']!=str(destination):raise Error('sealed_checkpoint_binding_mismatch',423)
    if record['generation']!=current:raise Error('sealed_checkpoint_fence_changed',423)
    job={'id':record['job'],'destination':str(destination),'provider':provider.identity,
         'provider_path':str(provider.path),'generation':current}
    state=_activation_state(destination,job)
    if record['activation_claimed'] and state is None:raise Error('sealed_checkpoint_already_used',423)
    if destination.is_symlink() or (destination.exists() and state is None):raise Error('recovery_target_exists',409)
    # The v8 pin is durable before the private file is published. An older
    # unpinned stage is never used as authority for a new activation.
    path=destination if state=='pending' else destination.parent/('.hellabit-sealed-'+job['id']+'-v8.sqlite3')
    if state!='active':
        if path.is_symlink():raise Error('unsafe_recovery_candidate_path',423)
        if not path.exists():
            with tempfile.TemporaryDirectory(prefix='.hellabit-seal-restore-',dir=destination.parent) as tmp:
                candidate=Path(tmp)/'candidate.sqlite3';restore(archive,candidate,key);_check_seal(candidate,job)
                if _hash(archive)!=sha:raise Error('sealed_checkpoint_binding_mismatch',423)
                db=bind_gateway_writer(sqlite3.connect(candidate))
                try:db.execute("DELETE FROM settings WHERE key='recovery_quarantine'");db.commit()
                finally:db.close()
                book=Ledger(candidate,_recovery_candidate=True);_verify(book)
                with provider.db() as external:
                    external.execute('BEGIN');_coverage(book,external,_watermarks(external))
                _standalone(book)
                job['candidate_sha256']=_hash(candidate)
                _pin_sealed(provider,job,sha,destination,rebuilt=True)
                _publish_file(candidate,path)
        else:
            job['candidate_sha256']=_hash(path)
        if _hash(path)!=job['candidate_sha256']:raise Error('sealed_candidate_changed',423)
        _pin_sealed(provider,job,sha,destination,rebuilt=False)
        _check_seal(path,job)
        _publish(path,destination,job)
        with provider.db() as db:
            db.execute('BEGIN IMMEDIATE')
            generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
            if generation!=current:raise Error('sealed_checkpoint_fence_changed',423)
            db.execute('UPDATE sealed_checkpoints SET activation_claimed=1 WHERE job=?',(job['id'],))
        _activate(destination,job)
    return {'resumed':True,'handover_id':job['id'],'profile':'fenced-sealed-checkpoint-synthetic',
            'original_primary_required':False,'provider_binding_preserved':True,
            'unplanned_post_checkpoint_loss_covered':False,'live_payments_enabled':False}


def _pin_sealed(provider,job,archive_hash,destination,*,rebuilt):
    with provider.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM sealed_checkpoints WHERE job=?',(job['id'],)).fetchone()
        generation=db.execute('SELECT generation FROM writer_authority WHERE id=1').fetchone()[0]
        if generation!=job['generation']:raise Error('sealed_checkpoint_fence_changed',423)
        if not row or (row['archive_hash'],row['generation'],row['destination'])!=(archive_hash,generation,str(destination)):
            raise Error('sealed_checkpoint_binding_mismatch',423)
        if row['activation_claimed'] and not destination.is_file():raise Error('sealed_checkpoint_already_used',423)
        if row['candidate_sha']==job['candidate_sha256']:return
        # A new pin is allowed only for a fresh authenticated reconstruction,
        # before any destination was published or activation was claimed.
        if not rebuilt or row['activation_claimed'] or destination.exists() or destination.is_symlink():
            raise Error('sealed_candidate_changed',423)
        db.execute('UPDATE sealed_checkpoints SET candidate_sha=? WHERE job=?',(job['candidate_sha256'],job['id']))
