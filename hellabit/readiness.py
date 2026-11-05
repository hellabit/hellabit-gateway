"""Evidence tracking and observed state; never an automatic legal determination."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import zipfile
from . import __version__
from .control import audit,ensure_schema,verify_audit
from .ledger import Error,digest,identifier
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
SCOPED_EVIDENCE_SCHEMA="""
ALTER TABLE readiness_evidence ADD COLUMN scope_sha256 TEXT;
ALTER TABLE enrollments ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
CREATE TABLE readiness_profiles(
 seq INTEGER PRIMARY KEY,tenant TEXT NOT NULL,profile_sha256 TEXT NOT NULL,changed INTEGER NOT NULL);
CREATE INDEX readiness_profile_head ON readiness_profiles(tenant,seq);
CREATE TRIGGER readiness_profiles_no_update BEFORE UPDATE ON readiness_profiles
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER readiness_profiles_no_delete BEFORE DELETE ON readiness_profiles
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TABLE readiness_revocations(
 tenant TEXT NOT NULL,control TEXT NOT NULL,reference TEXT NOT NULL,reason TEXT NOT NULL,revoked INTEGER NOT NULL,
 PRIMARY KEY(tenant,control,reference),
 FOREIGN KEY(tenant,control,reference) REFERENCES readiness_evidence(tenant,control,reference));
CREATE TRIGGER readiness_revocations_no_update BEFORE UPDATE ON readiness_revocations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER readiness_revocations_no_delete BEFORE DELETE ON readiness_revocations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""
REVOCATION_REASONS=("withdrawn","conditions-changed","incorrect")

def _sha256(value):
    return isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value) is not None

