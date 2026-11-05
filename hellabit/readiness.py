"""Evidence tracking and observed state; never an automatic legal determination."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import zipfile
from . import __version__
from .control import audit,ensure_schema,verify_audit
from .ledger import Error,identifier
from .operations import Operations
from .outcomes import AMBIGUOUS
from .indexer import EventIndexer
from .distribution import source_names

ROOT=Path(__file__).resolve().parents[1]
REQUIREMENTS=(
    ("entity","Operating entity","Confirm the exact registered entity and its authority."),
    ("legal-ca","Canadian operating role","Review the actual Canadian functions and funds flow."),
    ("legal-us","United States operating role","Review the intended US functions, states and customer types."),
    ("program","Institution/program approval","Bind written provider acceptance to the exact entity, account and funds flow."),
    ("identity","Identity and TLS deployment","Enroll issuer keys, subject grants, operator MFA policy and the deployment certificate."),
    ("custody","Signer and credential custody","Assign signing authority, limits, custody, rotation and recovery ownership."),
    ("recovery","Operational recovery","Enroll encrypted backup custody, retention, restoration targets and incident ownership."),
    ("security","Independent security review","Record review of the exact source, contracts and supported assets."),
    ("canary","Authorized live proof","Verify a separately authorized bounded payment, settlement and refund."),
    ("issuance","Branded issuance program","For issuance, establish legal issuer, reserve custody, redemption and governance."),
)
CATALOG={item[0]:item for item in REQUIREMENTS}
SCHEMA="""
CREATE TABLE IF NOT EXISTS readiness_evidence(
 tenant TEXT NOT NULL,control TEXT NOT NULL,reference TEXT NOT NULL,document_hash TEXT NOT NULL,
 expires INTEGER NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(tenant,control,reference));
CREATE TRIGGER IF NOT EXISTS readiness_evidence_no_update BEFORE UPDATE ON readiness_evidence
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS readiness_evidence_no_delete BEFORE DELETE ON readiness_evidence
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

def local_build():
    """Compare the accepted package with current source before calling it current."""
    file=ROOT/"evidence/terminal-result.json"
    pending={"status":"not-current-or-not-verified","version":__version__,"source_hash":None}
    try:
        status="packaged-source-matches-proof"
        if file.is_file():
            if file.is_symlink():return pending
            result=json.loads(file.read_text());archive_name=result.get("archive","")
            if not result.get("ok") or not re.fullmatch(r"release/hellabit-gateway-"+re.escape(__version__)+r"-[0-9a-f]{12}\.zip",archive_name):
                return pending
            archive=ROOT/archive_name
            if archive.is_symlink() or not archive.is_file() or not archive.resolve().is_relative_to(ROOT.resolve()):return pending
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=result.get("archiveSha256"):return pending
            with zipfile.ZipFile(archive) as z:
                manifest=json.loads(z.read("SOURCE-MANIFEST.json"));proof=json.loads(z.read("VERIFIED.json"))
            if proof["sourceTreeSha256"]!=result["sourceTreeSha256"]:return pending
            status="locally-verified-current"
        else:
            for name in ("VERIFIED.json","SOURCE-MANIFEST.json"):
                p=ROOT/name
                if p.is_symlink() or not p.is_file():return pending
            proof=json.loads((ROOT/"VERIFIED.json").read_text())
            manifest=json.loads((ROOT/"SOURCE-MANIFEST.json").read_text())
        if proof.get("productionReady") is not False or proof.get("externalTransactions")!=0:return pending
        hashes={}
        for row in manifest["files"]:
            name=row["path"]
            if name=="VERIFIED.json" or name in ("compiled/AttestedStablecoin.json","compiled/PaymentRouter.json"):continue
            parts=Path(name).parts
            if (not isinstance(name,str) or Path(name).is_absolute() or ".." in parts or name in hashes or
                    any(p in parts for p in (".private","evidence","release","node_modules"))):return pending
            current=ROOT/name
            if (current.is_symlink() or not current.is_file() or not current.resolve().is_relative_to(ROOT.resolve()) or
                    hashlib.sha256(current.read_bytes()).hexdigest()!=row["sha256"]):return pending
            hashes[name]=row["sha256"]
        if set(hashes)!=source_names(ROOT):return pending
        source_hash=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
        if source_hash!=proof["sourceTreeSha256"]:return pending
        return {"status":status,"version":__version__,"source_hash":source_hash,"production_ready":False,
                "proof_scope":"source-content comparison, not an independent security or legal approval"}
    except (OSError,ValueError,TypeError,KeyError,zipfile.BadZipFile):return pending

