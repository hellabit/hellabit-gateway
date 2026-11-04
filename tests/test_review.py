import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from hellabit.ledger import Error
from hellabit.readiness import SOURCE_ROOT_FILES

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("review_builder",ROOT/"scripts/review.py")
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)
sha=lambda b:hashlib.sha256(b).hexdigest()

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.archive=Path(self.tmp.name)/"source.zip"
        self.files={n:b"synthetic public content\n" for n in SOURCE_ROOT_FILES}
        self.files["package.json"]=b'{"version":"0.4.0"}\n'
        self.files["hellabit/ledger.py"]=b"first_line=1\nsecond_line=2\n"
        self.files["contracts/Test.sol"]=b"// synthetic\n"
        self.files["web/app.js"]=b"// synthetic interface\n"

    def tearDown(self):self.tmp.cleanup()

    def capsule(self,change=None):
        source_hash=sha(json.dumps({n:sha(b) for n,b in self.files.items()},sort_keys=True).encode())
        files={**self.files,"VERIFIED.json":json.dumps({"sourceTreeSha256":source_hash}).encode()}
        manifest={"files":[{"path":n,"sha256":sha(b),"bytes":len(b)} for n,b in files.items()]}
        files["SOURCE-MANIFEST.json"]=json.dumps(manifest).encode()
        if change:change(files)
        with zipfile.ZipFile(self.archive,"w") as z:
            for n,b in files.items():z.writestr(n,b)
        return sha(self.archive.read_bytes()),source_hash

    def test_nine_file_bundle_preserves_paths_hashes_and_original_line_numbers(self):
        digest,source_hash=self.capsule()
        first,manifest=review.build_review(self.archive,digest)
        second,_=review.build_review(self.archive,digest)
        self.assertEqual(first,second);self.assertEqual(manifest["source_tree_sha256"],source_hash)
        self.assertFalse(manifest["google_review_completed"]);self.assertFalse(manifest["real_money_enabled"])
        with zipfile.ZipFile(io.BytesIO(first)) as z:
            self.assertEqual(len(z.namelist()),9)
            text=z.read("01_PAYMENT_CORE.txt").decode()
            self.assertIn("FILE: hellabit/ledger.py",text)
            self.assertIn("L00002 second_line=2",text)
            recorded=json.loads(z.read("09_MANIFEST.json"))
            self.assertEqual(recorded["source_files"]["hellabit/ledger.py"],sha(self.files["hellabit/ledger.py"]))

    def test_changed_source_or_archive_bytes_fail_before_producing_review(self):
        digest,_=self.capsule(lambda files:files.update({"hellabit/ledger.py":b"changed"}))
        with self.assertRaisesRegex(Error,"review_source_hash_mismatch"):review.build_review(self.archive,digest)
        with self.assertRaisesRegex(Error,"review_archive_hash_mismatch"):review.build_review(self.archive,"0"*64)

    def test_private_or_traversal_paths_cannot_enter_a_shareable_review(self):
        for name in (".private/operator.json","../outside","hellabit/../../outside"):
            with self.subTest(name=name):
                digest,_=self.capsule(lambda files:files.update({name:b"must not share"}))
                with self.assertRaisesRegex(Error,"unsafe_review_source_path"):review.build_review(self.archive,digest)

    def test_unbounded_or_unsafe_text_inputs_are_rejected(self):
        digest,_=self.capsule(lambda files:files.update({"web/oversize.js":b"a"*2_000_001}))
        with self.assertRaisesRegex(Error,"review_archive_limits"):review.build_review(self.archive,digest)
        self.files["hellabit/ledger.py"]=b"unsupported\x00binary"
        digest,_=self.capsule()
        with self.assertRaisesRegex(Error,"review_source_not_shareable_text"):review.build_review(self.archive,digest)
