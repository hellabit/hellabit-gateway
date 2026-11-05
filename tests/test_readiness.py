import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from hellabit import __version__
from hellabit.ledger import Ledger,Error
from hellabit.readiness import Readiness,REQUIREMENTS,local_build,source_names

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=1000
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Synthetic merchant")["tenant"]
        self.ready=Readiness(self.book)

    def tearDown(self):self.tmp.cleanup()

    def test_all_reported_documents_cannot_clear_legal_or_payment_activation(self):
        scope=self.ready.report(self.tenant)["evidence_scope"]["sha256"]
        for control,_,_ in REQUIREMENTS:
            self.ready.record(self.tenant,control,"synthetic-document","a"*64,2000,scope)
        result=self.ready.report(self.tenant)
        self.assertTrue(all(c["status"]=="reported-unverified" for c in result["external_controls"]))
        self.assertFalse(result["legal_role_resolved"]);self.assertFalse(result["live_payments_enabled"])
        self.assertTrue(result["activation_blocked"])
        self.assertNotIn("outbound_sales_enabled",result)
        packet=self.ready.packet(self.tenant)
        self.assertFalse(packet["operator_identity_included"]);self.assertFalse(packet["legal_determination_included"])
        self.assertNotIn(self.tenant,json.dumps(packet));self.assertNotIn("synthetic-document",json.dumps(packet))

    def test_evidence_expiry_conflict_and_workspace_isolation(self):
        self.ready.record(self.tenant,"entity","report-1","a"*64,1100)
        self.assertEqual(self.control()["status"],"unscoped")
        with self.assertRaisesRegex(Error,"readiness_evidence_conflict"):
            self.ready.record(self.tenant,"entity","report-1","b"*64,1100)
        other=self.book.create_tenant("Other synthetic merchant")["tenant"]
        self.assertTrue(all(c["status"]=="missing" for c in self.ready.report(other)["external_controls"]))
        self.now=1101
        entity=next(c for c in self.ready.report(self.tenant)["external_controls"] if c["id"]=="entity")
        self.assertEqual(entity["status"],"expired")

    def control(self,name="entity"):
        return next(c for c in self.ready.report(self.tenant)["external_controls"] if c["id"]==name)

    def test_newer_record_stays_authoritative_after_expiry_and_withdrawal(self):
        scope=self.ready.report(self.tenant)["evidence_scope"]["sha256"]
        self.ready.record(self.tenant,"entity","long-lived","a"*64,2000,scope)
        self.ready.record(self.tenant,"entity","replacement","b"*64,1100,scope)
        self.now=1100
        self.assertEqual(self.control()["status"],"expired")
        self.ready.revoke(self.tenant,"entity","replacement","conditions-changed")
        self.assertEqual(self.control()["status"],"revoked")
        self.assertEqual(self.control()["evidence"]["revoked_at"],1100)
        self.assertTrue(self.ready.record(self.tenant,"entity","replacement","b"*64,1100,scope)["replayed"])
        self.assertTrue(self.ready.revoke(self.tenant,"entity","replacement","conditions-changed")["replayed"])
        with self.assertRaisesRegex(Error,"readiness_revocation_conflict"):
            self.ready.revoke(self.tenant,"entity","replacement","incorrect")
        self.assertEqual(self.control()["status"],"revoked")
        self.assertTrue(self.ready.report(self.tenant)["machine_observations"]["control_audit_valid"])

    def test_scope_follows_profile_and_drafts_without_reviving_old_evidence(self):
        def scope():return self.ready.report(self.tenant)["evidence_scope"]["sha256"]
        initial=scope()
        declared=self.ready.set_profile(self.tenant,"a"*64,initial)["scope"]
        self.assertTrue(declared["profile_declared"])
        self.assertNotEqual(initial,declared["sha256"])
        self.assertTrue(self.ready.set_profile(self.tenant,"a"*64,declared["sha256"])["replayed"])
        with self.assertRaisesRegex(Error,"readiness_scope_changed"):
            self.ready.set_profile(self.tenant,"b"*64,initial)
        terms={"institution_reference":"test-bank","account_reference":"test-account","currency":"CAD"}
        self.book.prepare_enrollment(self.tenant,"bank",terms)
        reviewed=scope()
        self.ready.record(self.tenant,"program","program-doc","c"*64,2000,reviewed)
        self.book.prepare_enrollment(self.tenant,"bank",terms)
        self.assertEqual(self.control("program")["status"],"reported-unverified")
        # Changing an account and then restoring its old draft is still a new scope.
        self.book.prepare_enrollment(self.tenant,"bank",{**terms,"account_reference":"replacement"})
        self.book.prepare_enrollment(self.tenant,"bank",terms)
        self.assertNotEqual(scope(),reviewed)
        self.assertEqual(self.control("program")["status"],"scope-changed")
        with self.assertRaisesRegex(Error,"readiness_scope_changed"):
            self.ready.record(self.tenant,"program","late-doc","d"*64,2000,reviewed)
        current=scope()
        self.ready.record(self.tenant,"program","renewed-doc","e"*64,2000,current)
        second=self.ready.set_profile(self.tenant,"b"*64,current)["scope"]["sha256"]
        self.ready.set_profile(self.tenant,"a"*64,second)
        self.assertEqual(self.control("program")["status"],"scope-changed")
        packet=json.dumps(self.ready.packet(self.tenant))
        for private in ("test-account","program-doc",current,"e"*64):
            self.assertNotIn(private,packet)

    def test_operational_audit_tampering_is_detected_independently_of_balances(self):
        self.ready.record(self.tenant,"entity","report-1","a"*64,1100)
        self.assertTrue(self.ready.report(self.tenant)["machine_observations"]["control_audit_valid"])
        with self.book.db() as db:
            db.execute("DROP TRIGGER control_audit_no_update")
            db.execute("UPDATE control_audit SET body='altered' WHERE tenant=?",(self.tenant,))
        observed=self.ready.report(self.tenant)["machine_observations"]
        self.assertTrue(observed["ledger_balanced"]);self.assertFalse(observed["control_audit_valid"])

