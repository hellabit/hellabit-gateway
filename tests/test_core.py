import concurrent.futures
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest

from hellabit.ledger import Error, Ledger, USDC_TEST, digest
from hellabit.issuer import Issuer
from hellabit.payouts import Payouts
from hellabit.rails import SandboxBank, LiveRail, UsdcReceiptVerifier, TRANSFER, sandbox_usdc_funding


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state" / "ledger.sqlite3"
        self.now = 1_800_000_000
        self.book = Ledger(self.path, lambda: self.now)
        self.created = self.book.create_tenant("Example Merchant")
        self.tenant = self.created["tenant"]
        self.bank = SandboxBank(self.book)
        self.issuer = Issuer(self.book)

    def tearDown(self):
        self.tmp.cleanup()

    def fails(self, code, fn, *args, **kwargs):
        with self.assertRaises(Error) as got:
            fn(*args, **kwargs)
        self.assertEqual(got.exception.code, code)

    def fund(self, amount=1000, reference="deposit-1"):
        return self.bank.fund(self.tenant, "agent-01", "CAD", amount, reference)

    def balance(self, asset="CAD"):
        return next(a for a in self.book.snapshot(self.tenant)["assets"] if a["id"] == asset)

    def quote(self, amount=100, fee=1):
        return self.book.create_quote(self.tenant, "CAD", amount, fee)["quote"]

    def reserved(self, key="request-1"):
        self.fund()
        return self.book.reserve(self.tenant, "agent-01", self.quote(), key)["id"]

    def attest(self, amount=100):
        event = self.bank.event(self.tenant, "issuer-reserve", "USD", amount, "reserve-1")
        return self.issuer.attest(self.tenant, event)

    def test_keys_are_hashed_and_scoped(self):
        self.assertEqual(self.book.authenticate(self.created["api_key"]), self.tenant)
        self.assertNotIn(self.created["api_key"].encode(), self.path.read_bytes())
        self.fails("unauthorized", self.book.authenticate, "wrong")

    def test_funding_replays_once_and_changed_amount_fails(self):
        self.fund(); self.assertTrue(self.fund()["replayed"])
        self.assertEqual(self.balance()["balance"], 1000)
        self.fails("settlement_replay_mismatch", self.fund, 1001)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_untrusted_bank_message_cannot_credit(self):
        event = self.bank.event(self.tenant, "agent-01", "CAD", 100, "bank-1")
        event["body"]["amount"] = 10000
        self.fails("bank_evidence_authentication_failed", self.bank.verify, event, self.tenant, "agent-01", "CAD", 10000)
        self.assertEqual(self.balance()["balance"], 0)

    def test_bank_evidence_binds_currency_recipient_workspace_and_direction(self):
        e = self.bank.event(self.tenant, "agent-01", "CAD", 100, "bank-1")
        for kwargs in [(self.tenant, "other", "CAD", 100), (self.tenant, "agent-01", "USD", 100), ("hb_other", "agent-01", "CAD", 100)]:
            self.fails("bank_evidence_binding_mismatch", self.bank.verify, e, *kwargs)
        self.fails("bank_evidence_binding_mismatch", self.bank.verify, e, self.tenant, "agent-01", "CAD", 100, "debit")

    def test_atomic_reservations_do_not_overspend(self):
        self.fund(100)
        q = self.quote(10, 0)
        def attempt(n):
            try:
                self.book.reserve(self.tenant, "agent-01", q, "concurrent-" + str(n)); return True
            except Error as e:
                self.assertEqual(e.code, "insufficient_available_credits"); return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            accepted = list(pool.map(attempt, range(60)))
        self.assertEqual(sum(accepted), 10)
        self.assertEqual(self.balance()["available"], 0)
        self.assertEqual(self.balance()["balance"], 100)

    def test_duplicate_concurrent_operation_is_one_reservation(self):
        self.fund(); q = self.quote()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            rows = list(pool.map(lambda _: self.book.reserve(self.tenant, "agent-01", q, "same-key"), range(30)))
        self.assertEqual(len({x["id"] for x in rows}), 1)
        self.assertEqual(self.balance()["held"], 100)

    def test_paid_work_fee_conservation_and_replay(self):
        op = self.reserved(); proof = digest("completed-report")
        self.book.resolve(self.tenant, op, "committed", proof)
        self.assertTrue(self.book.resolve(self.tenant, op, "committed", proof)["replayed"])
        b = self.balance()
        self.assertEqual((b["balance"], b["merchant"], b["fees"], b["held"]), (900, 99, 1, 0))
        self.fails("result_conflict", self.book.resolve, self.tenant, op, "committed", digest("different"))

    def test_completion_requires_evidence_and_terminal_state_cannot_change(self):
        op = self.reserved()
        self.fails("completion_evidence_required", self.book.resolve, self.tenant, op, "committed")
        self.book.resolve(self.tenant, op, "released")
        self.fails("operation_terminal", self.book.resolve, self.tenant, op, "committed", digest("x"))
        self.assertEqual(self.balance()["available"], 1000)

    def test_unknown_outcome_keeps_hold_through_restart(self):
        op = self.reserved()
        self.book.resolve(self.tenant, op, "uncertain")
        self.book = Ledger(self.path, lambda: self.now)
        self.assertEqual(self.balance()["held"], 100)
        self.fails("reconciliation_required", self.book.resolve, self.tenant, op, "released")
        self.book.resolve(self.tenant, op, "committed", digest("confirmed-after-restart"), reconciled=True)
        self.assertEqual(self.balance()["available"], 900)

    def test_idempotency_key_rejects_changed_quote(self):
        self.fund(); q = self.quote()
        self.book.reserve(self.tenant, "agent-01", q, "key")
        self.fails("idempotency_conflict", self.book.reserve, self.tenant, "agent-01", self.quote(200), "key")

    def test_expired_quote_cannot_start_work(self):
        self.fund(); q = self.quote(); self.now += 901
        self.fails("quote_expired_or_missing", self.book.reserve, self.tenant, "agent-01", q, "key")

    def test_refund_is_compensating_and_idempotent(self):
        op = self.reserved(); self.book.resolve(self.tenant, op, "committed", digest("x"))
        before = self.book.reconcile(self.tenant)["journal_count"]
        self.book.refund(self.tenant, op)
        self.assertTrue(self.book.refund(self.tenant, op)["replayed"])
        b = self.balance(); self.assertEqual((b["balance"], b["merchant"], b["fees"]), (1000, 0, 0))
        self.assertEqual(self.book.reconcile(self.tenant)["journal_count"], before + 1)

    def test_cross_tenant_operation_and_quote_are_inaccessible(self):
        op = self.reserved(); other = self.book.create_tenant("Other Merchant")["tenant"]
        self.fails("operation_not_found", self.book.operation, other, op)
        self.fails("quote_expired_or_missing", self.book.reserve, other, "agent-01", self.quote(), "key")
        self.assertEqual(self.book.snapshot(other)["journal"], [])

    def test_integer_units_only_and_asset_identity(self):
        for amount in [True, 1.5, "100", 0, -1, 10**12 + 1]:
            self.fails("invalid_integer_units", self.fund, amount)
        self.fails("asset_not_supported", self.book.create_quote, self.tenant, "USDC", 1, 0)
        self.fails("fee_exceeds_price", self.book.create_quote, self.tenant, "CAD", 1, 2)

    def test_journal_is_append_only_and_tampering_is_detected(self):
        self.fund()
        with self.book.db() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE postings SET delta=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM journals")
            db.execute("DROP TRIGGER posting_no_update")
            db.execute("UPDATE postings SET delta=delta+1 WHERE account='customer:agent-01'")
        self.assertFalse(self.book.reconcile(self.tenant)["ok"])

    def test_rollbacks_leave_no_partial_postings(self):
        self.fund()
        with self.assertRaises(RuntimeError):
            with self.book.transaction() as db:
                self.book._post(db, self.tenant, "charge", "interrupted", "CAD", [("merchant", 1), ("customer:agent-01", -1)])
                raise RuntimeError("process-interruption")
        self.assertEqual(self.balance()["balance"], 1000)
        self.assertEqual(self.book.reconcile(self.tenant)["journal_count"], 1)

    def test_seeded_many_operations_preserve_conservation(self):
        self.fund(100000)
        rng = random.Random(42)
        for n in range(150):
            q = self.quote(rng.randint(2, 100), 1)
            op = self.book.reserve(self.tenant, "agent-01", q, f"event-{n}")["id"]
            outcome = rng.choice(["committed", "released", "uncertain"])
            self.book.resolve(self.tenant, op, outcome, digest(n) if outcome == "committed" else None)
            if outcome == "uncertain":
                self.book.resolve(self.tenant, op, "released", reconciled=True)
            if outcome == "committed" and n % 4 == 0:
                self.book.refund(self.tenant, op)
        b = self.balance()
        self.assertEqual(b["balance"] + b["merchant"] + b["fees"], 100000)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_issuer_mint_needs_current_reserves_and_replays_once(self):
        self.fails("reserve_evidence_stale", self.issuer.mint, self.tenant, "agent-01", 1, "mint-1")
        self.attest(100)
        self.issuer.mint(self.tenant, "agent-01", 1_000_000, "mint-1")
        self.assertTrue(self.issuer.mint(self.tenant, "agent-01", 1_000_000, "mint-1")["replayed"])
        self.fails("insufficient_attested_reserves", self.issuer.mint, self.tenant, "agent-01", 1, "mint-2")
        self.assertFalse(self.issuer.state(self.tenant)["real_reserves_verified"])

    def test_expired_reserve_evidence_stops_issuance(self):
        self.attest(100); self.now += 86401
        self.fails("reserve_evidence_stale", self.issuer.mint, self.tenant, "agent-01", 1, "mint-1")

    def test_reserve_proof_cannot_fund_customer_twice(self):
        self.attest(100)
        self.fails("rail_event_already_consumed", self.bank.fund, self.tenant, "agent-01", "USD", 100, "reserve-1")
        self.assertEqual(self.balance("USD")["balance"], 0)

    def test_redemption_claim_locks_reserves_until_verified_payout(self):
        self.attest(100); self.issuer.mint(self.tenant, "agent-01", 1_000_000, "mint-1")
        self.issuer.request_redemption(self.tenant, "agent-01", 500_000, "redeem-1")
        s = self.issuer.state(self.tenant)
        self.assertEqual((s["supply"], s["pending_redemptions"], s["headroom"]), (500_000, 500_000, 0))
        self.fails("insufficient_attested_reserves", self.issuer.mint, self.tenant, "agent-01", 1, "mint-2")
        wrong = self.bank.event(self.tenant, "redemption:redeem-1", "USD", 50, "payout-1")
        self.fails("bank_evidence_binding_mismatch", self.issuer.confirm_redemption, self.tenant, "redeem-1", wrong)
        right = self.bank.event(self.tenant, "redemption:redeem-1", "USD", 50, "payout-1", "debit")
        self.issuer.confirm_redemption(self.tenant, "redeem-1", right)
        self.assertTrue(self.issuer.confirm_redemption(self.tenant, "redeem-1", right)["replayed"])
        s = self.issuer.state(self.tenant)
        self.assertEqual((s["reserve"], s["liabilities"]), (500_000, 500_000))

    def test_redemption_cannot_spend_reserved_or_fractional_fiat(self):
        self.attest(100); self.issuer.mint(self.tenant, "agent-01", 1_000_000, "mint-1")
        q = self.book.create_quote(self.tenant, "issued:" + self.tenant, 900_000, 0)["quote"]
        self.book.reserve(self.tenant, "agent-01", q, "work")
        self.fails("insufficient_available_credits", self.issuer.request_redemption, self.tenant, "agent-01", 500_000, "red")
        self.fails("redemption_requires_whole_fiat_minor_units", self.issuer.request_redemption, self.tenant, "agent-01", 1, "red")

    def test_white_label_identity_freezes_when_used(self):
        self.issuer.configure(self.tenant, "Example CAD Dollar", "EXCAD", "CAD")
        e = self.bank.event(self.tenant, "issuer-reserve", "CAD", 100, "reserve-cad")
        self.issuer.attest(self.tenant, e)
        self.fails("issuer_identity_already_in_use", self.issuer.configure, self.tenant, "Other Name", "OTHER", "USD")

    def test_live_adapters_fail_before_external_io(self):
        for rail in ["bank", "usdc", "issuer"]:
            self.fails("live_connection_not_enrolled", LiveRail(rail).submit, {"amount": 100})

    def test_merchant_payout_reserves_once_and_confirms_once(self):
        op = self.reserved(); self.book.run_report(self.tenant, op)
        payouts = Payouts(self.book)
        first = payouts.request(self.tenant, "CAD", 50, "merchant-bank-ref", "payout-1")
        self.assertTrue(payouts.request(self.tenant, "CAD", 50, "merchant-bank-ref", "payout-1")["replayed"])
        self.assertEqual(self.balance()["merchant"], 49)
        payouts.sandbox_confirm(self.tenant, first["id"], "outbound-bank-1")
        self.assertTrue(payouts.sandbox_confirm(self.tenant, first["id"], "outbound-bank-1")["replayed"])
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])
        self.fails("refund_requires_funded_reversal", self.book.refund, self.tenant, op)

    def test_payout_cannot_reuse_a_funding_event_or_spend_other_workspace(self):
        op = self.reserved(); self.book.run_report(self.tenant, op)
        payouts = Payouts(self.book)
        first = payouts.request(self.tenant, "CAD", 50, "merchant-bank-ref", "payout-1")
        self.fails("rail_event_already_consumed", payouts.sandbox_confirm, self.tenant, first["id"], "deposit-1")
        other = self.book.create_tenant("Other Merchant")["tenant"]
        self.fails("payout_not_found", payouts.sandbox_confirm, other, first["id"], "payout-1")

    def test_issued_assets_require_redemption_and_platform_fees_are_separate(self):
        payouts = Payouts(self.book)
        self.fails("issuer_redemption_required", payouts.request, self.tenant, "issued:" + self.tenant, 1, "bank-ref", "key")
        op = self.reserved(); self.book.run_report(self.tenant, op)
        payouts.request(self.tenant, "CAD", 1, "platform-bank", "platform-fee-1", "platform-fees")
        self.assertEqual(self.balance()["fees"], 0)


    def test_prices_and_reserved_terms_cannot_be_rewritten(self):
        op = self.reserved()
        with self.book.db() as db:
            for statement, value in [("UPDATE quotes SET fee=?", 0), ("UPDATE operations SET amount=?", 1)]:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement, (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM quotes')
        self.assertEqual(self.book.operation(self.tenant, op)['amount'], 100)

    def test_snapshot_refund_and_delivery_status_match_actual_access(self):
        op = self.reserved()
        self.book.run_report(self.tenant, op)
        row = self.book.snapshot(self.tenant)['operations'][0]
        self.assertTrue(row['has_delivery']); self.assertFalse(row['refunded'])
        self.book.refund(self.tenant, op)
        row = self.book.snapshot(self.tenant)['operations'][0]
        self.assertTrue(row['refunded']); self.assertFalse(row['has_delivery'])
        self.assertEqual(row['state'], 'committed')
        events = self.book.snapshot(self.tenant)['journal']
        self.assertEqual(next(x['amount'] for x in events if x['kind']=='refund'), 100)

    def test_all_merchant_keys_including_bootstrap_can_be_revoked_without_cross_workspace_effect(self):
        key = self.book.issue_merchant_key(self.tenant)
        other = self.book.create_tenant('Another Merchant')
        self.book.revoke_merchant_key(self.tenant, key['id'])
        self.fails('unauthorized', self.book.principal, key['api_key'])
        self.book.revoke_merchant_key(self.tenant, 'bootstrap')
        self.fails('unauthorized', self.book.principal, self.created['api_key'])
        self.assertEqual(self.book.principal(other['api_key'])['tenant'], other['tenant'])
        metadata = self.book.keys(self.tenant)
        self.assertTrue(all(x['revoked'] for x in metadata['keys']))
        self.assertNotIn('key_hash', json.dumps(metadata)); self.assertNotIn('api_key', json.dumps(metadata))

    def test_request_key_lookup_survives_reopen_and_cannot_cross_workspaces(self):
        op = self.reserved(key='durable-purchase')
        reopened = Ledger(self.path, lambda: self.now)
        self.assertEqual(reopened.lookup_operation(self.tenant, 'durable-purchase')['id'], op)
        other = reopened.create_tenant('Another Merchant')['tenant']
        self.fails('operation_not_found', reopened.lookup_operation, other, 'durable-purchase')

    def test_issuer_identity_stays_fixed_after_all_tokens_are_redeemed(self):
        self.attest(100)
        self.issuer.mint(self.tenant, 'agent-01', 1_000_000, 'full-issue')
        claim = self.issuer.request_redemption(self.tenant, 'agent-01', 1_000_000, 'full-redeem')
        envelope = self.bank.event(self.tenant, 'redemption:' + claim['request'], 'USD', 100, 'full-payout', direction='debit')
        self.issuer.confirm_redemption(self.tenant, claim['request'], envelope)
        self.assertEqual(self.issuer.state(self.tenant)['reserve'], 0)
        self.assertTrue(self.issuer.state(self.tenant)['identity_locked'])
        self.fails('issuer_identity_already_in_use', self.issuer.configure, self.tenant, 'Changed Currency', 'NEW', 'CAD')

    def test_legacy_workspace_migrates_without_losing_its_key_or_new_workspace_creation(self):
        import hashlib
        path = self.path.parent / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,country TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created INTEGER NOT NULL)')
            db.execute('INSERT INTO tenants VALUES(?,?,?,?,?)', ('hb_legacy','Legacy Merchant','CA',hashlib.sha256(b'hb_test_legacy').hexdigest(),self.now))
        reopened=Ledger(path,lambda:self.now)
        self.assertEqual(reopened.principal('hb_test_legacy')['tenant'],'hb_legacy')
        reopened.revoke_merchant_key('hb_legacy','bootstrap')
        self.fails('unauthorized', reopened.principal, 'hb_test_legacy')
        self.assertTrue(reopened.create_tenant('New Merchant')['tenant'].startswith('hb_'))


