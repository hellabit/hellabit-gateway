from pathlib import Path
import tempfile
import unittest
from hellabit.ledger import Ledger,Error,canonical
from hellabit.operations import Operations
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank
from hellabit.fixtures import SandboxProvider
from hellabit.inbox import ProviderInbox

class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=1000
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Inbox Merchant")["tenant"]
        SandboxBank(self.book).fund(self.tenant,"agent-01","CAD",1000,"initial")
        q=self.book.create_quote(self.tenant,"CAD",500,5)["quote"]
        op=self.book.reserve(self.tenant,"agent-01",q,"work")["id"];self.book.run_report(self.tenant,op)
        self.payout=Payouts(self.book).request(self.tenant,"CAD",100,"merchant-account","payout")
        self.ops=Operations(self.book);self.job=self.ops.claim(self.tenant)
        self.provider=SandboxProvider(Path(self.tmp.name)/"provider.sqlite3")
        self.receipt=self.provider.submit(self.job)
        self.secret=b"reference-fixture-key-only"*2
        self.inbox=ProviderInbox(self.book,"bank-fixture",self.secret)
        self.inbox.bind(self.tenant,"bank")

    def tearDown(self):self.tmp.cleanup()

    def envelope(self,id_="event-1",receipt=None):
        return canonical({"id":id_,"tenant":self.tenant,"occurred":1000,"receipt":receipt or self.receipt}).encode()

    def receive(self,raw):
        timestamp=str(self.now)
        return self.inbox.receive(self.tenant,raw,timestamp,self.inbox.signature(self.secret,timestamp,raw))

    def test_signed_event_can_finish_inflight_payment_and_duplicate_is_idempotent(self):
        raw=self.envelope();self.receive(raw)
        self.assertTrue(self.receive(raw)["replayed"])
        self.assertEqual(self.inbox.apply(self.tenant,"event-1")["state"],"confirmed")
        self.assertTrue(self.inbox.apply(self.tenant,"event-1")["replayed"])
        with self.book.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='payout-paid'").fetchone()[0],1)

    def test_wrong_signature_and_stale_signed_timestamp_rejected_before_storage(self):
        raw=self.envelope()
        with self.assertRaisesRegex(Error,"invalid_webhook_signature"):
            self.inbox.receive(self.tenant,raw,str(self.now),"0"*64)
        with self.assertRaisesRegex(Error,"webhook_timestamp_expired"):
            self.inbox.receive(self.tenant,raw,"1",self.inbox.signature(self.secret,"1",raw))
        with self.book.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM provider_inbox").fetchone()[0],0)

    def test_reused_event_identity_with_changed_payload_is_a_conflict(self):
        self.receive(self.envelope())
        changed={**self.receipt,"amount":101}
        with self.assertRaisesRegex(Error,"webhook_identity_conflict"):
            self.receive(self.envelope(receipt=changed))

    def test_late_pending_event_does_not_downgrade_settled_payment(self):
        self.receive(self.envelope());self.inbox.apply(self.tenant,"event-1")
        self.receive(self.envelope("late-pending",{**self.receipt,"status":"pending"}))
        self.assertEqual(self.inbox.apply(self.tenant,"late-pending")["state"],"ignored-after-terminal")
        self.assertEqual(self.ops.get(self.tenant,self.payout["intent"])["state"],"confirmed")

    def test_wrong_amount_and_other_workspace_cannot_book(self):
        self.receive(self.envelope(receipt={**self.receipt,"amount":999}))
        with self.assertRaisesRegex(Error,"provider_receipt_binding_mismatch"):
            self.inbox.apply(self.tenant,"event-1")
        other=self.book.create_tenant("Other Merchant")["tenant"]
        raw=self.envelope()
        with self.assertRaisesRegex(Error,"webhook_workspace_mismatch"):
            self.inbox.receive(other,raw,str(self.now),self.inbox.signature(self.secret,str(self.now),raw))
        self.assertEqual(self.ops.get(self.tenant,self.payout["intent"])["state"],"submitting")

    def test_signature_is_provider_bound_even_when_a_key_is_reused(self):
        other=ProviderInbox(self.book,"other-provider",self.secret)
        raw=self.envelope();timestamp=str(self.now)
        with self.assertRaisesRegex(Error,"invalid_webhook_signature"):
            other.receive(self.tenant,raw,timestamp,self.inbox.signature(self.secret,timestamp,raw))
        other.receive(self.tenant,raw,timestamp,other.signature(self.secret,timestamp,raw))
        with self.assertRaisesRegex(Error,"provider_connection_binding_mismatch"):
            other.apply(self.tenant,"event-1")
        with self.assertRaisesRegex(Error,"provider_binding_immutable"):
            other.bind(self.tenant,"bank")
        self.assertEqual(self.ops.get(self.tenant,self.payout["intent"])["state"],"submitting")