class SourceProofTests(unittest.TestCase):
    def test_unlisted_notes_and_operator_instructions_never_enter_the_public_set(self):
        (self.root/"docs").mkdir();(self.root/"docs/internal.md").write_text("unpublished internal note")
        (self.root/"AGENTS.md").write_text("local operator instructions")
        self.assertNotIn("docs/internal.md",source_names(self.root));self.assertNotIn("AGENTS.md",source_names(self.root))
        self.assertEqual(local_build()["status"],"packaged-source-matches-proof")
        manifest=json.loads((self.root/"PUBLIC_FILES.json").read_text());manifest["files"].append("AGENTS.md")
        (self.root/"PUBLIC_FILES.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,"unsafe_public_path"):source_names(self.root)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name in ("README.md","LICENSE"):(self.root/name).write_text("synthetic public source\n")
        (self.root/"hellabit").mkdir();(self.root/"hellabit/example.py").write_text("pass\n")
        (self.root/"PUBLIC_FILES.json").write_text(json.dumps({"schema":"hellabit.public-files.v1","files":["PUBLIC_FILES.json","README.md","LICENSE","hellabit/example.py"]}))
        self.hashes={n:hashlib.sha256((self.root/n).read_bytes()).hexdigest() for n in source_names(self.root)}
        self.source_hash=hashlib.sha256(json.dumps(self.hashes,sort_keys=True).encode()).hexdigest()
        self.proof={"sourceTreeSha256":self.source_hash,"pythonTests":1,"sdkTests":1,"contractChecks":1,
                    "browserChecks":1,"productionReady":False,"externalTransactions":0}
        self.manifest={"files":[{"path":n,"sha256":h} for n,h in self.hashes.items()]}
        (self.root/"VERIFIED.json").write_text(json.dumps(self.proof))
        (self.root/"SOURCE-MANIFEST.json").write_text(json.dumps(self.manifest))
        self.mock=patch("hellabit.readiness.ROOT",self.root);self.mock.start()

    def tearDown(self):self.mock.stop();self.tmp.cleanup()

    def test_portable_source_proof_compares_current_bytes_and_detects_unlisted_changes(self):
        self.assertEqual(local_build()["status"],"packaged-source-matches-proof")
        (self.root/"hellabit/unreviewed.py").write_text("unverified change")
        self.assertEqual(local_build()["status"],"not-current-or-not-verified")
        (self.root/"hellabit/unreviewed.py").unlink()
        (self.root/"README.md").write_text("different")
        self.assertEqual(local_build()["status"],"not-current-or-not-verified")

    def test_local_receipt_requires_exact_archive_hash_and_current_source(self):
        (self.root/"release").mkdir();(self.root/"evidence").mkdir()
        name="release/hellabit-gateway-"+__version__+"-"+self.source_hash[:12]+".zip"
        archive=self.root/name
        with zipfile.ZipFile(archive,"w") as z:
            z.writestr("SOURCE-MANIFEST.json",json.dumps(self.manifest));z.writestr("VERIFIED.json",json.dumps(self.proof))
        receipt={"ok":True,"archive":name,"archiveSha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
                 "sourceTreeSha256":self.source_hash}
        (self.root/"evidence/terminal-result.json").write_text(json.dumps(receipt))
        self.assertEqual(local_build()["status"],"locally-verified-current")
        archive.write_bytes(archive.read_bytes()+b"changed")
        self.assertEqual(local_build()["status"],"not-current-or-not-verified")

    def test_private_members_or_parent_symlinks_cannot_match_source_proof(self):
        self.manifest["files"].append({"path":".private/secret","sha256":"a"*64})
        (self.root/"SOURCE-MANIFEST.json").write_text(json.dumps(self.manifest))
        self.assertEqual(local_build()["status"],"not-current-or-not-verified")
        self.manifest["files"].pop();(self.root/"SOURCE-MANIFEST.json").write_text(json.dumps(self.manifest))
        with tempfile.TemporaryDirectory() as outside:
            external=Path(outside)/"example.py";external.write_text("pass\n")
            (self.root/"hellabit/example.py").unlink();(self.root/"hellabit").rmdir()
            (self.root/"hellabit").symlink_to(outside,target_is_directory=True)
            self.assertEqual(local_build()["status"],"not-current-or-not-verified")
