from pathlib import Path
import tempfile
import unittest
from hellabit.ledger import Ledger,Error
from hellabit.issuer import Issuer
from hellabit.rails import SandboxBank

class IssuerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=1000
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Issuer Merchant")["tenant"]
        self.issuer=Issuer(self.book);self.bank=SandboxBank(self.book)

    def tearDown(self):self.tmp.cleanup()

    def test_a_small_new_deposit_cannot_refresh_a_stale_reserve_pool(self):
        def deposit(amount,reference):
            return self.issuer.attest(self.tenant,self.bank.event(self.tenant,"issuer-reserve","USD",amount,reference))
        deposit(100000,"original-reserve")
        self.now+=366*86400
        state=deposit(1,"tiny-new-deposit")
        self.assertFalse(state["reserve_current"])
        with self.assertRaisesRegex(Error,"reserve_evidence_stale"):
            self.issuer.mint(self.tenant,"agent-01",10000,"mint-with-stale-pool")
        bad=self.bank.event(self.tenant,"issuer-reserve-balance","USD",1,"incomplete-balance")
        with self.assertRaisesRegex(Error,"reserve_balance_mismatch"):self.issuer.attest_balance(self.tenant,bad)
        full=self.bank.event(self.tenant,"issuer-reserve-balance","USD",100001,"full-current-balance")
        self.assertTrue(self.issuer.attest_balance(self.tenant,full)["reserve_current"])

    def test_merchant_earnings_have_a_complete_redemption_lifecycle(self):
        self.issuer.attest(self.tenant,self.bank.event(self.tenant,"issuer-reserve","USD",10000,"reserves"))
        self.issuer.mint(self.tenant,"agent-01",10000000,"mint")
        asset="issued:"+self.tenant
        q=self.book.create_quote(self.tenant,asset,1000000,10000)["quote"]
        op=self.book.reserve(self.tenant,"agent-01",q,"paid-work")["id"];self.book.run_report(self.tenant,op)
        self.issuer.request_redemption(self.tenant,None,990000,"merchant-redemption",source="merchant")
        self.assertEqual(self.issuer.state(self.tenant)["pending_redemptions"],990000)
        event=self.bank.event(self.tenant,"redemption:merchant-redemption","USD",99,"redemption-payment","debit")
        self.issuer.confirm_redemption(self.tenant,"merchant-redemption",event)
        state=self.issuer.state(self.tenant)
        self.assertEqual(state["pending_redemptions"],0);self.assertEqual(state["reserve"],99010000)
        with self.book.db() as db:self.assertEqual(self.book._balance(db,self.tenant,asset,"merchant"),0)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])
