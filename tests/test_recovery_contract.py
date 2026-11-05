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
from hellabit.recovery import handover,resume_sealed
from hellabit.backup import create,restore
from hellabit.fixtures import SandboxProvider
from hellabit.ledger import Ledger,Error
from hellabit.operations import Operations
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank

class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.now=1000
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

    def cli(self,state,*arguments,ok=True):
        cmd=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/operate.py'),'--state',str(state),'--tenant',self.tenant,*map(str,arguments)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=25,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'})
        self.assertEqual(r.returncode,0 if ok else 1,r.stderr+r.stdout)
        return json.loads(r.stdout)

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
        from hellabit.readiness import Readiness
        retired=Readiness(self.book).report(self.tenant)
        self.assertTrue(retired['machine_observations']['recovery_quarantine'])
        self.assertTrue(retired['machine_observations']['previous_writer_retired'])
        with Ledger(restored_path).db() as db:self.assertTrue(db.execute("SELECT 1 FROM settings WHERE key='recovery_quarantine'").fetchone())
        resumed=self.cli(restored_path,'recover-handover','--authoritative-state',self.book.path,'--provider-state',self.provider.path,'--output',output)
        self.assertTrue(resumed['result']['resumed']);self.assertTrue(output.is_file())

    def test_restart_completes_after_link_before_directory_sync(self):
        key=secrets.token_bytes(32);snapshot=self.root/'frozen.hbbackup';create(self.book,snapshot,key)
        restored_path=self.root/'restored.sqlite3';restore(snapshot,restored_path,key)
        payout=self.payout();self.ops.process_one(self.tenant,self.provider)
        output=self.root/'resumed.sqlite3'
        from hellabit.recovery import _publish_file
        def interrupted_link(source,dest):
            if dest==output:
                os.link(source,dest);raise OSError('simulated crash before directory sync')
            return _publish_file(source,dest)
        with patch('hellabit.recovery._publish_file',side_effect=interrupted_link),self.assertRaises(OSError):
            handover(Ledger(restored_path),self.book,self.provider,output)
        self.assertTrue(output.is_file())
        self.assertEqual(self.cli(output,'status',ok=False)['error'],'recovery_publication_pending')
        result=self.cli(restored_path,'recover-handover','--authoritative-state',self.book.path,'--provider-state',self.provider.path,'--output',output)['result']
        self.assertTrue(result['provider_binding_preserved']);self.assertEqual(self.cli(output,'reconcile','--intent',payout['intent'])['result']['net_paid_units'],50)
        self.assertEqual(self.provider.accepted_count(),1)

    def test_sealed_checkpoint_recovers_after_original_primary_and_stage_are_lost(self):
        outside=self.root/'retained';outside.mkdir();key=secrets.token_bytes(32)
        key_path=outside/'recovery.key';key_path.write_bytes(key);key_path.chmod(0o600)
        snapshot=self.root/'before.hbbackup';create(self.book,snapshot,key)
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        payout=self.payout();self.ops.process_one(self.tenant,self.provider)
        external=self.ops.get(self.tenant,payout['intent'])['external_key']
        output=self.root/'after-loss.sqlite3';archive=outside/'fenced.hbbackup'
        result=self.cli(restored_path,'recover-handover','--authoritative-state',self.book.path,'--provider-state',self.provider.path,
                        '--output',output,'--seal-to',archive,'--key-file',key_path)['result']
        self.assertTrue(result['sealed']);self.assertFalse(output.exists())
        for path in (self.book.path,restored_path,self.root/('.hellabit-'+result['handover_id']+'.sqlite3')):path.unlink()
        command=('recover-sealed','--input',archive,'--key-file',key_path,'--provider-state',self.provider.path,'--output',output)
        self.provider.return_payment(external,'while-primary-lost',5)
        stage=self.root/('.hellabit-sealed-'+result['handover_id']+'-v8.sqlite3')
        with patch('hellabit.backup.time.time',return_value=1700000000),patch(
                'hellabit.recovery._publish',side_effect=OSError('crash before destination publication')):
            with self.assertRaises(OSError):resume_sealed(archive,key,self.provider,output)
        self.assertFalse(output.exists())
        with self.provider.db() as db:
            first=db.execute('SELECT candidate_sha,activation_claimed FROM sealed_checkpoints').fetchone()
            self.assertEqual(first['activation_claimed'],0)
        # An existing staged file cannot replace the pinned bytes. A missing
        # stage can be rebuilt only from the original authenticated archive.
        with stage.open('ab') as file:file.write(b'changed-stage')
        with self.assertRaisesRegex(Error,'sealed_candidate_changed'):
            resume_sealed(archive,key,self.provider,output)
        stage.unlink()
        with patch('hellabit.recovery._activate',side_effect=OSError('crash after destination publication')):
            with self.assertRaises(OSError):resume_sealed(archive,key,self.provider,output)
        with self.provider.db() as db:
            second=db.execute('SELECT candidate_sha,activation_claimed FROM sealed_checkpoints').fetchone()
            self.assertNotEqual(second['candidate_sha'],first['candidate_sha'])
            self.assertEqual(second['activation_claimed'],1)
        stage.unlink()
        self.assertFalse(self.cli(self.book.path,*command)['result']['original_primary_required'])
        outcome=self.cli(output,'reconcile','--intent',payout['intent'])['result']
        self.assertEqual((outcome['returned_units'],outcome['net_paid_units']),(5,45))
        self.assertEqual(self.provider.accepted_count(),1)
        output.rename(self.root/'after-loss-moved.sqlite3')
        self.assertEqual(self.cli(self.book.path,*command,ok=False)['error'],'sealed_checkpoint_already_used')

    def test_handover_continues_after_midpage_loss_without_reposting(self):
        key=secrets.token_bytes(32);snapshot=self.root/'before.hbbackup';create(self.book,snapshot,key)
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        for i in range(6):
            payout=self.payout('page-'+str(i));self.provider.submit(self.ops.get(self.tenant,payout['intent']))
        output=self.root/'resumed.sqlite3';original=Operations.finish;calls=0
        def stopped(*args,**kwargs):
            nonlocal calls
            result=original(*args,**kwargs);calls+=1
            if calls==3:raise OSError('interruption between history checkpoints')
            return result
        with patch('hellabit.recovery.BATCH',2),patch.object(Operations,'finish',stopped),self.assertRaises(OSError):
            handover(Ledger(restored_path),self.book,self.provider,output)
        result=self.cli(restored_path,'recover-handover','--authoritative-state',self.book.path,'--provider-state',self.provider.path,'--output',output)['result']
        self.assertEqual(result['provider_movements'],6)
        with Ledger(output).db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='payout-paid'").fetchone()[0],6)
        self.assertEqual(self.provider.accepted_count(),6)

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
        self.provider.return_payment(job['external_key'],'post-handover-return',30)
        projected=self.cli(output,'reconcile','--intent',payout['intent'])['result']
        self.assertEqual((projected['returned_units'],projected['net_paid_units']),(50,0))
        next_payout=self.payout('new-writer',book=resumed)
        self.assertEqual(self.cli(output,'tick','--intent',next_payout['intent'])['result']['state'],'confirmed')
        canceled=self.payout('safe-close',book=resumed);Operations(resumed).claim(self.tenant,intent=canceled['intent'])
        with resumed.db() as db:db.execute('UPDATE execution_intents SET lease_until=0 WHERE id=?',(canceled['intent'],))
        self.cli(output,'reconcile','--intent',canceled['intent'])
        self.assertEqual(self.cli(output,'cancel-unknown','--intent',canceled['intent'])['result']['state'],'failed')
        second_backup=self.root/'second.hbbackup';create(resumed,second_backup,key)
        second_restored=self.root/'second-restored.sqlite3';restore(second_backup,second_restored,key)
        second=self.root/'second-resumed.sqlite3'
        self.assertTrue(self.cli(second_restored,'recover-handover','--authoritative-state',output,'--provider-state',self.provider.path,'--output',second)['result']['provider_binding_preserved'])
        self.assertEqual(self.cli(second,'reconcile','--intent',payout['intent'])['result']['returned_units'],50)
        for state in (output,second):self.assertFalse(state.with_name(state.name+'.sandbox-provider.sqlite3').exists())
        self.assertEqual(self.provider.accepted_count(),2)
        self.assertTrue(Ledger(second).reconcile(self.tenant)['ok'])

    def test_unknown_external_command_blocks_handover_before_fencing_or_publishing(self):
        key=secrets.token_bytes(32);snapshot=self.root/'old.hbbackup';create(self.book,snapshot,key)
        restored_path=self.root/'old.sqlite3';restore(snapshot,restored_path,key)
        payout=self.payout();job=self.ops.claim(self.tenant);self.provider.submit(job)
        # The selected primary is older than an independent accepted command.
        with tempfile.TemporaryDirectory() as tmp:
            older=Path(tmp)/'older.sqlite3'
            import sqlite3
            with Ledger(restored_path).db() as src,sqlite3.connect(older) as target:src.backup(target)
            with Ledger(older).db() as db:db.execute("DELETE FROM settings WHERE key='recovery_quarantine'")
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
