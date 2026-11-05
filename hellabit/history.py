"""Private, consistent financial history; no credentials or signing authority."""
import csv
import hashlib
import os
from pathlib import Path
import tempfile
from .backup import _publish_file
from .ledger import Error,canonical
from .outcomes import INTENT_STATE

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
            for table,columns in (("execution_intents","id,tenant,payout,rail,asset,amount,recipient,external_key,request_hash,state,attempts,reference,evidence_hash,last_error,created,updated,connection,accepted_reference"),
                                  ("console_commands","tenant,action,sequence,id,terms,state,result"),
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


def export_payout_report(book, tenant, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise Error("payout_report_target_exists", 409)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    count = 0
    with tempfile.TemporaryDirectory(prefix="hellabit-payout-report-", dir=destination.parent) as tmp:
        path = Path(tmp) / "report.csv"
        with book.db() as db, path.open("x", encoding="utf-8", newline="") as output:
            path.chmod(0o600)
            db.execute("BEGIN")
            book.tenant(db, tenant)
            rows = db.execute(f"""WITH intents AS (
                SELECT *, {INTENT_STATE} AS display_state FROM execution_intents WHERE tenant=?
            ) SELECT i.id AS intent_id,i.payout AS payout_id,i.connection,i.asset,a.decimals AS decimal_places,
                i.display_state AS state,i.amount AS requested_units,COALESCE(pos.balance,0) AS held_units,
                CASE WHEN i.state='confirmed' THEN i.amount ELSE 0 END AS settled_units,
                COALESCE((SELECT SUM(x.amount) FROM payment_adjustments x
                    WHERE x.tenant=i.tenant AND x.intent=i.id),0) AS returned_units,
                COALESCE(i.accepted_reference,i.reference) AS provider_reference,
                i.evidence_hash AS evidence_sha256,i.created AS created_unix,i.updated AS updated_unix
            FROM intents i JOIN assets a ON a.tenant=i.tenant AND a.id=i.asset
            LEFT JOIN account_positions pos ON pos.tenant=i.tenant AND pos.asset=i.asset
                AND pos.account='payout:'||i.payout
            ORDER BY i.created,i.id""", (tenant,))
            writer = csv.DictWriter(output, ("intent_id","payout_id","connection","asset","decimal_places","state",
                                    "requested_units","held_units","settled_units","returned_units","net_paid_units",
                                    "provider_reference","evidence_sha256","created_unix","updated_unix"))
            writer.writeheader()
            for row in rows:
                record = dict(row)
                record["net_paid_units"] = row["settled_units"] - row["returned_units"]
                writer.writerow(record)
                count += 1
            output.flush()
            os.fsync(output.fileno())
        with path.open("rb") as output:
            checksum = hashlib.file_digest(output, "sha256").hexdigest()
        _publish_file(path, destination)
    return {"schema":"hellabit.payout-report.v1","exported":True,"records":count,
            "sha256":checksum,"scope":"sandbox-ledger","financial_writes":0}
