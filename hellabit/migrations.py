"""Atomic, versioned storage upgrades before any component serves a request."""
import hashlib
import secrets
import sqlite3
import threading
from contextlib import closing
from .ledger import Error,canonical,SCHEMA

VERSION=12
APPLICATION_ID=0x48424757
_lock=threading.RLock()

SCHEMA_V7="""
CREATE TABLE account_positions(tenant TEXT NOT NULL,asset TEXT NOT NULL,account TEXT NOT NULL,
 balance INTEGER NOT NULL CHECK(typeof(balance)='integer'),PRIMARY KEY(tenant,asset,account));
INSERT INTO account_positions SELECT j.tenant,j.asset,p.account,SUM(p.delta)
 FROM postings p JOIN journals j ON j.seq=p.journal GROUP BY j.tenant,j.asset,p.account;
CREATE TRIGGER position_on_posting AFTER INSERT ON postings BEGIN
 INSERT INTO account_positions(tenant,asset,account,balance)
 SELECT tenant,asset,NEW.account,NEW.delta FROM journals WHERE seq=NEW.journal
 ON CONFLICT(tenant,asset,account) DO UPDATE SET balance=balance+excluded.balance;
END;
CREATE INDEX journal_tenant_head ON journals(tenant,seq);
CREATE INDEX posting_journal_readback ON postings(journal);
CREATE INDEX audit_tenant_head ON control_audit(tenant,seq);
CREATE INDEX open_customer_holds ON operations(tenant,asset,customer) WHERE state IN ('reserved','uncertain');
CREATE INDEX connection_open_work ON execution_intents(tenant,connection,state);
CREATE INDEX adjustment_intent_history ON payment_adjustments(tenant,intent,created);
CREATE TABLE reserve_assessments(tenant TEXT NOT NULL,reference TEXT NOT NULL,
 booked_units INTEGER NOT NULL,liability_units INTEGER NOT NULL,outcome TEXT NOT NULL
 CHECK(outcome IN ('matched','contradiction')),PRIMARY KEY(tenant,reference));
CREATE TRIGGER reserve_assessment_no_update BEFORE UPDATE ON reserve_assessments
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER reserve_assessment_no_delete BEFORE DELETE ON reserve_assessments
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

WRITER_TRIGGER = """
CREATE TRIGGER {trigger} BEFORE {action} ON {table}
BEGIN SELECT CASE WHEN hellabit_write_epoch_8() IS NOT 8
    THEN RAISE(ABORT, 'storage_writer_incompatible') END;
END
"""
WRITER_TRIGGER_V9 = WRITER_TRIGGER.replace("epoch_8() IS NOT 8", "epoch_9() IS NOT 9")
WRITER_TRIGGER_V10 = WRITER_TRIGGER.replace("epoch_8() IS NOT 8", "epoch_10() IS NOT 10")
WRITER_TRIGGER_V11 = WRITER_TRIGGER.replace("epoch_8() IS NOT 8", "epoch_11() IS NOT 11")
WRITER_TRIGGER_V12 = WRITER_TRIGGER.replace("epoch_8() IS NOT 8", "epoch_12() IS NOT 12")
ACCEPTANCE_SCHEMA = """
ALTER TABLE execution_intents ADD COLUMN accepted_reference TEXT;
CREATE INDEX intent_accepted_reference ON execution_intents(tenant,connection,accepted_reference)
 WHERE accepted_reference IS NOT NULL;
