import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from hellabit.history import export_history
from hellabit.ledger import Ledger,Error
from hellabit.rails import SandboxBank
from hellabit.operations import Operations
from hellabit.payouts import Payouts

class HistoryTests(unittest.TestCase):
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
