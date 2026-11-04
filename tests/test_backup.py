import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from hellabit.backup import create,restore
from hellabit.ledger import Ledger,Error
from hellabit.rails import SandboxBank
from hellabit.operations import Operations
from hellabit.payouts import Payouts

class BackupTests(unittest.TestCase):
    def test_oversize_snapshot_stops_before_creating_a_backup(self):
        with patch("hellabit.backup.LIMIT",4096):
            with self.assertRaisesRegex(Error,"backup_size_limit"):create(self.book,self.backup,self.key)
        self.assertFalse(self.backup.exists())

    def test_authenticated_but_damaged_database_is_rejected_without_a_restore_target(self):
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from hellabit.backup import AAD
        nonce=secrets.token_bytes(12)
        invalid=b"SQLite format 3\x00"+b"invalid database"*100
        envelope={"schema":AAD.decode(),"nonce":base64.b64encode(nonce).decode(),
                  "ciphertext":base64.b64encode(AESGCM(self.key).encrypt(nonce,invalid,AAD)).decode()}
        self.backup.write_text(json.dumps(envelope));target=self.root/"invalid.sqlite3"
        with self.assertRaisesRegex(Error,"backup_database_invalid"):restore(self.backup,target,self.key)
        self.assertFalse(target.exists())

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.book=Ledger(self.root/"source.sqlite3")
        self.tenant=self.book.create_tenant("Backup Merchant")["tenant"]
        SandboxBank(self.book).fund(self.tenant,"agent-01","CAD",1000,"funding")
        quote=self.book.create_quote(self.tenant,"CAD",100,1)["quote"]
        self.op=self.book.reserve(self.tenant,"agent-01",quote,"stable-purchase")["id"]
        self.book.run_report(self.tenant,self.op)
        pending=self.book.reserve(self.tenant,"agent-01",quote,"unknown-purchase")["id"]
        self.book.resolve(self.tenant,pending,"uncertain")
        self.payout=Payouts(self.book).request(self.tenant,"CAD",50,"merchant-account","pending-payout")
        Operations(self.book).claim(self.tenant)
        self.key=secrets.token_bytes(32);self.backup=self.root/"snapshot.hbbackup"

    def tearDown(self):self.tmp.cleanup()

    def test_encrypted_snapshot_restores_results_holds_and_original_provider_identity(self):
        result=create(self.book,self.backup,self.key)
        self.assertTrue(result["encrypted"])
        raw=self.backup.read_bytes()
        self.assertNotIn(self.tenant.encode(),raw);self.assertNotIn(b"SQLite format",raw)
        target=self.root/"restored.sqlite3"
        restore(self.backup,target,self.key)
        recovered=Ledger(target)
        self.assertTrue(recovered.reconcile(self.tenant)["ok"])
        self.assertEqual(recovered.lookup_operation(self.tenant,"stable-purchase")["id"],self.op)
        self.assertEqual(recovered.delivery(self.tenant,self.op)["operation"],self.op)
        old=Operations(self.book).get(self.tenant,self.payout["intent"])
        new=Operations(recovered).get(self.tenant,self.payout["intent"])
        self.assertEqual(old["external_key"],new["external_key"])
        self.assertEqual(new["state"],"submitting")
        self.assertEqual(target.stat().st_mode&0o777,0o600)

    def test_tampering_wrong_key_and_overwrite_do_not_create_or_replace_target(self):
        create(self.book,self.backup,self.key)
        for key in [secrets.token_bytes(32),self.key]:
            source=self.backup
            if key==self.key:
                source=self.root/"tampered.hbbackup"
                body=json.loads(self.backup.read_text());cipher=body["ciphertext"]
                body["ciphertext"]=("A" if cipher[0]!="A" else "B")+cipher[1:]
                source.write_text(json.dumps(body))
            target=self.root/"must-not-exist.sqlite3"
            with self.assertRaisesRegex(Error,"backup_authentication_failed"):restore(source,target,key)
            self.assertFalse(target.exists())
        with self.assertRaisesRegex(Error,"restore_target_exists"):restore(self.backup,self.book.path,self.key)
        with self.assertRaisesRegex(Error,"backup_target_exists"):create(self.book,self.backup,self.key)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])
