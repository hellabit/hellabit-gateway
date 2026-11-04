import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from hellabit.ledger import Ledger
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank
from hellabit.identity import Identity

ROOT=Path(__file__).resolve().parents[1]

class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.book=Ledger(self.home/"ledger.sqlite3")
        self.tenant=self.book.create_tenant("Command merchant")["tenant"]
        SandboxBank(self.book).fund(self.tenant,"agent-01","CAD",1000,"opening-funds")
        q=self.book.create_quote(self.tenant,"CAD",1000,10)
        op=self.book.reserve(self.tenant,"agent-01",q["quote"],"original-work")
        self.book.run_report(self.tenant,op["id"])
        self.env={"PATH":os.defpath,"LANG":"en_US.UTF-8","PYTHONPATH":str(ROOT),"PYTHONDONTWRITEBYTECODE":"1"}

    def tearDown(self):self.tmp.cleanup()

    def command(self,*arguments,good=True):
        result=subprocess.run([sys.executable,str(ROOT/"scripts/operate.py"),"--state",str(self.book.path),
            "--tenant",self.tenant,*map(str,arguments)],cwd=ROOT,env=self.env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0 if good else 1,result.stderr)
        data=json.loads(result.stdout)
        self.assertFalse(data["live_payments_enabled"])
        return data

    def private(self,name,data):
        p=self.home/name;p.write_text(json.dumps(data));p.chmod(0o600);return p

    def test_status_packet_and_recovery_are_one_command_and_exclude_account_identity(self):
        status=self.command("status")["result"]
        self.assertTrue(status["machine_observations"]["ledger_balanced"])
        target=self.home/"responsibilities.json"
        self.command("packet","--output",target)
        self.assertNotIn(self.tenant,target.read_text());self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertTrue(self.command("drill")["result"]["encrypted"])
        self.assertEqual(self.command("packet","--output",target,good=False)["error"],"backup_target_exists")

    def test_worker_command_settles_once_then_does_no_more_work(self):
        p=Payouts(self.book).request(self.tenant,"CAD",50,"synthetic-recipient","original-payout")
        self.assertEqual(self.command("tick","--intent",p["intent"])["result"]["state"],"confirmed")
        self.assertFalse(self.command("tick")["result"]["processed"])
        self.assertEqual(self.command("reconcile","--intent",p["intent"])["result"]["state"],"confirmed")
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_exact_usage_invoice_preview_collects_nothing(self):
        self.command("usage","plan","--plan","api-basic","--rate-micro",1000)
        self.command("usage","record","--plan","api-basic","--key","first-batch","--quantity",10)
        preview=self.command("usage","preview","--plan","api-basic")["result"]
        self.assertEqual(preview["due_minor"],1)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_subject_grant_requires_private_file_and_exact_state_binding_then_revokes(self):
        public=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(
            serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        (self.home/"issuer.pem").write_bytes(public)
        cfg={"state":"ledger.sqlite3","issuer":"https://identity.example.invalid","audience":"hellabit-test",
             "public_keys":{"key-1":"issuer.pem"},"allowed_hosts":["api.example.invalid"]}
        binding=self.private("api.json",cfg)
        grant=self.private("grant.json",{"subject":"synthetic-subject","role":"merchant","expires":int(time.time())+3600})
        result=self.command("identity","grant","--binding",binding,"--grant",grant)["result"]
        self.assertNotIn("synthetic-subject",json.dumps(result))
        self.assertTrue(self.command("identity","revoke","--binding",binding,"--principal",result["id"])["result"]["revoked"])
        grant.chmod(0o644)
        self.assertEqual(self.command("identity","grant","--binding",binding,"--grant",grant,good=False)["error"],"operator_configuration_or_file_invalid")
        cfg["state"]="other.sqlite3";binding=self.private("api.json",cfg)
        self.assertEqual(self.command("identity","grant","--binding",binding,"--grant",grant,good=False)["error"],"identity_state_binding_mismatch")

    def test_chain_bind_and_status_do_not_contact_network_or_book_money(self):
        config=self.private("chain.json",{"binding":"watched-test-token","chain_id":8453,
            "contract":"0x"+"1"*40,"start_block":10,"endpoint":"https://rpc.example.invalid/private-reference",
            "allowed_hosts":["rpc.example.invalid"]})
        result=self.command("index","bind","--config",config)["result"]
        self.assertEqual(result["network_requests"],0)
        result=self.command("index","status","--config",config)["result"]
        self.assertEqual(result["cursor"],9);self.assertNotIn("endpoint",result)
