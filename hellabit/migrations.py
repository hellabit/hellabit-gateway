"""Atomic, versioned storage upgrades before any component serves a request."""
import hashlib
import secrets
import sqlite3
import threading
from contextlib import closing
from .ledger import Error,canonical,SCHEMA

VERSION=6
APPLICATION_ID=0x48424757
_lock=threading.RLock()

def execute_script(db,script):
    statement=""
    for character in script:
        statement+=character
        if character==";" and sqlite3.complete_statement(statement):
            db.execute(statement);statement=""
    if statement.strip():db.execute(statement)

def columns(db,table):
    return {r[1] for r in db.execute("PRAGMA table_info("+table+")")}

def preflight(path):
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

def upgrade(book):
    from .control import AUDIT_SCHEMA,audit
    from . import operations,identity,inbox,indexer,metering,readiness,issuer
    from .events import event_key
    schemas={"operations":operations.SCHEMA,"identity":identity.SCHEMA,
             "provider-inbox":inbox.SCHEMA,"indexer":indexer.SCHEMA,
             "metering":metering.SCHEMA,"readiness":readiness.SCHEMA,"issuer-audit":issuer.SCHEMA,"audit-only":""}
    checksum=hashlib.sha256((SCHEMA+AUDIT_SCHEMA+"".join(schemas.values())+"v6-generation-and-event-scope").encode()).hexdigest()
    with _lock,book.db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current=db.execute("PRAGMA user_version").fetchone()[0]
            if current>VERSION:raise Error("database_requires_newer_gateway",423)
            if current==VERSION:
                row=db.execute("SELECT checksum FROM schema_migrations WHERE version=?",(VERSION,)).fetchone()
                if not row or row[0]!=checksum:raise Error("migration_definition_changed",423)
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
                db.execute("INSERT INTO schema_migrations VALUES(?,?,?)",(VERSION,checksum,book.clock()))
                db.execute("PRAGMA user_version="+str(VERSION));db.execute("PRAGMA application_id="+str(APPLICATION_ID))
                db.execute("UPDATE settings SET value=? WHERE key='schema_version'",(str(VERSION),))
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:db.execute("ROLLBACK")
            raise
    for name in schemas:setattr(book,"_hellabit_schema_"+name,True)
    book._identity_grant_migrated=True
