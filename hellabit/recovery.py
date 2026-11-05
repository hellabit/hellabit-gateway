"""Fenced single-host handover from an intact authoritative primary.

This is a synthetic recovery profile. A lost primary requires an independently
enrolled complete command archive; a snapshot and a provider lookup cannot
reconstruct missing purchases, grants or accounting commands.
"""
from contextlib import ExitStack
from pathlib import Path
import secrets
import sqlite3
import tempfile
from .backup import _publish_file
from .control import audit,verify_audit
from .ledger import Ledger,Error,canonical,digest


def _coverage(book,history):
    if history.get('mode')!='sandbox' or history.get('retention')!='complete-independent-fixture-store':
        raise Error('complete_provider_history_required',423)
    with book.db() as db:
        rows=db.execute('SELECT * FROM execution_intents LIMIT 10001').fetchall()
        if len(rows)>10000:raise Error('recovery_command_history_exceeds_bound',423)
        known={r['external_key']:dict(r) for r in rows}
    receipts={}
    for receipt in history['receipts']:
        row=known.get(receipt['key'])
        if not row:raise Error('recovery_missing_authoritative_command',423)
        if any(receipt[k]!=row[k] for k in ('request_hash','asset','amount','recipient')):
            raise Error('recovery_command_terms_conflict',423)
        receipts[receipt['key']]=receipt
    for row in rows:
        if row['state'] in ('confirmed','failed') and row['external_key'] not in receipts:
            raise Error('recovery_provider_history_incomplete',423)
    for event in history['corrections']:
        if event['key'] not in receipts:raise Error('recovery_orphan_correction',423)
    return known,receipts


def handover(restored,source,provider,destination):
    """Retain all source commands, recover observations, retire all old access."""
    from .fixtures import SandboxProvider
    from .operations import Operations
    if type(provider) is not SandboxProvider:raise Error('recovery_profile_not_enrolled',423)
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise Error('recovery_target_exists',409)
    if source.path==restored.path:raise Error('independent_authoritative_primary_required',423)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ExitStack() as stack:
        for book in sorted((source,restored),key=lambda b:str(b.path)):
            stack.enter_context(book.writer_lock(exclusive=True))
        with restored.db() as old,source.db() as current:
            if not old.execute("SELECT 1 FROM settings WHERE key='recovery_quarantine'").fetchone():
                raise Error('quarantined_snapshot_required',423)
            if current.execute("SELECT 1 FROM settings WHERE key IN ('recovery_quarantine','recovery_retired')").fetchone():
                raise Error('current_authoritative_primary_required',423)
            tenants=[r[0] for r in current.execute('SELECT id FROM tenants')]
            if not tenants:raise Error('recovery_workspace_missing',423)
            for table in ('journals','control_audit'):
                # Compare every retained chain entry, not only the last balance.
                for row in old.execute('SELECT seq,digest FROM '+table):
                    match=current.execute('SELECT digest FROM '+table+' WHERE seq=?',(row['seq'],)).fetchone()
                    if not match or match[0]!=row['digest']:raise Error('authoritative_history_does_not_extend_snapshot',423)
        for book in (source,restored):
            with book.db() as db:scope=[r[0] for r in db.execute('SELECT id FROM tenants')]
            if any(not book.reconcile(t)['ok'] or not verify_audit(book,t)['ok'] for t in scope):
                raise Error('recovery_history_integrity_failed',423)
        _coverage(source,provider.recovery_history())
        with tempfile.TemporaryDirectory(prefix='hellabit-handover-',dir=destination.parent) as tmp:
            path=Path(tmp)/'candidate.sqlite3'
            target=sqlite3.connect(path)
            try:
                with source.db() as current:current.backup(target)
            finally:target.close()
            path.chmod(0o600)
            candidate=Ledger(path,clock=source.clock);operations=Operations(candidate)
            # Independent provider fencing serializes with even an in-flight
            # old submit. New command creation remains locked at the primary.
            history=provider.recovery_history(fence=True)
            try:
                known,receipts=_coverage(candidate,history)
                with source.db() as db:
                    db.execute("INSERT INTO settings VALUES('recovery_retired',?)",(str(source.clock()),))
                with candidate.transaction() as db:
                    db.execute("UPDATE execution_intents SET state='unknown',lease_hash=NULL,lease_until=NULL,last_error='recovery_observation_required' WHERE state IN ('submitting','prepared') AND external_key IN ("+','.join('?' for _ in receipts)+")",tuple(receipts)) if receipts else None
                    db.execute("UPDATE execution_intents SET state='unknown',lease_hash=NULL,lease_until=NULL,last_error='recovery_outcome_unknown' WHERE state='submitting'")
                for key,receipt in receipts.items():
                    row=known[key]
                    operations.finish(row['tenant'],row['id'],receipt,source='lookup')
                for event in history['corrections']:
                    row=known[event['key']];operations.adjust(row['tenant'],row['id'],event)
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
                        audit(candidate,db,tenant,'recovery-handover','single-host-synthetic',
                              {'complete_primary_retained':True,'provider_history_hash':digest(history),
                               'previous_access_revoked':True,'live_payments_enabled':False})
                if any(not candidate.reconcile(t)['ok'] or not verify_audit(candidate,t)['ok'] for t in tenants):
                    raise Error('recovered_history_integrity_failed',423)
                with candidate.db() as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
                    db.execute('PRAGMA journal_mode=DELETE').fetchall()
                _publish_file(path,destination)
            except BaseException:
                # Fencing has happened. Preserve both original histories and
                # stop financial writes; never automatically revive an old key.
                with source.db() as db:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('recovery_quarantine','handover-stopped-after-fence')")
                raise
    return {'resumed':True,'profile':'intact-primary-single-host-synthetic','old_primary_retired':True,
            'old_provider_workers_fenced':True,'all_previous_credentials_revoked':True,
            'complete_command_history_retained':True,'provider_movements':len(receipts),
            'later_corrections':len(history['corrections']),'fresh_access_enrollment_required':True,
            'catastrophic_archive_recovery_verified':False,'live_payments_enabled':False}