class ChainReceiptTests(unittest.TestCase):
    def setUp(self):
        self.token, self.payer, self.recipient = ["0x" + x * 40 for x in ["1", "2", "3"]]
        self.tx, self.block = "0x" + "a" * 64, "0x" + "b" * 64
        self.receipt = {"transactionHash": self.tx, "status": "0x1", "blockNumber": "0x64", "blockHash": self.block,
            "logs": [{"logIndex": "0x0", "address": self.token, "topics": [TRANSFER, "0x" + self.payer[2:].rjust(64, "0"), "0x" + self.recipient[2:].rjust(64, "0")], "data": hex(1_000_000)}]}
        self.tip, self.chain = 111, 84532
        self.adapter = UsdcReceiptVerifier(self.rpc, chain_id=84532, token=self.token)

    def rpc(self, method, params):
        return {"eth_chainId": hex(self.chain), "eth_getTransactionReceipt": self.receipt,
                "eth_getBlockByNumber": {"hash": self.block}, "eth_blockNumber": hex(self.tip)}[method]

    def verify(self):
        return self.adapter.verify(self.tx, payer=self.payer, recipient=self.recipient, amount=1_000_000)

    def test_complete_transfer_and_required_confirmations(self):
        self.assertEqual(self.verify()["confirmations"], 12)
        self.tip = 110
        with self.assertRaisesRegex(Error, "settlement_pending"): self.verify()

    def test_wrong_chain_token_amount_recipient_or_failed_transaction(self):
        original = json.loads(json.dumps(self.receipt))
        for field, value in [("address", self.payer), ("data", hex(1)), ("removed", True)]:
            self.receipt = json.loads(json.dumps(original)); self.receipt["logs"][0][field] = value
            with self.assertRaises(Error): self.verify()
        self.receipt = original; self.chain = 1
        with self.assertRaisesRegex(Error, "chain_mismatch"): self.verify()
        self.chain = 84532; self.receipt["status"] = "0x0"
        with self.assertRaisesRegex(Error, "transaction_not_successful"): self.verify()

    def test_reorg_and_missing_receipt_never_confirm(self):
        self.receipt["blockHash"] = "0x" + "c" * 64
        with self.assertRaisesRegex(Error, "receipt_not_canonical"): self.verify()
        self.receipt = None
        with self.assertRaisesRegex(Error, "settlement_pending"): self.verify()


if __name__ == "__main__":
    unittest.main()
