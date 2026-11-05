import json
from http.cookiejar import CookieJar
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import HTTPCookieProcessor, Request, build_opener
from hellabit.ledger import Ledger
from hellabit.rails import SandboxBank


class ProcessRecoveryTests(unittest.TestCase):
    def test_console_keeps_default_origin_and_recovers_draft_on_different_port(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            book = Ledger(path)
            tenant = book.create_tenant("Restart Merchant")["tenant"]
            SandboxBank(book).fund(tenant, "agent-01", "CAD", 200, "restart-funding")
            quote = book.create_quote(tenant, "CAD", 200, 0)["quote"]
            book.run_report(tenant, book.reserve(tenant, "agent-01", quote, "restart-work")["id"])
            base = "/api/v1/workspaces/" + tenant + "/commands"

            def launch(*options):
                child = subprocess.Popen([sys.executable, str(root / "scripts/run.py"),
                                          "--state", str(path), *options], cwd=root,
                                         text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                def stop():
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                    child.stdout.close()
                    child.stderr.close()
                self.addCleanup(stop)
                return child, stop

            def origin(child):
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=10), "Console startup timed out")
                line = child.stdout.readline()
                self.assertTrue(line, child.stderr.read() if child.poll() is not None else "No startup receipt")
                return json.loads(line)["url"]

            def session(url):
                opener = build_opener(HTTPCookieProcessor(CookieJar()))
                with opener.open(url, timeout=5) as response:
                    html = response.read().decode()
                csrf = re.search(r'name="hellabit-csrf" content="([^"]+)"', html).group(1)
                def read(tail="", body=None):
                    req = Request(url + base + tail, data=json.dumps(body).encode() if body else None,
                                  headers={"Origin":url, "X-Hellabit-CSRF":csrf, "Content-Type":"application/json"})
                    with opener.open(req, timeout=5) as response:
                        return json.load(response)
                return read

            first, stop_first = launch()
            original = origin(first)
            terms = {"asset":"CAD","recipient":"restart-recipient","amount":100}
            draft = session(original)("/payout/prepare", {"terms":terms,"after":None})
            first.kill()
            first.wait(timeout=5)
            stop_first()
            same, stop_same = launch()
            self.assertEqual(origin(same), original)
            self.assertEqual(session(original)()["payout"], draft)
            stop_same()

            with socket.socket() as blocker:
                blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                blocker.bind(("127.0.0.1", int(original.rsplit(":", 1)[1])))
                blocker.listen(1)
                conflict, stop_conflict = launch()
                stdout, stderr = conflict.communicate(timeout=10)
                self.assertEqual((conflict.returncode, stdout), (1, ""))
                self.assertIn("address is in use", stderr)
                stop_conflict()
                override, stop_override = launch("--port", "0")
                alternate = origin(override)
                self.assertNotEqual(alternate, original)
                read = session(alternate)
                self.assertEqual(read()["payout"], draft)
                receipt = read("/payout/confirm", {"id":draft["id"]})
                self.assertEqual(receipt["state"], "recorded")
                stop_override()

            final, stop_final = launch()
            self.assertEqual(origin(final), original)
            read = session(original)
            self.assertEqual(read()["payout"], receipt)
            self.assertEqual(read("/payout/confirm", {"id":draft["id"]}), receipt)
            self.assertEqual(len(book.snapshot(tenant)["payouts"]), 1)
            stop_final()

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
