from pathlib import Path
import tempfile
import unittest
from hellabit.ledger import Ledger,Error
from hellabit.issuer import Issuer
from hellabit.rails import SandboxBank
from hellabit.ledger import digest

class IssuerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=1000
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Issuer Merchant")["tenant"]
        self.issuer=Issuer(self.book);self.bank=SandboxBank(self.book)

    def tearDown(self):self.tmp.cleanup()

    def test_unpaid_queue_keeps_old_claims_and_paid_history_separate(self):
        self.issuer.attest(self.tenant,self.bank.deposit_event(
            self.tenant,'issuer-reserve','USD',1000,'queue-deposit'))
        self.issuer.mint(self.tenant,'agent-01',1_000_000,'queue-mint')
        ids=[self.issuer.request_redemption(self.tenant,'agent-01',10000,f'claim-{n}')['request']
             for n in range(22)]
        debit=self.bank.event(self.tenant,'redemption:'+ids[-1],'USD',1,'last-paid','debit')
        self.issuer.confirm_redemption(self.tenant,ids[-1],debit)
        state=self.issuer.state(self.tenant)
        first=state['claim_pages']['pending']
        second=self.issuer.redemption_page(self.tenant,'pending',first['next_cursor'])
        self.assertEqual(state['pending_redemption_count'],21)
        self.assertEqual([row['id'] for row in first['claims']+second['claims']],ids[:-1])
        self.assertEqual([row['id'] for row in state['claim_pages']['paid']['claims']],ids[-1:])
        self.assertEqual(self.issuer.redemption(self.tenant,ids[0])['state'],'pending')

    def test_unused_old_balance_evidence_cannot_refresh_reserves(self):
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,'issuer-reserve','USD',10000,'initial-deposit'))
        unused=self.bank.balance_event(self.tenant,'USD',10000,'unused-observation')
        self.now+=366*86400
        with self.assertRaisesRegex(Error,'balance_observation_stale'):self.issuer.attest_balance(self.tenant,unused)
        with self.assertRaisesRegex(Error,'reserve_evidence_stale'):self.issuer.mint(self.tenant,'agent-01',10000,'stale-mint')

    def test_balance_observation_sequence_and_authenticated_time_are_enforced(self):
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,'issuer-reserve','USD',10000,'initial-deposit'))
        first=self.bank.balance_event(self.tenant,'USD',10000,'observation-1')
        second=self.bank.balance_event(self.tenant,'USD',10000,'observation-2')
        self.issuer.attest_balance(self.tenant,second)
        with self.assertRaisesRegex(Error,'reserve_observation_out_of_order'):self.issuer.attest_balance(self.tenant,first)
        tampered={**second,'body':{**second['body'],'observed_at':self.now+1}}
        with self.assertRaises(Error):self.issuer.attest_balance(self.tenant,tampered)
        expiry=self.issuer.state(self.tenant)['valid_until'];self.now+=30
        self.assertTrue(self.issuer.attest_balance(self.tenant,second)['replayed'])
        self.assertEqual(self.issuer.state(self.tenant)['valid_until'],expiry)

    def test_redemption_identity_distinguishes_customer_named_merchant_from_proceeds(self):
        self.book.add_customer(self.tenant,'merchant')
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,'issuer-reserve','USD',10000,'initial-deposit'))
        self.issuer.mint(self.tenant,'merchant',1000000,'mint-merchant-customer')
        self.issuer.request_redemption(self.tenant,'merchant',10000,'same-command')
        with self.assertRaisesRegex(Error,'redemption_reference_conflict'):
            self.issuer.request_redemption(self.tenant,None,10000,'same-command',source='merchant')
        with self.book.db() as db:
            db.execute('UPDATE redemptions SET request_hash=? WHERE id=?',(digest(['merchant',10000]),'same-command'))
        self.assertTrue(self.issuer.request_redemption(self.tenant,'merchant',10000,'same-command')['replayed'])
        with self.assertRaisesRegex(Error,'redemption_reference_conflict'):
            self.issuer.request_redemption(self.tenant,None,10000,'same-command',source='merchant')

    def test_a_small_new_deposit_cannot_refresh_a_stale_reserve_pool(self):
        def deposit(amount,reference):
            return self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,"issuer-reserve","USD",amount,reference))
        deposit(100000,"original-reserve")
        self.now+=366*86400
        state=deposit(1,"tiny-new-deposit")
        self.assertFalse(state["reserve_current"])
        with self.assertRaisesRegex(Error,"reserve_evidence_stale"):
            self.issuer.mint(self.tenant,"agent-01",10000,"mint-with-stale-pool")
        bad=self.bank.balance_event(self.tenant,"USD",1,"incomplete-balance")
        self.assertTrue(self.issuer.attest_balance(self.tenant,bad)['reserve_reconciliation_required'])
        full=self.bank.balance_event(self.tenant,"USD",100001,"full-current-balance")
        self.assertTrue(self.issuer.attest_balance(self.tenant,full)["reserve_current"])

    def test_authoritative_shortfall_revokes_unused_authority_without_erasing_liabilities(self):
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,'issuer-reserve','USD',10000,'initial-deposit'))
        self.issuer.mint(self.tenant,'agent-01',50_000_000,'initial-mint')
        observation=self.bank.balance_event(self.tenant,'USD',4000,'shortfall')
        state=self.issuer.attest_balance(self.tenant,observation)
        self.assertEqual((state['reserve'],state['observed_reserve_units'],state['liabilities'],state['headroom']),(100_000_000,40_000_000,50_000_000,0))
        self.assertTrue(state['reserve_reconciliation_required']);self.assertFalse(state['reserve_current'])
        self.assertTrue(self.issuer.attest_balance(self.tenant,observation)['replayed'])
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,'issuer-reserve','USD',1,'small-credit'))
        with self.assertRaisesRegex(Error,'reserve_reconciliation_required'):self.issuer.mint(self.tenant,'agent-01',1,'blocked-mint')
        self.assertEqual(self.issuer.request_redemption(self.tenant,'agent-01',10000,'preserved-claim')['state'],'pending')
        with self.book.db() as db:
            self.assertEqual(db.execute('SELECT balance FROM reserve_observations WHERE reference=?',('shortfall',)).fetchone()[0],4000)
        full=self.bank.balance_event(self.tenant,'USD',10001,'reconciled-balance')
        self.assertTrue(self.issuer.attest_balance(self.tenant,full)['reserve_current'])

    def test_merchant_earnings_have_a_complete_redemption_lifecycle(self):
        self.issuer.attest(self.tenant,self.bank.deposit_event(self.tenant,"issuer-reserve","USD",10000,"reserves"))
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
