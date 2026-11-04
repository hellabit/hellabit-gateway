import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from hellabit.ledger import Ledger
from hellabit.rails import SandboxBank


class ProcessRecoveryTests(unittest.TestCase):
    def test_sigkill_preserves_pending_work_and_rolls_back_partial_journal(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            path, marker = Path(tmp) / "ledger.sqlite3", Path(tmp) / "ready.json"
            book = Ledger(path); tenant = book.create_tenant("Recovery Merchant")["tenant"]
            SandboxBank(book).fund(tenant, "agent-01", "CAD", 1000, "bank-1")
            quote = book.create_quote(tenant, "CAD", 100, 1)["quote"]
            op = book.reserve(tenant, "agent-01", quote, "job-1")["id"]
            code = """
import sys,time
from pathlib import Path
from hellabit.ledger import Ledger
book=Ledger(sys.argv[1]); tenant=sys.argv[2]; op=sys.argv[3]
book.resolve(tenant,op,'uncertain')
with book.transaction() as db:
 book._post(db,tenant,'charge','unfinished','CAD',[('merchant',10),('customer:agent-01',-10)])
 Path(sys.argv[4]).write_text('ready')
 time.sleep(15)
"""
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
            child = subprocess.Popen([sys.executable, "-c", code, str(path), tenant, op, str(marker)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                until = time.monotonic() + 8
                while not marker.exists() and time.monotonic() < until and child.poll() is None: time.sleep(.02)
                self.assertTrue(marker.exists(), "child must reach open transaction")
                child.kill(); child.wait(timeout=5)
                reopened = Ledger(path)
                self.assertEqual(reopened.operation(tenant, op)["state"], "uncertain")
                asset = next(x for x in reopened.snapshot(tenant)["assets"] if x["id"] == "CAD")
                self.assertEqual((asset["balance"], asset["held"]), (1000, 100))
                self.assertEqual(reopened.reconcile(tenant)["journal_count"], 1)
                self.assertTrue(reopened.reconcile(tenant)["ok"])
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
                child.stderr.close()


if __name__ == "__main__": unittest.main()
