"""Private, consistent financial history; no credentials or signing authority."""
import hashlib
import os
from pathlib import Path
import tempfile
from .backup import _publish_file
from .ledger import Error,canonical

TABLES=("customers","assets","journals","quotes","operations","settlements","issuer",
        "reserve_proofs","rail_events","redemptions","issuer_requests","deliveries","payouts",
        "execution_observations","payment_adjustments","control_audit","api_grants",
        "readiness_evidence","provider_bindings","provider_inbox","enrollments",
        "billing_plans","usage_events","usage_invoices","usage_totals","reserve_observations","reserve_assessments",
        "agent_policies","agent_daily_usage","agent_approvals","agent_reservations")

def export_history(book,tenant,destination):
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():raise Error("history_target_exists",409)
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    digest=hashlib.sha256();count=0
    with tempfile.TemporaryDirectory(prefix="hellabit-history-",dir=destination.parent) as tmp:
        path=Path(tmp)/"history.jsonl"
        with book.db() as db,path.open("xb") as output:
            path.chmod(0o600);db.execute("BEGIN")
            book.tenant(db,tenant)
            def line(value):
                raw=(canonical(value)+"\n").encode();digest.update(raw);output.write(raw)
            line({"schema":"hellabit.financial-history.v1","workspace":tenant,"observed_at":book.clock(),
                  "confidential":True,"contains_credentials":False,"restores_execution_authority":False})
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            queries=[("tenants","SELECT id,name,country,created FROM tenants WHERE id=?"),
                     ("postings","SELECT p.journal,p.account,p.delta FROM postings p JOIN journals j ON j.seq=p.journal WHERE j.tenant=? ORDER BY p.rowid")]
            for table in TABLES:
                if table not in tables:continue
                if table=="api_grants":
                    queries.append((table,"SELECT g.* FROM api_grants g JOIN api_principals p ON p.id=g.principal WHERE p.tenant=? ORDER BY g.rowid"))
                else:queries.append((table,f"SELECT * FROM {table} WHERE tenant=? ORDER BY rowid"))
            for table,columns in (("execution_intents","id,tenant,payout,rail,asset,amount,recipient,external_key,request_hash,state,attempts,reference,evidence_hash,last_error,created,updated,connection"),
                                  ("api_principals","id,tenant,issuer,subject_hash,role,customer,expires,revoked,rate_limit,valid_after")):
                if table in tables:queries.append((table,f"SELECT {columns} FROM {table} WHERE tenant=? ORDER BY rowid"))
            for table,query in queries:
                for row in db.execute(query,(tenant,)):
                    line({"table":table,"row":dict(row)});count+=1
            db.execute("COMMIT")
            checksum=digest.hexdigest()
            output.write((canonical({"end":True,"records":count,"content_sha256":checksum})+"\n").encode())
            output.flush();os.fsync(output.fileno())
        _publish_file(path,destination)
    return {"exported":True,"records":count,"content_sha256":checksum,"credentials_included":False,
            "confidential":True,"execution_authority_included":False}