class Readiness:
    def __init__(self,book):
        self.book=book
        ensure_schema(book,"readiness",SCHEMA)
        self.operations=Operations(book)
        EventIndexer(book)

    def record(self,tenant,control,reference,document_hash,expires):
        if control not in CATALOG:raise Error("unknown_readiness_control",400)
        identifier(reference)
        if not isinstance(document_hash,str) or not re.fullmatch(r"[0-9a-f]{64}",document_hash):
            raise Error("invalid_evidence_hash",400)
        if type(expires) is not int or not self.book.clock()<expires<=self.book.clock()+366*86400:
            raise Error("invalid_evidence_expiry",400)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            old=db.execute("SELECT * FROM readiness_evidence WHERE tenant=? AND control=? AND reference=?",
                           (tenant,control,reference)).fetchone()
            if old:
                if old["document_hash"]!=document_hash or old["expires"]!=expires:
                    raise Error("readiness_evidence_conflict",409)
                return {"recorded":True,"externally_verified":False,"replayed":True}
            db.execute("INSERT INTO readiness_evidence VALUES(?,?,?,?,?,?)",
                       (tenant,control,reference,document_hash,expires,self.book.clock()))
            audit(self.book,db,tenant,"external-evidence-reported",control,
                  {"document_hash":document_hash,"expires":expires,"verification":"not-established"})
        return {"recorded":True,"externally_verified":False,"financial_activation_enabled":False}

    def report(self,tenant):
        from .backup import capacity
        from .recovery import BATCH
        with self.book.db() as db:
            db.execute('BEGIN')
            check=self.book.reconcile(tenant,db=db)
            audit_check=verify_audit(self.book,tenant,db=db)
            ops=self.operations.list(tenant,db=db)
            self.book.tenant(db,tenant)
            evidence=[dict(r) for r in db.execute("SELECT * FROM readiness_evidence WHERE tenant=? ORDER BY created DESC,rowid DESC",(tenant,))]
            chains=[dict(r) for r in db.execute("""SELECT id,chain_id,cursor,finalized,halted,updated
                FROM chain_bindings WHERE tenant=?""",(tenant,))]
            for chain in chains:
                chain["binding_ref_hash"]=hashlib.sha256(chain.pop("id").encode()).hexdigest()[:12]
            overdue=db.execute("""SELECT COUNT(*) FROM execution_intents WHERE tenant=?
                AND state='submitting' AND lease_until<=?""",(tenant,self.book.clock())).fetchone()[0]
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            principals=db.execute("SELECT COUNT(*) FROM api_principals WHERE tenant=? AND revoked=0 AND expires>?",
                                  (tenant,self.book.clock())).fetchone()[0] if "api_principals" in tables else 0
            retired=bool(db.execute("SELECT 1 FROM settings WHERE key='recovery_retired'").fetchone())
            quarantine=retired or bool(db.execute("SELECT 1 FROM settings WHERE key IN ('recovery_quarantine','recovery_publish_pending')").fetchone())
            unresolved=[dict(r) for r in db.execute(f"""SELECT id,connection,asset,amount,created FROM execution_intents
                WHERE tenant=? AND ({AMBIGUOUS} OR state='submitting' AND lease_until<=?)
                ORDER BY created LIMIT 30""",(tenant,self.book.clock()))]
            recovery_history={'intents':sum(ops['counts'].values()),'adjustments':db.execute('SELECT COUNT(*) FROM payment_adjustments WHERE tenant=?',(tenant,)).fetchone()[0],
                              'batch_size':BATCH,'fixed_row_limit':None}
        controls=[]
        for id_,title,action in REQUIREMENTS:
            row=next((r for r in evidence if r["control"]==id_ and r["expires"]>self.book.clock()),None)
            expired=any(r["control"]==id_ for r in evidence)
            controls.append({"id":id_,"title":title,"status":"reported-unverified" if row else "expired" if expired else "missing",
                             "next_action":action,"external_verification_established":False})
        unknown=ops["counts"].get("unknown",0)+overdue
        backup_capacity=capacity(self.book)
        for row in unresolved:row['age_seconds']=max(0,self.book.clock()-row['created'])
        return {"schema":"hellabit.readiness-report.v1","version":__version__,"observed_at":self.book.clock(),
                "role_profile":"merchant-controlled-software","intended_markets":["CA","US"],
                "source_build":local_build(),
                "machine_observations":{"ledger_balanced":check["ok"],"journal_count":check["journal_count"],
                    "control_audit_valid":audit_check["ok"],"control_audit_entries":audit_check["entries_checked"],
                    "backup_capacity":backup_capacity,"retained_recovery_history":recovery_history,"recovery_quarantine":quarantine,"previous_writer_retired":retired,"unresolved_holds":unresolved,
                    "unknown_or_overdue_submissions":unknown,"payout_counts":ops["counts"],
                    "enrolled_api_principals":principals,"chain_bindings":chains},
                "external_controls":controls,"legal_role_resolved":False,"live_payments_enabled":False,
                "activation_blocked":True,
                "personal_or_account_details_included":False,
                "next_action":"This primary is retired. Inspect its preserved history and operate only the new handover path with fresh access." if retired else
                    "Keep this restored copy isolated. Reconcile missing commands and provider movements and fence the prior writer before promotion." if quarantine else
                    "Reconcile the original unknown payment identity." if unknown else
                    "Resolve the exact operating role and provider program before enrolling a live connection."}

    def packet(self,tenant):
        report=self.report(tenant)
        return {"schema":"hellabit.provider-responsibility-packet.v1","version":__version__,
                "proposed_role":report["role_profile"],"intended_markets":report["intended_markets"],
                "operator_identity_included":False,"legal_determination_included":False,
                "functions_to_review":["customer budget accounting","payment instruction handling",
                    "API work orchestration","fund custody","transaction signing","issuance and redemption"],
                "required_responsibility_assignment":["merchant","software operator","settlement institution",
                    "identity provider","credential custodian","incident owner","legal issuer if applicable"],
                "unresolved_controls":report["external_controls"],
                "sources":["https://www.bankofcanada.ca/2026/06/criteria-for-registering-payment-service-providers/",
                    "https://fintrac-canafe.canada.ca/msb-esm/msb-eng",
                    "https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-certain-business-models"],
                "statement":"Publishing software and recording documents do not establish a legal exemption or provider approval."}
