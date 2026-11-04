import concurrent.futures
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hellabit.ledger import Ledger, Error, digest, require_safe_sqlite
from hellabit.rails import SandboxBank, sandbox_usdc_funding


INPUT = {'currency':'CAD','items':[{'quantity':2,'unit_amount_units':1500},{'quantity':1,'unit_amount_units':500}], 'tax_units':175,'declared_total_units':3675}


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'book.sqlite3'
        self.book=Ledger(self.path)
        self.tenant=self.book.create_tenant('Purchase Example')['tenant']
        SandboxBank(self.book).fund(self.tenant,'agent-01','CAD',10000,'initial-funding')

    def tearDown(self):self.tmp.cleanup()

    def reserve(self,key='one-purchase',value=None):
        q=self.book.create_quote(self.tenant,'CAD',100,1,'invoice-check',value or INPUT)
        return self.book.reserve(self.tenant,'agent-01',q['quote'],key)['id'],q

    def test_real_computation_and_input_identity_are_immutable_and_recoverable(self):
        supplied=copy.deepcopy(INPUT)
        op,quote=self.reserve(value=supplied)
        supplied['items'][0]['quantity']=999
        self.book.run_report(self.tenant,op)
        result=self.book.delivery(self.tenant,op)
        self.assertEqual(result['input_hash'],digest(INPUT))
        self.assertEqual(quote['input_hash'],result['input_hash'])
        self.assertEqual(result['result']['computed_total_units'],3675)
        self.assertTrue(result['result']['matches_declared_total'])
        self.assertEqual(result['charge_units'],100)
        self.assertEqual(self.book.lookup_operation(self.tenant,'one-purchase')['id'],op)
        with self.book.db() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE quotes SET work_input=?',('{}',))

    def test_mismatch_is_a_computed_result_and_not_a_fake_positive(self):
        value={**INPUT,'declared_total_units':3600}
        op,_=self.reserve(value=value)
        self.book.run_report(self.tenant,op)
        result=self.book.delivery(self.tenant,op)['result']
        self.assertFalse(result['matches_declared_total'])
        self.assertEqual(result['difference_units'],-75)
        self.assertFalse(result['invoice_authenticity_verified'])
        self.assertEqual(result['scope'],'arithmetic-only')

    def test_invalid_or_ambiguous_invoice_inputs_do_not_create_quotes(self):
        cases=[{**INPUT,'currency':'USDC'},{**INPUT,'tax_units':True},{**INPUT,'unknown':'field'},{**INPUT,'items':[]},{**INPUT,'items':[{'quantity':True,'unit_amount_units':1}]},{**INPUT,'items':[{'quantity':1,'unit_amount_units':1.2}]},{**INPUT,'items':[{'quantity':1000,'unit_amount_units':10**12}]}]
        for value in cases:
            with self.assertRaises(Error):self.book.create_quote(self.tenant,'CAD',100,1,'invoice-check',value)
        with self.book.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0],0)

    def test_computation_failure_rolls_back_charge_and_result_entitlement(self):
        op,_=self.reserve()
        with patch('hellabit.services.run_service',side_effect=RuntimeError('synthetic-computation-failure')):
            with self.assertRaises(RuntimeError):self.book.run_report(self.tenant,op)
        self.assertEqual(self.book.operation(self.tenant,op)['state'],'reserved')
        with self.book.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='charge'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],0)
        self.book.run_report(self.tenant,op)
        self.assertEqual(self.book.delivery(self.tenant,op)['result']['computed_total_units'],3675)

    def test_concurrent_run_and_reopen_preserve_one_charge_and_the_original_result(self):
        op,_=self.reserve()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.book.run_report(self.tenant,op),range(20)))
        self.assertEqual(sum(not x['replayed'] for x in results),1)
        reopened=Ledger(self.path)
        self.assertEqual(reopened.delivery(self.tenant,op),self.book.delivery(self.tenant,op))
        with reopened.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='charge'").fetchone()[0],1)

    def test_activity_filters_before_limit_and_prioritizes_old_uncertain_work(self):
        op,_=self.reserve('old-unknown')
        self.book.resolve(self.tenant,op,'uncertain')
        for i in range(35):
            sandbox_usdc_funding(self.book,self.tenant,'agent-01',1,'usdc-'+str(i))
            q=self.book.create_quote(self.tenant,'CAD',1,0)['quote']
            newer=self.book.reserve(self.tenant,'agent-01',q,'new-'+str(i))['id']
            self.book.run_report(self.tenant,newer)
        selected=self.book.activity(self.tenant,'CAD')
        self.assertEqual(selected['operations'][0]['id'],op)
        self.assertEqual(selected['operation_count'],36)
        self.assertEqual(len(selected['operations']),30)
        self.assertTrue(all(x['asset']=='CAD' for x in selected['journal']))
        self.assertEqual(selected['journal_count'],36)
        usdc=self.book.activity(self.tenant,next(a['id']for a in self.book.snapshot(self.tenant)['assets']if a['rail']=='usdc'))
        self.assertEqual(usdc['journal_count'],35)
        self.assertEqual(len(usdc['journal']),30)
        self.assertEqual(self.book.activity(self.tenant)['operations'][0]['id'],op)

    def test_sqlite_backup_restores_results_holds_and_purchase_lookup(self):
        completed,_=self.reserve('completed-for-backup');self.book.run_report(self.tenant,completed)
        held,_=self.reserve('uncertain-for-backup');self.book.resolve(self.tenant,held,'uncertain')
        restored_path=Path(self.tmp.name)/'restore.sqlite3'
        with self.book.db() as source:
            with sqlite3.connect(restored_path) as destination:source.backup(destination)
        restored_path.chmod(0o600)
        restored=Ledger(restored_path)
        self.assertTrue(restored.reconcile(self.tenant)['ok'])
        self.assertEqual(restored.lookup_operation(self.tenant,'uncertain-for-backup')['state'],'uncertain')
        self.assertEqual(restored.delivery(self.tenant,completed),self.book.delivery(self.tenant,completed))
        self.assertEqual(restored.snapshot(self.tenant)['assets'],self.book.snapshot(self.tenant)['assets'])

    def test_unpatched_wal_runtime_fails_before_database_creation(self):
        for version in [(3,51,3),(3,53,0),(3,44,6),(3,50,7)]:require_safe_sqlite(version)
        for version in [(3,51,2),(3,50,6),(3,44,5)]:
            with self.assertRaises(Error):require_safe_sqlite(version)
        path=Path(self.tmp.name)/'unsafe.sqlite3'
        with patch('hellabit.ledger.sqlite3.sqlite_version_info',(3,51,2)):
            with self.assertRaisesRegex(Error,'sqlite_runtime_requires_wal_fix'):Ledger(path)
        self.assertFalse(path.exists())
