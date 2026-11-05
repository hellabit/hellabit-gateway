from __future__ import annotations
from .ledger import Error, digest, identifier, units
from .rails import SandboxBank


class Payouts:
    def __init__(self, ledger):
        self.ledger = ledger
        from .operations import Operations
        self.operations = Operations(ledger)

    def request(self, tenant, asset, amount, recipient, key, source="merchant"):
        units(amount); identifier(recipient); identifier(key)
        if source not in ("merchant", "platform-fees"):
            raise Error("invalid_payout_source", 400)
        request_hash, key_hash = digest([asset, amount, recipient, source]), digest(key)
        with self.ledger.transaction() as db:
            self.ledger.assert_payment_writes(db)
            self.ledger.tenant(db, tenant)
            if self.ledger.asset(db, tenant, asset)["rail"] not in ("bank", "usdc"):
                raise Error("issuer_redemption_required")
            old = db.execute("SELECT * FROM payouts WHERE tenant=? AND key_hash=?", (tenant, key_hash)).fetchone()
            if old:
                if old["request_hash"] != request_hash: raise Error("payout_idempotency_conflict")
                intent=db.execute("SELECT id FROM execution_intents WHERE tenant=? AND payout=?",(tenant,old["id"])).fetchone()
                from .outcomes import payout_view
                return {**payout_view(db,old), "intent":intent["id"] if intent else None,"replayed": True}
            if self.ledger._balance(db, tenant, asset, source) < amount:
                raise Error("insufficient_payout_balance")
            # The caller's business identity, not a random restored database row,
            # determines the provider identity. Terms are checked separately.
            payout = "pay_" + digest(["payout-command-v2", tenant, key_hash])[:48]
            db.execute("INSERT INTO payouts(tenant,id,asset,amount,source,recipient,state,key_hash,request_hash,payment_reference) VALUES(?,?,?,?,?,?,?,?,?,?)", (tenant, payout, asset, amount, source, recipient, "pending", key_hash, request_hash, None))
            self.ledger._post(db, tenant, "payout-reserved", payout, asset, [(source, -amount), ("payout:" + payout, amount)])
            intent = self.operations.enqueue(db, tenant, payout, asset, amount, recipient)
            return {"id": payout, "state": "pending", "amount": amount, "asset": asset, "intent": intent}

    def sandbox_confirm(self, tenant, payout, reference):
        identifier(payout); identifier(reference)
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM payouts WHERE tenant=? AND id=?", (tenant, payout)).fetchone()
            if not row: raise Error("payout_not_found", 404)
            if row["state"] == "paid":
                if row["payment_reference"] != reference: raise Error("payout_payment_conflict")
                return {"id": payout, "state": "paid", "replayed": True}
            intent = db.execute("SELECT state FROM execution_intents WHERE tenant=? AND payout=?", (tenant, payout)).fetchone()
            if intent and intent["state"] in ("submitting", "unknown"):
                raise Error("reconcile_existing_submission", 409)
            rail = self.ledger.asset(db, tenant, row["asset"])["rail"]
            if rail == "bank":
                bank = SandboxBank(self.ledger)
                body = bank.event(tenant, "payout:" + payout, row["asset"], row["amount"], reference, "debit")
                proof = bank.verify(body, tenant, "payout:" + payout, row["asset"], row["amount"], "debit")["proof_hash"]
            else:
                proof = digest({"mode": "sandbox", "payout": payout, "asset": row["asset"], "amount": row["amount"], "recipient": row["recipient"], "reference": reference})
            result = self._paid_tx(db, tenant, payout, reference, proof)
            db.execute("""UPDATE execution_intents SET state='confirmed',reference=?,evidence_hash=?,
                lease_hash=NULL,lease_until=NULL,updated=? WHERE tenant=? AND payout=?""",
                (reference, proof, self.ledger.clock(), tenant, payout))
            return result

    def _paid_tx(self, db, tenant, payout, reference, proof):
        row = db.execute("SELECT * FROM payouts WHERE tenant=? AND id=?", (tenant, payout)).fetchone()
        if not row: raise Error("payout_not_found", 404)
        if row["state"] == "paid":
            if row["payment_reference"] != reference: raise Error("payout_payment_conflict")
            return {"id": payout, "state": "paid", "replayed": True}
        if row["state"] != "pending": raise Error("payout_not_pending")
        rail = self.ledger.asset(db, tenant, row["asset"])["rail"]
        from .events import sandbox_connection
        intent=db.execute('SELECT connection FROM execution_intents WHERE tenant=? AND payout=?',(tenant,payout)).fetchone()
        connection=intent[0] if intent else sandbox_connection(rail,row['asset'])
        self.ledger.consume_rail_event(db, tenant, rail, reference, "merchant-payout", proof,connection=connection)
        self.ledger._post(db, tenant, "payout-paid", payout, row["asset"],
            [("payout:" + payout, -row["amount"]), ("clearing:" + rail, row["amount"])],
            {"proof_hash": proof, "mode": "sandbox",'external_reference':reference,'connection':connection})
        db.execute("UPDATE payouts SET state='paid',payment_reference=? WHERE id=?", (reference, payout))
        return {"id": payout, "state": "paid", "mode": "sandbox"}

    def _failed_tx(self, db, tenant, payout, reference, proof):
        row = db.execute("SELECT * FROM payouts WHERE tenant=? AND id=?", (tenant, payout)).fetchone()
        if not row or row["state"] != "pending": raise Error("payout_not_pending")
        self.ledger._post(db, tenant, "payout-failed", payout, row["asset"],
            [("payout:" + payout, -row["amount"]), (row["source"], row["amount"])],
            {"proof_hash": proof, "mode": "sandbox", "provider_reference": reference})
        db.execute("UPDATE payouts SET state='failed',payment_reference=? WHERE id=?", (reference, payout))
        return {"id": payout, "state": "failed", "mode": "sandbox"}
