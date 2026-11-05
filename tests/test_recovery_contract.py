"""Executable failure cases for payment-outcome adapters and recovery."""
import itertools
from pathlib import Path
import secrets
import tempfile
import unittest
import json
import os
import subprocess
import sys
import time
import jwt
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from hellabit.identity import Identity
from hellabit.recovery import handover
from hellabit.backup import create,restore
from hellabit.fixtures import SandboxProvider
from hellabit.ledger import Ledger,Error
from hellabit.operations import Operations
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank

class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1000
        self.book=Ledger(self.root/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Contract Merchant")["tenant"]
        for asset in ("CAD","USD"):
            SandboxBank(self.book).fund(self.tenant,"agent-01",asset,10000,"opening-"+asset)
            quote=self.book.create_quote(self.tenant,asset,1000,10)["quote"]
            op=self.book.reserve(self.tenant,"agent-01",quote,"work-"+asset)["id"]
            self.book.run_report(self.tenant,op)
        self.ops=Operations(self.book);self.provider=SandboxProvider(self.root/"provider.sqlite3")

    def tearDown(self):self.tmp.cleanup()

    def payout(self,key="command",asset="CAD",amount=50,book=None):
        return Payouts(book or self.book).request(self.tenant,asset,amount,"recipient",key)

    def test_failure_after_provider_fencing_preserves_histories_and_stops_both_writers(self):
        key=secrets.token_bytes(32);snapshot=self.root/'old.hbbackup';create(self.book,snapshot,key)
        payout=self.payout();job=self.ops.claim(self.tenant);self.provider.submit(job)
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        before=self.book.reconcile(self.tenant)['journal_count'];output=self.root/'resumed.sqlite3'
        with patch('hellabit.recovery._publish_file',side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):handover(Ledger(restored_path),self.book,self.provider,output)
        self.assertFalse(output.exists());self.assertTrue(snapshot.exists())
        self.assertEqual(self.book.reconcile(self.tenant)['journal_count'],before)
        with self.assertRaisesRegex(Error,'previous_writer_retired'):self.payout('retired-writer')
        with self.assertRaisesRegex(Error,'provider_writer_fenced'):self.provider.submit(job)
        with Ledger(restored_path).db() as db:self.assertTrue(db.execute("SELECT 1 FROM settings WHERE key='recovery_quarantine'").fetchone())

    def test_actual_handover_cli_retains_missing_commands_reconciles_returns_and_retires_access(self):
        self.now=int(time.time())
        signer=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=signer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        config={'issuer':'https://identity.example.invalid','audience':'hellabit-test','public_keys':{'key-1':public}}
        identity=Identity(self.book,**config)
        grant=identity.enroll(self.tenant,'old-reader','read-only',self.now+3600)
        token=jwt.encode({'iss':config['issuer'],'aud':config['audience'],'sub':'old-reader','iat':self.now-1,
                          'exp':self.now+300,**grant['token_claims']},signer,algorithm='RS256',headers={'kid':'key-1','typ':'at+jwt'})
        merchant_key=self.book.issue_merchant_key(self.tenant)['api_key']
        key=secrets.token_bytes(32);snapshot=self.root/'old.hbbackup';create(self.book,snapshot,key)
        payout=self.payout();job=self.ops.claim(self.tenant);self.provider.submit(job)
        self.provider.return_payment(job['external_key'],'later-return',20)
        identity.revoke(self.tenant,grant['id'])
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        restored=Ledger(restored_path,clock=lambda:self.now)
        with self.assertRaisesRegex(Error,'restored_database_quarantined'):
            Identity(restored,**config).authenticate(token)
        output=self.root/'resumed.sqlite3'
        command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/operate.py'),'--state',str(restored_path),
                 'recover-handover','--authoritative-state',str(self.book.path),'--provider-state',str(self.provider.path),'--output',str(output)]
        r=subprocess.run(command,capture_output=True,text=True,timeout=20,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'})
        self.assertEqual(r.returncode,0,r.stderr);result=json.loads(r.stdout)['result']
        self.assertTrue(result['resumed']);self.assertFalse(result['catastrophic_archive_recovery_verified'])
        resumed=Ledger(output,clock=lambda:self.now);row=Operations(resumed).get(self.tenant,payout['intent'])
        self.assertEqual((row['state'],row['returned_units'],row['net_paid_units']),('confirmed',20,30))
        self.assertEqual(row['external_key'],job['external_key']);self.assertEqual(self.provider.accepted_count(),1)
        with self.assertRaises(Error):Identity(resumed,**config).authenticate(token)
        with self.assertRaises(Error):resumed.principal(merchant_key)
        with self.assertRaisesRegex(Error,'previous_writer_retired'):self.payout('old-writer')
        with self.assertRaisesRegex(Error,'provider_writer_fenced'):self.provider.submit(job)
        self.payout('new-writer',book=resumed)
        fresh_provider=SandboxProvider(self.provider.path)
        self.assertEqual(Operations(resumed).process_one(self.tenant,fresh_provider)['intent']['state'],'confirmed')
        self.assertTrue(resumed.reconcile(self.tenant)['ok']);self.assertEqual(fresh_provider.accepted_count(),2)

    def test_unknown_external_command_blocks_handover_before_fencing_or_publishing(self):
        key=secrets.token_bytes(32);snapshot=self.root/'old.hbbackup';create(self.book,snapshot,key)
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        payout=self.payout();job=self.ops.claim(self.tenant);self.provider.submit(job)
        # The selected primary is older than an independent accepted command.
        with tempfile.TemporaryDirectory() as tmp:
            older=Path(tmp)/'older.sqlite3'
            import sqlite3
            with Ledger(restored_path).db() as src,sqlite3.connect(older) as target:src.backup(target)
            with sqlite3.connect(older) as db:db.execute("DELETE FROM settings WHERE key='recovery_quarantine'")
            output=self.root/'must-not-exist.sqlite3'
            with self.assertRaisesRegex(Error,'recovery_missing_authoritative_command'):
                handover(Ledger(restored_path),Ledger(older),self.provider,output)
            self.assertFalse(output.exists())
            self.assertEqual(self.provider.submit(job)['status'],'settled')

    def test_restore_before_intent_cannot_recreate_or_execute_a_payment(self):
        key=secrets.token_bytes(32);snapshot=self.root/"before.hbbackup"
        create(self.book,snapshot,key)
        original=self.payout();self.ops.process_one(self.tenant,self.provider)
        target=self.root/"restored.sqlite3";restore(snapshot,target,key)
        restored=Ledger(target,clock=lambda:self.now);ops=Operations(restored)
        self.assertTrue(restored.reconcile(self.tenant)["ok"])
        for action in (lambda:self.payout(book=restored),lambda:ops.process_one(self.tenant,self.provider),
                       lambda:SandboxBank(restored).fund(self.tenant,"agent-01","CAD",1,"new-funds")):
            with self.assertRaisesRegex(Error,"restored_database_quarantined"):action()
        self.assertEqual(self.provider.accepted_count(),1)
        self.assertEqual(self.ops.get(self.tenant,original["intent"])["state"],"confirmed")

    def test_business_identity_and_terms_survive_loss_of_the_caller_mapping(self):
        # Two isolated snapshots made before the command demonstrate stable
        # identities independently of the mandatory restore quarantine.
        copies=[]
        for name in ("same","changed"):
            import sqlite3
            path=self.root/(name+".sqlite3")
            with self.book.db() as source,sqlite3.connect(path) as target:source.backup(target)
            copies.append(Ledger(path,clock=lambda:self.now))
        original=self.payout();job=self.ops.get(self.tenant,original["intent"])
        self.ops.process_one(self.tenant,self.provider)
        same=self.payout(book=copies[0]);otherops=Operations(copies[0])
        self.assertEqual(otherops.get(self.tenant,same["intent"])["external_key"],job["external_key"])
        self.assertEqual(otherops.process_one(self.tenant,self.provider)["intent"]["state"],"confirmed")
        self.payout(book=copies[1],amount=51)
        self.assertEqual(Operations(copies[1]).process_one(self.tenant,self.provider)["intent"]["state"],"unknown")
        self.assertEqual(self.provider.accepted_count(),1)

    def test_authoritative_close_fences_a_worker_that_died_before_sending(self):
        p=self.payout();job=self.ops.claim(self.tenant)
        self.now+=61;self.ops.reconcile(self.tenant,p["intent"],self.provider)
        self.assertEqual(self.ops.cancel_unknown(self.tenant,p["intent"],self.provider)["state"],"failed")
        self.assertEqual(self.provider.submit(job)["status"],"rejected")
        self.assertEqual(self.provider.accepted_count(),0)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_close_after_acceptance_recovers_the_payment_instead_of_releasing_it(self):
        p=self.payout();job=self.ops.claim(self.tenant);self.provider.submit(job)
        self.now+=61;self.ops.reconcile(self.tenant,p["intent"],self.provider)
        # A completed reconciliation is terminal; no cancellation is available.
        with self.assertRaisesRegex(Error,"unknown_payment_required"):
            self.ops.cancel_unknown(self.tenant,p["intent"],self.provider)
        self.assertEqual(self.provider.accepted_count(),1)

    def test_uncertainty_isolated_to_the_exact_synthetic_account_currency(self):
        cad=self.payout();self.ops.claim(self.tenant,intent=cad["intent"])
        usd=self.payout("dollar-command","USD")
        result=self.ops.process_one(self.tenant,self.provider,intent=usd["intent"])
        self.assertEqual(result["intent"]["state"],"confirmed")
        self.assertEqual(self.ops.get(self.tenant,cad["intent"])["state"],"submitting")

    def test_later_returns_append_compensation_and_preserve_original_settlement(self):
        p=self.payout();self.ops.process_one(self.tenant,self.provider)
        original=self.ops.get(self.tenant,p["intent"])
        self.provider.return_payment(original["external_key"],"partial-return",20)
        self.ops.reconcile(self.tenant,p["intent"],self.provider)
        self.ops.reconcile(self.tenant,p["intent"],self.provider)
        with self.book.db() as db:
            self.assertEqual(self.book._balance(db,self.tenant,"CAD","merchant"),960)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM payment_adjustments").fetchone()[0],1)
        self.assertEqual(self.ops.get(self.tenant,p["intent"])["evidence_hash"],original["evidence_hash"])
        self.provider.return_payment(original["external_key"],"too-large",31)
        with self.assertRaisesRegex(Error,"adjustment_exceeds_payment"):
            self.ops.reconcile(self.tenant,p["intent"],self.provider)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_generated_orderings_never_double_book_settlement_or_return(self):
        for index,order in enumerate(itertools.permutations(("observe","repeat","return"))):
            p=self.payout("ordering-"+str(index));job=self.ops.claim(self.tenant,intent=p["intent"])
            receipt=self.provider.submit(job)
            self.ops._unknown(self.tenant,p["intent"],job["lease"],"reply-lost")
            for action in order:
                if action=="return":
                    self.provider.return_payment(job["external_key"],"return-"+str(index),50)
                self.ops.reconcile(self.tenant,p["intent"],self.provider)
            self.ops.reconcile(self.tenant,p["intent"],self.provider)
            with self.book.db() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='payout-paid' AND reference=?",(p["id"],)).fetchone()[0],1)
            self.assertTrue(self.book.reconcile(self.tenant)["ok"])
