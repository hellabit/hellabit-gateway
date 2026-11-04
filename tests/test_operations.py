import concurrent.futures
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from hellabit.ledger import Ledger, Error
from hellabit.operations import Operations
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank
from hellabit.fixtures import SandboxProvider

ROOT = Path(__file__).resolve().parents[1]

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000
        self.book = Ledger(Path(self.tmp.name) / "ledger.sqlite3", clock=lambda: self.now)
        self.tenant = self.book.create_tenant("Recovery Merchant")["tenant"]
        SandboxBank(self.book).fund(self.tenant, "agent-01", "CAD", 10000, "opening-funds")
        quote = self.book.create_quote(self.tenant, "CAD", 1000, 10)["quote"]
        operation = self.book.reserve(self.tenant, "agent-01", quote, "first-purchase")["id"]
        self.book.run_report(self.tenant, operation)
        self.payouts = Payouts(self.book)
        self.ops = Operations(self.book)
        self.provider = SandboxProvider(Path(self.tmp.name) / "separate-provider.sqlite3")

    def tearDown(self): self.tmp.cleanup()

    def payout(self, key="same-payout"):
        return self.payouts.request(self.tenant, "CAD", 50, "merchant-account", key)

    def balance(self, account):
        with self.book.db() as db: return self.book._balance(db, self.tenant, "CAD", account)

    def test_concurrent_same_key_has_one_hold_and_one_intent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.payout(), range(20)))
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(len({r["intent"] for r in results}),1)
        self.assertEqual(self.ops.list(self.tenant)["counts"], {"prepared": 1})
        self.assertEqual(self.balance("merchant"), 940)
        self.assertEqual(self.balance("payout:" + results[0]["id"]), 50)

    def test_normal_worker_commits_one_payment_and_replays_no_mutation(self):
        p = self.payout()
        result = self.ops.process_one(self.tenant, self.provider)
        self.assertEqual(result["intent"]["state"], "confirmed")
        self.assertFalse(self.ops.process_one(self.tenant, self.provider)["processed"])
        self.assertEqual(self.ops.reconcile(self.tenant, p["intent"], self.provider)["state"], "confirmed")
        self.assertEqual(self.provider.accepted_count(), 1)
        self.assertEqual(self.balance("payout:" + p["id"]), 0)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_a_connection_has_one_inflight_writer_and_pauses_for_unknown_outcomes(self):
        first=self.payout("first-payment");second=self.payout("second-payment")
        job=self.ops.claim(self.tenant,intent=first["intent"])
        self.assertIsNone(self.ops.claim(self.tenant,intent=second["intent"]))
        self.ops._unknown(self.tenant,job["id"],job["lease"],"missing-reply")
        self.assertIsNone(self.ops.claim(self.tenant,intent=second["intent"]))
        self.assertEqual(self.ops.reconcile(self.tenant,first["intent"],self.provider)["state"],"unknown")
        self.assertEqual(self.provider.accepted_count(),0)
        self.assertEqual(self.balance("payout:"+second["id"]),50)

    def test_process_dies_after_provider_acceptance_then_lookup_recovers_once(self):
        p = self.payout()
        code = """
import os,sys
from hellabit.ledger import Ledger
from hellabit.operations import Operations
from hellabit.fixtures import SandboxProvider
b=Ledger(sys.argv[1],clock=lambda:1000)
provider=SandboxProvider(sys.argv[2],after_accept=lambda:os._exit(73))
Operations(b).process_one(sys.argv[3],provider)
"""
        env = {"PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        child = subprocess.run([sys.executable, "-c", code, str(self.book.path),
                                str(self.provider.path), self.tenant], cwd=ROOT, env=env,
                               capture_output=True, timeout=15)
        self.assertEqual(child.returncode, 73)
        self.assertEqual(self.provider.accepted_count(), 1)
        self.assertEqual(self.ops.get(self.tenant, p["intent"])["state"], "submitting")
        self.now = 1061
        reopened = Ledger(self.book.path, clock=lambda: self.now)
        recovered = Operations(reopened).reconcile(self.tenant, p["intent"], self.provider)
        self.assertEqual(recovered["state"], "confirmed")
        self.assertEqual(self.provider.accepted_count(), 1)
        with reopened.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='payout-paid'").fetchone()[0], 1)
        self.assertTrue(reopened.reconcile(self.tenant)["ok"])

    def test_missing_lookup_stays_unknown_and_is_never_resubmitted(self):
        class Unreachable:
            mode = "sandbox"
            calls = 0
            def lookup(self, key): return None
            def submit(self, job):
                self.calls += 1
                raise TimeoutError("No transport outcome")
        adapter = Unreachable()
        p = self.payout()
        self.assertEqual(self.ops.process_one(self.tenant, adapter)["intent"]["state"], "unknown")
        self.assertEqual(self.ops.reconcile(self.tenant, p["intent"], adapter)["lookup"], "not-found-inconclusive")
        self.assertFalse(self.ops.process_one(self.tenant, adapter)["processed"])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.balance("payout:" + p["id"]), 50)
        with self.assertRaisesRegex(Error, "reconcile_existing_submission"):
            self.payouts.sandbox_confirm(self.tenant, p["id"], "replacement-reference")

    def test_expired_worker_is_fenced_and_lookup_can_finish_original(self):
        p = self.payout(); job = self.ops.claim(self.tenant)
        receipt = self.provider.submit(job)
        self.now = 1061
        with self.assertRaisesRegex(Error, "worker_lease_expired"):
            self.ops.finish(self.tenant, job["id"], receipt, lease=job["lease"])
        self.assertEqual(self.balance("payout:" + p["id"]), 50)
        self.assertEqual(self.ops.reconcile(self.tenant, p["intent"], self.provider)["state"], "confirmed")
        with self.assertRaisesRegex(Error, "worker_fenced"):
            self.ops._unknown(self.tenant, job["id"], job["lease"], "stale-worker")

    def test_rejected_payment_releases_exact_hold_with_compensating_posting(self):
        class Rejected:
            mode = "sandbox"
            def lookup(self, key): return None
            def submit(self, job):
                return {"mode": "sandbox", "key": job["external_key"], "request_hash": job["request_hash"],
                        "status": "rejected", "reference": "bank-rejected", "amount": job["amount"],
                        "asset": job["asset"], "recipient": job["recipient"]}
        p = self.payout()
        self.assertEqual(self.ops.process_one(self.tenant, Rejected())["intent"]["state"], "failed")
        self.assertEqual(self.balance("merchant"), 990)
        self.assertEqual(self.balance("payout:" + p["id"]), 0)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_forged_receipt_cannot_book_payment_or_release_hold(self):
        class WrongAmount(SandboxProvider):
            def submit(self, job):
                receipt = super().submit(job); receipt["amount"] += 1
                return receipt
        p = self.payout()
        bad = WrongAmount(Path(self.tmp.name) / "bad-provider.sqlite3")
        self.assertEqual(self.ops.process_one(self.tenant, bad)["intent"]["state"], "unknown")
        self.assertEqual(self.balance("payout:" + p["id"]), 50)
        self.assertEqual(self.balance("merchant"), 940)

    def test_manual_sandbox_confirmation_fences_prepared_worker(self):
        p = self.payout()
        self.payouts.sandbox_confirm(self.tenant, p["id"], "manual-test-settlement")
        self.assertEqual(self.ops.get(self.tenant, p["intent"])["state"], "confirmed")
        self.assertFalse(self.ops.process_one(self.tenant, self.provider)["processed"])
        self.assertEqual(self.provider.accepted_count(), 0)

    def test_live_adapter_is_blocked_before_claim_or_io(self):
        class Live:
            mode = "production"
            def lookup(self, key): raise AssertionError("No live I/O permitted")
        p = self.payout()
        with self.assertRaisesRegex(Error, "live_connection_not_enrolled"):
            self.ops.process_one(self.tenant, Live())
        self.assertEqual(self.ops.get(self.tenant, p["intent"])["state"], "prepared")

    def test_slow_lookup_cannot_start_a_submission_after_lease_expiry(self):
        test=self
        class SlowLookup:
            mode="sandbox"
            calls=0
            def lookup(self,key):test.now=1061;return None
            def submit(self,job):self.calls+=1;raise AssertionError("Expired worker must not start a send")
        p=self.payout();adapter=SlowLookup()
        result=self.ops.process_one(self.tenant,adapter)
        self.assertFalse(result["submission_started"]);self.assertEqual(adapter.calls,0)
        self.assertEqual(result["intent"]["state"],"unknown")
        self.assertEqual(self.balance("payout:"+p["id"]),50)

    def test_callback_winning_during_lookup_prevents_a_later_worker_send(self):
        from hellabit.inbox import ProviderInbox
        from hellabit.ledger import canonical
        p=self.payout();intent=self.ops.get(self.tenant,p["intent"])
        secret=b"synthetic-backend-key"*2;inbox=ProviderInbox(self.book,"bound-provider",secret)
        inbox.bind(self.tenant,"bank");test=self
        receipt={"mode":"sandbox","key":intent["external_key"],"request_hash":intent["request_hash"],
                 "status":"settled","reference":"callback-settlement","amount":intent["amount"],
                 "asset":intent["asset"],"recipient":intent["recipient"]}
        class CallbackFirst:
            mode="sandbox"
            def lookup(self,key):
                raw=canonical({"id":"callback-event","tenant":test.tenant,"occurred":1000,"receipt":receipt}).encode()
                inbox.receive(test.tenant,raw,"1000",inbox.signature(secret,"1000",raw));inbox.apply(test.tenant,"callback-event")
                return None
            def submit(self,job):raise AssertionError("Settled intent must not be submitted")
        result=self.ops.process_one(self.tenant,CallbackFirst())
        self.assertEqual(result["intent"]["state"],"confirmed");self.assertFalse(result["submission_started"])
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_immutable_terms_tenant_isolation_and_no_lease_secret_in_snapshot(self):
        p = self.payout(); job = self.ops.claim(self.tenant)
        with self.book.db() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE execution_intents SET amount=1 WHERE id=?", (p["intent"],))
        other = self.book.create_tenant("Another Merchant")["tenant"]
        with self.assertRaisesRegex(Error, "intent_not_found"):
            self.ops.get(other, p["intent"])
        self.assertNotIn(job["lease"], str(self.ops.list(self.tenant)))
        self.assertNotIn("lease_hash", str(self.ops.list(self.tenant)))
