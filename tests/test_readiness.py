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
        for control,_,_ in REQUIREMENTS:
            self.ready.record(self.tenant,control,"synthetic-document","a"*64,2000)
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
        with self.assertRaisesRegex(Error,"readiness_evidence_conflict"):
            self.ready.record(self.tenant,"entity","report-1","b"*64,1100)
        other=self.book.create_tenant("Other synthetic merchant")["tenant"]
        self.assertTrue(all(c["status"]=="missing" for c in self.ready.report(other)["external_controls"]))
        self.now=1101
        entity=next(c for c in self.ready.report(self.tenant)["external_controls"] if c["id"]=="entity")
        self.assertEqual(entity["status"],"expired")

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