"""


def fence_legacy_writers(db):
    # SQLite enforces this in already-open older runtimes, even if they
    # never reread the user_version or acquire the application lock.
    tables = db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'").fetchall()
    for (table,) in tables:
        for action in ("INSERT", "UPDATE", "DELETE"):
            for version in range(8,VERSION):
                legacy = f"storage_v{version}_{action.lower()}_{table}".replace('"', '""')
                db.execute(f'DROP TRIGGER IF EXISTS "{legacy}"')
            trigger = f"storage_v{VERSION}_{action.lower()}_{table}"
            db.execute(WRITER_TRIGGER_V12.format(
                trigger='"' + trigger.replace('"', '""') + '"',
                table='"' + table.replace('"', '""') + '"',
                action=action,
            ))

def execute_script(db,script):
    statement=""
    for character in script:
        statement+=character
        if character==";" and sqlite3.complete_statement(statement):
            db.execute(statement);statement=""
    if statement.strip():db.execute(statement)

def columns(db,table):
    return {r[1] for r in db.execute("PRAGMA table_info("+table+")")}

def preflight(path, *, allow_pending=False):
    """Refuse a foreign or newer database before opening a writable connection."""
    if not path.exists():return
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
        version=db.execute("PRAGMA user_version").fetchone()[0]
        application=db.execute("PRAGMA application_id").fetchone()[0]
        if version>VERSION:raise Error("database_requires_newer_gateway",423)
        if application not in (0,APPLICATION_ID):raise Error("foreign_database",423)
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        legacy_tenant_only=tables=={'tenants'} and {'id','name','country','token_hash','created'}<=columns(db,'tenants')
        if tables and not legacy_tenant_only and not {'tenants','settings','journals','postings','operations'}<=tables:
            raise Error("foreign_database",423)
        if not allow_pending and 'settings' in tables:
            recovery=dict(db.execute("""SELECT key,value FROM settings
                WHERE key IN ('recovery_publish_pending','recovery_candidate_job','recovery_active_job')"""))
            if 'recovery_publish_pending' in recovery:
                raise Error('recovery_publication_pending',423)
            if 'recovery_candidate_job' in recovery and recovery['recovery_candidate_job']!=recovery.get('recovery_active_job'):
                raise Error('recovery_candidate_not_active',423)

def upgrade(book):
    from .control import AUDIT_SCHEMA,audit
    from . import operations,identity,inbox,indexer,metering,readiness,issuer
    from .events import event_key
    from .agent_policy import SCHEMA as AGENT_POLICY_SCHEMA
    from .console_commands import RECEIPTS_SCHEMA, SCHEMA as CONSOLE_COMMANDS_SCHEMA
    schemas={"operations":operations.SCHEMA,"identity":identity.SCHEMA,
             "provider-inbox":inbox.SCHEMA,"indexer":indexer.SCHEMA,
             "metering":metering.SCHEMA,"readiness":readiness.SCHEMA,"issuer-audit":issuer.SCHEMA,"audit-only":""}
    v6_checksum=hashlib.sha256((SCHEMA+AUDIT_SCHEMA+"".join(schemas.values())+"v6-generation-and-event-scope").encode()).hexdigest()
    v7_checksum=hashlib.sha256((v6_checksum+SCHEMA_V7+AGENT_POLICY_SCHEMA+"v7-reserve-authority-and-positions").encode()).hexdigest()
    v8_checksum=hashlib.sha256((v7_checksum+WRITER_TRIGGER+"v8-writer-admission").encode()).hexdigest()
    v9_checksum=hashlib.sha256((v8_checksum+CONSOLE_COMMANDS_SCHEMA+WRITER_TRIGGER_V9+
                             "v9-console-commands").encode()).hexdigest()
    v10_checksum=hashlib.sha256((v9_checksum+RECEIPTS_SCHEMA+WRITER_TRIGGER_V10+
                             "v10-console-receipts").encode()).hexdigest()
    v11_checksum=hashlib.sha256((v10_checksum+ACCEPTANCE_SCHEMA+WRITER_TRIGGER_V11+
                                 "v11-provider-acceptance").encode()).hexdigest()
    checksum=hashlib.sha256((v11_checksum+readiness.SCOPED_EVIDENCE_SCHEMA+WRITER_TRIGGER_V12+
                             "v12-scoped-readiness").encode()).hexdigest()
    known_checksums = {6:v6_checksum,7:v7_checksum,8:v8_checksum,9:v9_checksum,10:v10_checksum,11:v11_checksum,VERSION:checksum}
    with _lock,book.db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current=db.execute("PRAGMA user_version").fetchone()[0]
            if current>VERSION:raise Error("database_requires_newer_gateway",423)
            if current < VERSION and "state" in columns(db, "execution_intents"):
                if db.execute("SELECT 1 FROM execution_intents WHERE state='submitting' LIMIT 1").fetchone():
                    raise Error("finish_payout_attempts_before_upgrade",423)
            if current in known_checksums:
                row=db.execute("SELECT checksum FROM schema_migrations WHERE version=?",(current,)).fetchone()
                if not row or row[0]!=known_checksums[current]:raise Error("migration_definition_changed",423)
                if current == 7 and db.execute("""SELECT 1
                    FROM settlements scoped JOIN journals movement ON movement.seq=scoped.journal
                    JOIN settlements legacy ON legacy.tenant=scoped.tenant AND legacy.rail=scoped.rail
                      AND legacy.reference=json_extract(movement.body,'$.metadata.external_reference')
                      AND legacy.journal<>scoped.journal
                    JOIN journals original ON original.seq=legacy.journal AND original.asset=movement.asset
                    LIMIT 1""").fetchone():
                    raise Error("external_funding_identity_conflict_requires_review",423)
            else:
                execute_script(db,SCHEMA)
                for table,fields in {'tenants':{'bootstrap_revoked':'INTEGER NOT NULL DEFAULT 0'},
                                     'quotes':{'work_input':'TEXT','input_hash':'TEXT'}}.items():
                    known=columns(db,table)
                    for name,definition in fields.items():
                        if name not in known:db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
                db.execute("INSERT OR IGNORE INTO settings VALUES('sandbox_rail_key',?)",(secrets.token_hex(32),))
                db.execute("INSERT OR IGNORE INTO settings VALUES('schema_version','1')")
                execute_script(db,AUDIT_SCHEMA)
                db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied INTEGER NOT NULL)")
                old_adjustments=bool(columns(db,"payment_adjustments")) and "event_key" not in columns(db,"payment_adjustments")
                if old_adjustments:db.execute("ALTER TABLE payment_adjustments RENAME TO payment_adjustments_v5")
                for sql in schemas.values():execute_script(db,sql)
                additions={"execution_intents":{"connection":"TEXT NOT NULL DEFAULT ''"},
                           "api_principals":{"valid_after":"INTEGER NOT NULL DEFAULT 0","grant_id":"TEXT NOT NULL DEFAULT ''"},
                           "redemptions":{"source":"TEXT NOT NULL DEFAULT 'customer'"}}
                for table,fields in additions.items():
                    known=columns(db,table)
                    for name,definition in fields.items():
                        if name not in known:db.execute("ALTER TABLE "+table+" ADD COLUMN "+name+" "+definition)
                db.execute("UPDATE execution_intents SET connection='sandbox:'||rail||':'||asset WHERE connection=''")
                db.execute("""CREATE TRIGGER IF NOT EXISTS intent_connection_immutable
                    BEFORE UPDATE OF connection ON execution_intents
                    BEGIN SELECT RAISE(ABORT,'intent_connection_is_immutable'); END""")
                missing=db.execute("""SELECT p.* FROM payouts p LEFT JOIN execution_intents e
                    ON e.tenant=p.tenant AND e.payout=p.id WHERE p.state='pending' AND e.id IS NULL LIMIT 10001""").fetchall()
                if len(missing)>10000:raise Error('payout_migration_requires_bounded_batch',423)
                operation_writer=object.__new__(operations.Operations);operation_writer.book=book
                for row in missing:
                    operation_writer.enqueue(db,row['tenant'],row['id'],row['asset'],row['amount'],row['recipient'],migrated=True)
                for row in db.execute("SELECT * FROM api_principals WHERE grant_id='' OR id NOT IN (SELECT principal FROM api_grants)").fetchall():
                    grant=row["grant_id"] or "grant_"+secrets.token_hex(24)
                    db.execute("UPDATE api_principals SET grant_id=? WHERE id=?",(grant,row["id"]))
                    version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM api_grants WHERE principal=?',(row['id'],)).fetchone()[0]
                    body={k:row[k] for k in ("role","customer","expires","rate_limit","revoked","valid_after")};body['grant_id']=grant
                    db.execute("INSERT INTO api_grants VALUES(?,?,'imported',?,?)",(row["id"],version,canonical(body),book.clock()))
                    audit(book,db,row["tenant"],"api-grant-imported",row["id"],{"version":version,"grant_id":grant})
                if old_adjustments:
                    for row in db.execute("SELECT a.*,e.connection FROM payment_adjustments_v5 a JOIN execution_intents e ON e.tenant=a.tenant AND e.id=a.intent").fetchall():
                        key=event_key(row['tenant'],row['connection'],row['event_id'])
                        db.execute("INSERT INTO payment_adjustments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            tuple(row[k] for k in ('tenant','intent','event_id','kind','amount','reference','proof_hash','body','created'))+(key,row['connection'],row['event_id']))
                    old_count=db.execute("SELECT COUNT(*) FROM payment_adjustments_v5").fetchone()[0]
                    if db.execute("SELECT COUNT(*) FROM payment_adjustments").fetchone()[0]!=old_count:
                        raise Error("legacy_adjustment_binding_missing",423)
                if not db.execute("SELECT 1 FROM settings WHERE key='usage_totals_v1'").fetchone():
                    db.execute("INSERT OR REPLACE INTO usage_totals SELECT tenant,plan,period,SUM(micro) FROM usage_events GROUP BY tenant,plan,period")
                    db.execute("INSERT INTO settings VALUES('usage_totals_v1','1')")
                for row in db.execute('SELECT tenant,valid_until FROM issuer WHERE valid_until<>0').fetchall():
                    audit(book,db,row['tenant'],'reserve-evidence-revalidation-required','schema-v6',{'previous_valid_until':row['valid_until']})
                db.execute('UPDATE issuer SET valid_until=0')
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(6,v6_checksum,book.clock()))
            if current<7:
                execute_script(db,SCHEMA_V7+AGENT_POLICY_SCHEMA)
                for row in db.execute('SELECT tenant,valid_until FROM issuer WHERE valid_until<>0').fetchall():
                    audit(book,db,row['tenant'],'reserve-evidence-revalidation-required','schema-v7',{'previous_valid_until':row['valid_until']})
                db.execute('UPDATE issuer SET valid_until=0')
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(7,v7_checksum,book.clock()))
            if current<8:
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(8,v8_checksum,book.clock()))
            if current<9:
                execute_script(db,CONSOLE_COMMANDS_SCHEMA)
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(9,v9_checksum,book.clock()))
            if current<10:
                execute_script(db,RECEIPTS_SCHEMA)
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(10,v10_checksum,book.clock()))
            if current<11:
                execute_script(db,ACCEPTANCE_SCHEMA)
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(11,v11_checksum,book.clock()))
            if current<VERSION:
                execute_script(db,readiness.SCOPED_EVIDENCE_SCHEMA)
                fence_legacy_writers(db)
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(VERSION,checksum,book.clock()))
                db.execute("PRAGMA user_version="+str(VERSION));db.execute("PRAGMA application_id="+str(APPLICATION_ID))
                db.execute("UPDATE settings SET value=? WHERE key='schema_version'",(str(VERSION),))
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:db.execute("ROLLBACK")
            raise
    for name in schemas:setattr(book,"_hellabit_schema_"+name,True)
    book._identity_grant_migrated=True
