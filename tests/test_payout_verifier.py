import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.verify_payout import verify

ROOT = Path(__file__).resolve().parents[1]


class PayoutVerifierTests(unittest.TestCase):
    def setUp(self):
        self.sample = json.loads((ROOT / "examples/payout-recovery.json").read_text())

    def report(self):
        return verify(json.dumps(self.sample).encode())

    def test_original_payment_and_repeated_return_record_are_counted_once(self):
        lookup = self.sample["lookup"]
        lookup["records"].append(copy.deepcopy(lookup["records"][0]))
        lookup["returns"].append(copy.deepcopy(lookup["returns"][0]))
        report = self.report()
        self.assertEqual((report["verdict"], report["accepted_payment_count"]), ("pass", 1))
        self.assertEqual((report["payment_state"], report["unsettled_command_units"]),
                         ("partially-returned", 600))
        self.assertNotIn(self.sample["command"]["key"], json.dumps(report))
        self.assertFalse(report["provider_authenticated_by_verifier"])

    def test_pending_and_incomplete_lookup_cannot_claim_settlement(self):
        lookup = self.sample["lookup"]
        lookup["records"][0]["state"] = "pending"
        lookup["returns"] = []
        report = self.report()
        self.assertEqual((report["verdict"], report["payment_state"], report["observed_settled_units"]),
                         ("pass", "pending", 0))
        lookup["coverage"] = "partial"
        lookup["records"] = []
        report = self.report()
        self.assertEqual((report["verdict"], report["payment_state"], report["unsettled_command_units"]),
                         ("review", "unknown", None))

    def test_duplicate_payment_changed_command_and_return_conflict_fail(self):
        lookup = self.sample["lookup"]
        lookup["records"].append({**lookup["records"][0], "payment_id":"different-payment"})
        self.sample["attempts"][0]["command"]["key"] = "replacement-key"
        lookup["returns"].append({**lookup["returns"][0], "amount_units":700})
        report = self.report()
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(set(report["violations"]), {
            "multiple_accepted_payment_ids", "original_command_identity_changed", "conflicting_return_identity"})
        self.assertIsNone(report["unsettled_command_units"])

    def test_cli_runs_from_empty_directory_and_does_not_open_a_ledger(self):
        command = [sys.executable, str(ROOT / "scripts/verify_payout.py")]
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(command, cwd=tmp, text=True, capture_output=True, check=True, timeout=5)
            self.assertEqual(json.loads(proc.stdout)["verdict"], "pass")
            self.assertEqual(list(Path(tmp).iterdir()), [])
            bad = Path(tmp) / "malformed.json"
            bad.write_text('{"schema":1,"schema":2}')
            proc = subprocess.run(command + ["--evidence", str(bad)], cwd=tmp,
                                  text=True, capture_output=True, timeout=5)
            self.assertEqual((proc.returncode, json.loads(proc.stdout)), (2, {"error":"duplicate_json_key"}))
