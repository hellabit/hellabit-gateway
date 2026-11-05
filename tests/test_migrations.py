import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from hellabit.ledger import Ledger,Error,digest

ROOT=Path(__file__).resolve().parents[1]

class MigrationTests(unittest.TestCase):
    def test_newer_or_foreign_database_is_rejected_without_modifying_it(self):
        for pragma in ('user_version=999','application_id=123456'):
            with self.subTest(pragma=pragma),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'future.sqlite3'
                with sqlite3.connect(path) as db:db.execute('PRAGMA '+pragma)
                before=path.read_bytes()
                with self.assertRaises(Error):Ledger(path)
                self.assertEqual(path.read_bytes(),before)
                self.assertFalse(path.with_name(path.name+'-wal').exists())

    def legacy(self,path,version):
        db=sqlite3.connect(path)
        try:
            db.executescript((ROOT/'tests/fixtures'/('storage-v'+version+'.sql')).read_text())
            db.execute("INSERT INTO tenants(id,name,country,token_hash,created) VALUES('hb_legacy','Legacy Merchant','CA',?,1000)",('0'*64,))
            db.execute("INSERT INTO customers VALUES('hb_legacy','agent-01')")
            db.execute("INSERT INTO assets VALUES('hb_legacy','CAD','CAD',2,'bank')")
            db.execute("INSERT INTO issuer(tenant,name,symbol,currency) VALUES('hb_legacy','Legacy Dollar','LDLR','USD')")
            db.execute("INSERT INTO settings VALUES('sandbox_rail_key',?)",('11'*32,))
            db.execute("INSERT INTO settings VALUES('schema_version','1')")
            db.execute("INSERT INTO api_principals(id,tenant,issuer,subject_hash,role,expires,revoked,rate_limit) VALUES('principal_legacy','hb_legacy','https://legacy.example.invalid',?,'read-only',?,0,120)",(digest('legacy-subject'),int(time.time())+3600))
            db.commit()
        finally:db.close()

    def test_actual_released_schemas_support_status_then_export_without_constructor_order(self):
        for version in ('04','05'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);path=root/'legacy.sqlite3';self.legacy(path,version)
                env={'PATH':os.defpath,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'}
                for arguments in [('status',),('export','--output',str(root/'history.jsonl'))]:
                    r=subprocess.run([sys.executable,str(ROOT/'scripts/operate.py'),'--state',str(path),'--tenant','hb_legacy',*arguments],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(r.stdout)['ok'])
                rows=[json.loads(s) for s in (root/'history.jsonl').read_text().splitlines()]
                principal=next(r['row'] for r in rows if r.get('table')=='api_principals')
                self.assertEqual(principal['id'],'principal_legacy');self.assertIn('valid_after',principal)
                self.assertEqual(Ledger(path).reconcile('hb_legacy')['journal_count'],0)

    def test_interrupted_upgrade_rolls_back_schema_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'legacy.sqlite3';self.legacy(path,'04')
            from hellabit import migrations
            original=migrations.execute_script;calls=0
            def interrupted(db,sql):
                nonlocal calls
                calls+=1;original(db,sql)
                if calls==3:raise RuntimeError('synthetic migration interruption')
            with patch('hellabit.migrations.execute_script',side_effect=interrupted),self.assertRaisesRegex(RuntimeError,'synthetic migration interruption'):Ledger(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
                self.assertNotIn('grant_id',{r[1] for r in db.execute('PRAGMA table_info(api_principals)')})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM api_principals').fetchone()[0],1)
            self.assertEqual(Ledger(path).reconcile('hb_legacy')['journal_count'],0)
