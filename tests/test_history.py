import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from hellabit.fixtures import SandboxProvider
from hellabit.history import export_history, export_payout_report
from hellabit.ledger import Ledger,Error
from hellabit.rails import SandboxBank
from hellabit.operations import Operations
from hellabit.payouts import Payouts

class HistoryTests(unittest.TestCase):
    def test_payout_report_reconciles_holds_and_returns_in_native_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = Ledger(root/"ledger.sqlite3")
            tenant = book.create_tenant("Reporting Merchant")["tenant"]
            SandboxBank(book).fund(tenant,"agent-01","CAD",1000,"opening")
            quote = book.create_quote(tenant,"CAD",1000,0)["quote"]
            book.run_report(tenant,book.reserve(tenant,"agent-01",quote,"report-work")["id"])
            payouts, ops, provider = Payouts(book), Operations(book), SandboxProvider(root/"provider.sqlite3")
            paid = payouts.request(tenant,"CAD",100,"private-recipient","sample-paid")
            held = payouts.request(tenant,"CAD",50,"private-recipient","sample-held")
            ops.process_one(tenant,provider,paid["intent"])
            provider.return_payment(ops.get(tenant,paid["intent"])["external_key"],"return-1",20)
            ops.reconcile(tenant,paid["intent"],provider)
            target = root/"report.csv"
            receipt = export_payout_report(book,tenant,target)
            with target.open(newline="") as file:
                rows = {r["payout_id"]:r for r in csv.DictReader(file)}
            self.assertEqual(receipt["records"],2)
            self.assertEqual(receipt["sha256"],hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual((rows[paid["id"]]["settled_units"],rows[paid["id"]]["returned_units"],
                              rows[paid["id"]]["net_paid_units"]),("100","20","80"))
            self.assertEqual((rows[held["id"]]["state"],rows[held["id"]]["held_units"]),("prepared","50"))
            self.assertEqual((rows[held["id"]]["asset"],rows[held["id"]]["decimal_places"]),("CAD","2"))
            self.assertNotIn(b"private-recipient",target.read_bytes())
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            with self.assertRaisesRegex(Error,"payout_report_target_exists"):
                export_payout_report(book,tenant,target)

    def test_complete_consistent_history_excludes_other_workspaces_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);book=Ledger(root/"book.sqlite3")
            workspace=book.create_tenant("Export Merchant");tenant=workspace["tenant"]
            other=book.create_tenant("Other Merchant")["tenant"]
            SandboxBank(book).fund(tenant,"agent-01","CAD",1000,"opening")
            q=book.create_quote(tenant,"CAD",1000,10)["quote"]
            op=book.reserve(tenant,"agent-01",q,"work")["id"];book.run_report(tenant,op)
            payout=Payouts(book).request(tenant,"CAD",50,"recipient","payout")
            lease=Operations(book).claim(tenant)["lease"]
            target=root/"history.jsonl";result=export_history(book,tenant,target)
            raw=target.read_bytes();rows=[json.loads(s) for s in raw.splitlines()]
            self.assertNotIn(other.encode(),raw);self.assertNotIn(workspace["api_key"].encode(),raw)
            self.assertNotIn(lease.encode(),raw);self.assertNotIn(b'"lease_hash"',raw)
            self.assertEqual(rows[-1]["content_sha256"],hashlib.sha256(b"".join(raw.splitlines(keepends=True)[:-1])).hexdigest())
            self.assertEqual(result["records"],len(rows)-2)
            postings=[r["row"] for r in rows if r.get("table")=="postings"]
            self.assertEqual(sum(p["delta"] for p in postings),0)
            self.assertTrue(any(r.get("table")=="execution_intents" and r["row"]["payout"]==payout["id"] for r in rows))
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            with self.assertRaisesRegex(Error,"history_target_exists"):export_history(book,tenant,target)