def _source_identity():
    try:
        hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_names(ROOT)}
        return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    except (OSError,ValueError,TypeError):
        return None

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

    def _scope(self,db,tenant,source):
        from .fixtures import SandboxProvider
        workspace=self.book.tenant(db,tenant)
        profile=db.execute("""SELECT seq,profile_sha256,changed FROM readiness_profiles
            WHERE tenant=? ORDER BY seq DESC LIMIT 1""",(tenant,)).fetchone()
        enrollments=[list(r) for r in db.execute("""SELECT rail,body,revision FROM enrollments
            WHERE tenant=? ORDER BY rail""",(tenant,))]
        chains=[list(r) for r in db.execute("""SELECT id,chain_id,contract,start_block FROM chain_bindings
            WHERE tenant=? ORDER BY id""",(tenant,))]
        issuer=db.execute("SELECT name,symbol,currency FROM issuer WHERE tenant=?",(tenant,)).fetchone()
        provider=SandboxProvider.read_binding(db)
        terms={"kind":"hellabit.readiness-scope.v1","workspace":tenant,"country":workspace["country"],
               "source":source,"profile":list(profile) if profile else None,"enrollments":enrollments,
               "chains":chains,"issuer":list(issuer) if issuer else None,
               "sandbox_provider":[provider["identity"],provider["generation"]] if provider else None}
        return {"sha256":digest(terms) if source else None,"source_tree_sha256":source,
                "profile_declared":profile is not None,"profile_changed_at":profile["changed"] if profile else None}

    def set_profile(self,tenant,profile_sha256,expected_scope_sha256):
        if not _sha256(profile_sha256) or not _sha256(expected_scope_sha256):
            raise Error("invalid_readiness_scope_hash",400)
        source=_source_identity()
        with self.book.transaction() as db:
            self.book.assert_payment_writes(db)
            scope=self._scope(db,tenant,source)
            if scope["sha256"]!=expected_scope_sha256:
                raise Error("readiness_scope_changed",409)
            current=db.execute("SELECT profile_sha256 FROM readiness_profiles WHERE tenant=? ORDER BY seq DESC LIMIT 1",
                               (tenant,)).fetchone()
            if current and current[0]==profile_sha256:
                return {"scope":scope,"replayed":True,"financial_activation_enabled":False}
            db.execute("INSERT INTO readiness_profiles(tenant,profile_sha256,changed) VALUES(?,?,?)",
                       (tenant,profile_sha256,self.book.clock()))
            scope=self._scope(db,tenant,source)
            audit(self.book,db,tenant,"readiness-profile-declared","operating-profile",
                  {"scope_sha256":scope["sha256"],"profile_sha256":profile_sha256})
            return {"scope":scope,"financial_activation_enabled":False}

    def record(self,tenant,control,reference,document_hash,expires,scope_sha256=None):
        if not isinstance(control,str) or control not in CATALOG:raise Error("unknown_readiness_control",400)
        identifier(reference)
        if not _sha256(document_hash):raise Error("invalid_evidence_hash",400)
        if scope_sha256 is not None and not _sha256(scope_sha256):raise Error("invalid_readiness_scope_hash",400)
        if type(expires) is not int:raise Error("invalid_evidence_expiry",400)
        source=_source_identity() if scope_sha256 else None
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            self.book.assert_payment_writes(db)
            old=db.execute("SELECT * FROM readiness_evidence WHERE tenant=? AND control=? AND reference=?",
                           (tenant,control,reference)).fetchone()
            if old:
                if (old["document_hash"],old["expires"],old["scope_sha256"])!=(document_hash,expires,scope_sha256):
                    raise Error("readiness_evidence_conflict",409)
                return {"recorded":True,"externally_verified":False,"replayed":True,"financial_activation_enabled":False}
            now=self.book.clock()
            if not now<expires<=now+366*86400:raise Error("invalid_evidence_expiry",400)
            if scope_sha256 and scope_sha256!=self._scope(db,tenant,source)["sha256"]:
                raise Error("readiness_scope_changed",409)
            db.execute("""INSERT INTO readiness_evidence(tenant,control,reference,document_hash,expires,created,scope_sha256)
                VALUES(?,?,?,?,?,?,?)""",(tenant,control,reference,document_hash,expires,now,scope_sha256))
            audit(self.book,db,tenant,"external-evidence-reported",control,
                  {"reference_sha256":digest(reference),"document_hash":document_hash,"expires":expires,
                   "scope_sha256":scope_sha256,"verification":"not-established"})
        return {"recorded":True,"externally_verified":False,"financial_activation_enabled":False}

    def revoke(self,tenant,control,reference,reason):
        if not isinstance(control,str) or control not in CATALOG:raise Error("unknown_readiness_control",400)
        identifier(reference)
        if reason not in REVOCATION_REASONS:raise Error("invalid_readiness_revocation_reason",400)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            self.book.assert_payment_writes(db)
            key=(tenant,control,reference)
            if not db.execute("SELECT 1 FROM readiness_evidence WHERE tenant=? AND control=? AND reference=?",key).fetchone():
                raise Error("readiness_evidence_not_found",404)
            old=db.execute("SELECT reason FROM readiness_revocations WHERE tenant=? AND control=? AND reference=?",key).fetchone()
            if old:
                if old["reason"]!=reason:raise Error("readiness_revocation_conflict",409)
                return {"revoked":True,"replayed":True,"financial_activation_enabled":False}
            db.execute("INSERT INTO readiness_revocations VALUES(?,?,?,?,?)",(*key,reason,self.book.clock()))
            audit(self.book,db,tenant,"readiness-evidence-revoked",control,
                  {"reference_sha256":digest(reference),"reason":reason})
            return {"revoked":True,"financial_activation_enabled":False}

    def report(self,tenant):
        from .backup import capacity
        from .recovery import BATCH
        build=local_build()
        source=build["source_hash"] or _source_identity()
        with self.book.db() as db:
            db.execute('BEGIN')
            check=self.book.reconcile(tenant,db=db)
            audit_check=verify_audit(self.book,tenant,db=db)
            ops=self.operations.list(tenant,db=db)
            observed=ops["observed_at"]
            scope=self._scope(db,tenant,source)
            # The most recently recorded evidence supersedes older records even
            # after expiry or withdrawal. Never fall back to a stale approval.
            evidence={r["control"]:dict(r) for r in db.execute("""SELECT e.control,e.scope_sha256,e.created,e.expires,
                v.reason,v.revoked FROM readiness_evidence e LEFT JOIN readiness_revocations v
                USING(tenant,control,reference) WHERE e.rowid IN (
                    SELECT MAX(rowid) FROM readiness_evidence WHERE tenant=? GROUP BY control
                )""",(tenant,))}
            chains=[dict(r) for r in db.execute("""SELECT id,chain_id,cursor,finalized,halted,updated
                FROM chain_bindings WHERE tenant=?""",(tenant,))]
            for chain in chains:
                chain["binding_ref_hash"]=hashlib.sha256(chain.pop("id").encode()).hexdigest()[:12]
            overdue=db.execute("""SELECT COUNT(*) FROM execution_intents WHERE tenant=?
                AND state='submitting' AND lease_until<=?""",(tenant,observed)).fetchone()[0]
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            principals=db.execute("SELECT COUNT(*) FROM api_principals WHERE tenant=? AND revoked=0 AND expires>?",
                                  (tenant,observed)).fetchone()[0] if "api_principals" in tables else 0
            retired=bool(db.execute("SELECT 1 FROM settings WHERE key='recovery_retired'").fetchone())
            quarantine=retired or bool(db.execute("SELECT 1 FROM settings WHERE key IN ('recovery_quarantine','recovery_publish_pending')").fetchone())
            unresolved=[dict(r) for r in db.execute(f"""SELECT id,connection,asset,amount,created,last_error FROM execution_intents
                WHERE tenant=? AND ({AMBIGUOUS} OR state='submitting' AND lease_until<=?)
                ORDER BY created LIMIT 30""",(tenant,observed))]
            recovery_history={'intents':sum(ops['counts'].values()),'adjustments':db.execute('SELECT COUNT(*) FROM payment_adjustments WHERE tenant=?',(tenant,)).fetchone()[0],
                              'batch_size':BATCH,'fixed_row_limit':None}
        controls=[]
        for id_,title,action in REQUIREMENTS:
            row=evidence.get(id_)
            status=("missing" if not row else "revoked" if row["revoked"] is not None else
                    "expired" if row["expires"]<=observed else "unscoped" if not row["scope_sha256"] else
                    "scope-unavailable" if not scope["sha256"] else
                    "scope-changed" if row["scope_sha256"]!=scope["sha256"] else "reported-unverified")
            controls.append({"id":id_,"title":title,"status":status,"next_action":action,
                "external_verification_established":False,"evidence":None if not row else
                    {"recorded_at":row["created"],"expires_at":row["expires"],"revoked_at":row["revoked"],
                     "revocation_reason":row["reason"]}})
        unknown=ops["counts"].get("unknown",0)+overdue
        backup_capacity=capacity(self.book)
        for row in unresolved:row['age_seconds']=max(0,observed-row['created'])
        return {"schema":"hellabit.readiness-report.v1","version":__version__,"observed_at":observed,
                "role_profile":"merchant-controlled-software","intended_markets":["CA","US"],
                "source_build":build,"evidence_scope":scope,
                "machine_observations":{"ledger_balanced":check["ok"],"journal_count":check["journal_count"],
                    "control_audit_valid":audit_check["ok"],"control_audit_entries":audit_check["entries_checked"],
                    "backup_capacity":backup_capacity,"retained_recovery_history":recovery_history,"recovery_quarantine":quarantine,"previous_writer_retired":retired,"unresolved_holds":unresolved,
                    "unknown_or_overdue_submissions":unknown,"payout_counts":ops["counts"],"connection_impact":ops["connection_impact"],
                    "enrolled_api_principals":principals,"chain_bindings":chains},
                "external_controls":controls,"legal_role_resolved":False,"live_payments_enabled":False,
                "activation_blocked":True,
                "personal_or_account_details_included":False,
                "next_action":"This primary is retired. Inspect its preserved history and operate only the new handover path with fresh access." if retired else
                    "Keep this restored copy isolated. Reconcile missing commands and provider movements and fence the prior writer before promotion." if quarantine else
                    "Review the original reference and any returns before clearing a disputed payout." if
                    any(row["last_error"]=="provider_reference_conflict" for row in unresolved) else
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
                "unresolved_controls":[{k:v for k,v in item.items() if k!="evidence"} for item in report["external_controls"]],
                "sources":["https://www.bankofcanada.ca/2026/06/criteria-for-registering-payment-service-providers/",
                    "https://fintrac-canafe.canada.ca/msb-esm/msb-eng",
                    "https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-certain-business-models"],
                "statement":"Publishing software and recording documents do not establish a legal exemption or provider approval."}
