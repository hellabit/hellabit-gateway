import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from hellabit.fixtures import SandboxProvider
from hellabit.inbox import ProviderInbox
from hellabit.ledger import Ledger,Error,USDC_TEST,canonical
from hellabit.operations import Operations
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank,sandbox_usdc_funding

ROOT=Path(__file__).resolve().parents[1]

class OperatingContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.book=Ledger(self.root/'ledger.sqlite3')
        self.tenant=self.book.create_tenant('Operating Merchant')['tenant']
        self.ops=Operations(self.book)
        self.provider=SandboxProvider(self.book.path.with_name(self.book.path.name+'.sandbox-provider.sqlite3'))
        for asset in ('CAD','USD',USDC_TEST):
            if asset==USDC_TEST:sandbox_usdc_funding(self.book,self.tenant,'agent-01',1000,'opening-usdc')
            else:SandboxBank(self.book).fund(self.tenant,'agent-01',asset,1000,'opening-'+asset)
            q=self.book.create_quote(self.tenant,asset,500,0)['quote']
            op=self.book.reserve(self.tenant,'agent-01',q,'work-'+asset)['id'];self.book.run_report(self.tenant,op)

    def tearDown(self):self.tmp.cleanup()

    def payout(self,asset='CAD',key='payout'):
        return Payouts(self.book).request(self.tenant,asset,100,'recipient',key)

    def test_funding_references_are_scoped_by_clearing_account(self):
        bank=SandboxBank(self.book)
        first=bank.fund(self.tenant,'agent-01','CAD',7,'shared-bank-ref')
        other=bank.fund(self.tenant,'agent-01','USD',11,'shared-bank-ref')
        self.assertNotEqual(first['journal'],other['journal'])
        self.assertTrue(bank.fund(self.tenant,'agent-01','CAD',7,'shared-bank-ref')['replayed'])
        with self.assertRaisesRegex(Error,'settlement_replay_mismatch'):bank.fund(self.tenant,'agent-01','CAD',8,'shared-bank-ref')
        self.assertTrue(self.book.reconcile(self.tenant)['ok'])

    def tick(self,intent=None):
        command=[sys.executable,str(ROOT/'scripts/operate.py'),'--state',str(self.book.path),'--tenant',self.tenant,'tick']
        if intent:command+=['--intent',intent]
        r=subprocess.run(command,cwd=ROOT,env={'PATH':os.defpath,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)['result']

    def test_two_authenticated_providers_can_reuse_event_and_reference_ids(self):
        for index,asset,rail in [(0,'CAD','bank'),(1,USDC_TEST,'usdc')]:
            provider=SandboxProvider(self.root/('provider-'+str(index)+'.sqlite3'))
            payout=self.payout(asset,'payout-'+str(index));self.ops.process_one(self.tenant,provider,intent=payout['intent'])
            row=self.ops.get(self.tenant,payout['intent'])
            receipt=provider.return_payment(row['external_key'],'return-1',20);receipt['reference']='return-reference-1'
            secret=b'synthetic-webhook-key-for-operating-tests'
            inbox=ProviderInbox(self.book,'provider-'+str(index),secret);inbox.bind(self.tenant,rail)
            now=self.book.clock();raw=canonical({'id':'return-1','tenant':self.tenant,'occurred':now,'receipt':receipt}).encode()
            inbox.receive(self.tenant,raw,str(now),inbox.signature(secret,str(now),raw));inbox.apply(self.tenant,'return-1')
            inbox.apply(self.tenant,'return-1')
            with self.book.db() as db:self.assertEqual(self.book._balance(db,self.tenant,asset,'merchant'),420)
        with self.book.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(DISTINCT reference) FROM journals WHERE kind='payout-returned'").fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT event_key) FROM payment_adjustments').fetchone()[0],2)
        self.assertTrue(self.book.reconcile(self.tenant)['ok'])

    def test_actual_cli_respects_selected_intent_and_advances_unrelated_work(self):
        cad=self.payout();self.ops.claim(self.tenant,intent=cad['intent'])
        with self.book.db() as db:db.execute('UPDATE execution_intents SET lease_until=0 WHERE id=?',(cad['intent'],))
        original=self.ops.get(self.tenant,cad['intent'])['external_key']
        usd=self.payout('USD','usd-1');result=self.tick(usd['intent'])
        self.assertEqual((result['intent'],result['state']),(usd['intent'],'confirmed'))
        self.assertEqual(self.ops.get(self.tenant,cad['intent'])['state'],'unknown')
        next_usd=self.payout('USD','usd-2');result=self.tick()
        self.assertEqual((result['intent'],result['state']),(next_usd['intent'],'confirmed'))
        self.assertEqual(self.ops.get(self.tenant,cad['intent'])['external_key'],original)
        with self.book.db() as db:
            self.assertEqual(self.book._balance(db,self.tenant,'CAD','payout:'+cad['id']),100)
            self.assertEqual(db.execute('SELECT lookups FROM execution_retries WHERE intent=?',(cad['intent'],)).fetchone()[0],1)

    def test_partial_and_full_returns_reach_every_primary_projection(self):
        payout=self.payout();self.ops.process_one(self.tenant,self.provider)
        row=self.ops.get(self.tenant,payout['intent']);key=row['external_key']
        for event,amount,total,label in [('return-1',20,20,'partially-returned'),('return-2',80,100,'fully-returned')]:
            self.provider.return_payment(key,event,amount);self.ops.reconcile(self.tenant,payout['intent'],self.provider)
            views=[self.ops.get(self.tenant,payout['intent']),self.ops.list(self.tenant)['intents'][0],
                   self.book.snapshot(self.tenant)['payouts'][0],self.payout()]
            for view in views:
                self.assertEqual((view['original_settled_units'],view['returned_units'],view['net_paid_units']),(100,total,100-total))
                self.assertEqual(view['effective_outcome'],label);self.assertEqual(view['latest_adjustment']['event_id'],event)
        self.assertEqual(self.ops.get(self.tenant,payout['intent'])['state'],'confirmed')
        self.assertEqual(self.book.snapshot(self.tenant)['payouts'][0]['state'],'paid')
