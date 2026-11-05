"""Offchain issuer lifecycle with explicit reserves and redemption liabilities.

This models a sandbox issuer's books. It does not issue a redeemable currency,
attest real reserves or submit a blockchain mint. Contract proof is independent.
"""
import re
from .ledger import Error, canonical, digest, identifier, units
from .rails import SandboxBank
from .control import audit,ensure_schema


class Issuer:
    def __init__(self, ledger):
        self.ledger = ledger
        ensure_schema(ledger,"issuer-audit","")
        if not getattr(ledger,"_issuer_source_migrated",False):
            with ledger.transaction() as db:
                if "source" not in {r[1] for r in db.execute("PRAGMA table_info(redemptions)")}:
                    db.execute("ALTER TABLE redemptions ADD COLUMN source TEXT NOT NULL DEFAULT 'customer'")
            ledger._issuer_source_migrated=True

    def _state(self, db, tenant):
        row = db.execute("SELECT * FROM issuer WHERE tenant=?", (tenant,)).fetchone()
        if not row:
            raise Error("issuer_not_found", 404)
        data = dict(row)
        data["asset"] = "issued:" + tenant
        data["supply"] = -self.ledger._balance(db, tenant, data["asset"], "issuer-liability")
        data["pending_redemptions"] = db.execute("SELECT COALESCE(SUM(amount),0) FROM redemptions WHERE tenant=? AND state='pending'", (tenant,)).fetchone()[0]
        data["liabilities"] = data["supply"] + data["pending_redemptions"]
        data["headroom"] = data["reserve"] - data["liabilities"]
        data["reserve_current"] = data["valid_until"] > self.ledger.clock()
        data['identity_locked'] = bool(db.execute('SELECT 1 FROM reserve_proofs WHERE tenant=? LIMIT 1', (tenant,)).fetchone())
        data["mode"] = "sandbox"
        data["real_reserves_verified"] = False
        data["redemptions"] = [dict(x) for x in db.execute("SELECT id,customer,source,amount,state,payment_reference FROM redemptions WHERE tenant=? ORDER BY rowid DESC LIMIT 20", (tenant,))]
        return data

    def state(self, tenant):
        with self.ledger.db() as db:
            return self._state(db, tenant)

    def configure(self, tenant, name, symbol, currency):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .'-]{1,59}", name):
            raise Error("invalid_issuer_name", 400)
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{2,7}", symbol) or currency not in ("USD", "CAD"):
            raise Error("invalid_issuer_configuration", 400)
        with self.ledger.transaction() as db:
            state = self._state(db, tenant)
            if state['identity_locked'] or state["reserve"] or state["liabilities"]:
                raise Error("issuer_identity_already_in_use")
            db.execute("UPDATE issuer SET name=?,symbol=?,currency=? WHERE tenant=?", (name, symbol, currency, tenant))
            db.execute("UPDATE assets SET symbol=? WHERE tenant=? AND rail='issuer'", (symbol, tenant))
        return self.state(tenant)

    def attest(self, tenant, bank_envelope):
        with self.ledger.transaction() as db:
            state = self._state(db, tenant)
            body = bank_envelope.get("body", {})
            amount = units(body.get("amount"))
            proof = SandboxBank(self.ledger).verify(bank_envelope, tenant, "issuer-reserve", state["currency"], amount)
            reference, request = proof["reference"], digest(bank_envelope)
            old = db.execute("SELECT * FROM reserve_proofs WHERE tenant=? AND reference=?", (tenant, reference)).fetchone()
            if old:
                if old["request_hash"] != request:
                    raise Error("reserve_reference_conflict")
                return {"replayed": True, **self._state(db, tenant)}
            token_units = amount * 10_000
            if state["reserve"] + token_units > 10**15:
                raise Error("reserve_limit")
            self.ledger.consume_rail_event(db, tenant, "bank", reference, "issuer-reserve", request)
            db.execute("INSERT INTO reserve_proofs VALUES(?,?,?,?)", (tenant, reference, request, token_units))
            # Evidence of a new deposit cannot refresh the historical pool.
            expiry=min(state["valid_until"],self.ledger.clock()+86400) if state["reserve"] else self.ledger.clock()+86400
            db.execute("UPDATE issuer SET reserve=reserve+?,valid_until=? WHERE tenant=?", (token_units, expiry, tenant))
            self.ledger._post(db, tenant, "reserve-attestation", reference, state["currency"], [("reserve-bank", amount), ("reserve-source", -amount)], {"proof_hash": request, "mode": "sandbox"})
            return self._state(db, tenant)

    def attest_balance(self,tenant,bank_envelope):
        """Fresh evidence must cover the entire current reserve balance."""
        with self.ledger.transaction() as db:
            self.ledger.assert_payment_writes(db)
            state=self._state(db,tenant)
            amount=units(bank_envelope.get("body",{}).get("amount"))
            proof=SandboxBank(self.ledger).verify(bank_envelope,tenant,"issuer-reserve-balance",state["currency"],amount)
            if amount*10000!=state["reserve"]:raise Error("reserve_balance_mismatch",409)
            self.ledger.consume_rail_event(db,tenant,"bank",proof["reference"],"reserve-balance",proof["proof_hash"])
            db.execute("UPDATE issuer SET valid_until=? WHERE tenant=?",(self.ledger.clock()+86400,tenant))
            audit(self.ledger,db,tenant,"reserve-balance-attested",proof["reference"],{"balance":state["reserve"],"mode":"sandbox"})
        return self.state(tenant)

    def mint(self, tenant, customer, amount, request_id):
        units(amount); identifier(request_id)
        request = digest(["mint", customer, amount])
        with self.ledger.transaction() as db:
            self.ledger.customer(db, tenant, customer)
            old = db.execute("SELECT * FROM issuer_requests WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
            if old:
                if old["request_hash"] != request:
                    raise Error("issuer_request_conflict")
                return {"request": request_id, "replayed": True}
            state = self._state(db, tenant)
            if not state["reserve_current"]:
                raise Error("reserve_evidence_stale")
            if amount > state["headroom"]:
                raise Error("insufficient_attested_reserves")
            seq = self.ledger._post(db, tenant, "issuance", request_id, state["asset"], [("issuer-liability", -amount), ("customer:" + customer, amount)])
            db.execute("INSERT INTO issuer_requests VALUES(?,?,?,?,?)", (tenant, request_id, "mint", request, canonical({"journal": seq})))
            return {"request": request_id, "journal": seq, "mode": "sandbox"}

    def request_redemption(self, tenant, customer, amount, request_id, source="customer"):
        units(amount); identifier(request_id)
        if amount % 10_000:
            raise Error("redemption_requires_whole_fiat_minor_units", 400)
        if source not in ("customer","merchant","platform-fees"):raise Error("invalid_redemption_source",400)
        request = digest([customer, amount]) if source=="customer" else digest([source,amount])
        with self.ledger.transaction() as db:
            if source=="customer":self.ledger.customer(db, tenant, customer)
            elif customer is not None:raise Error("unexpected_customer_binding",400)
            account="customer:"+customer if source=="customer" else source
            old = db.execute("SELECT * FROM redemptions WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
            if old:
                if old["request_hash"] != request:
                    raise Error("redemption_reference_conflict")
                return {**dict(old), "replayed": True}
            state = self._state(db, tenant)
            available = self.ledger._balance(db, tenant, state["asset"], account)
            if source=="customer":available-=self.ledger._held(db, tenant, state["asset"], customer)
            if available < amount:
                raise Error("insufficient_available_credits", 402)
            self.ledger._post(db, tenant, "redemption-burn", request_id, state["asset"], [(account, -amount), ("issuer-liability", amount)])
            db.execute("INSERT INTO redemptions(tenant,id,customer,amount,state,payment_reference,request_hash,source) VALUES(?,?,?,?,?,?,?,?)", (tenant, request_id, customer if source=="customer" else source, amount, "pending", None, request,source))
            return {"request": request_id, "state": "pending", "amount": amount}

    def confirm_redemption(self, tenant, request_id, bank_envelope):
        identifier(request_id)
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM redemptions WHERE tenant=? AND id=?", (tenant, request_id)).fetchone()
            if not row:
                raise Error("redemption_not_found", 404)
            state = self._state(db, tenant)
            proof = SandboxBank(self.ledger).verify(bank_envelope, tenant, "redemption:" + request_id, state["currency"], row["amount"] // 10_000, "debit")
            if row["state"] == "paid":
                if row["payment_reference"] != proof["reference"]:
                    raise Error("redemption_payment_conflict")
                return {"state": "paid", "replayed": True}
            if db.execute("SELECT 1 FROM redemptions WHERE tenant=? AND payment_reference=?", (tenant, proof["reference"])).fetchone():
                raise Error("payment_reference_already_used")
            self.ledger.consume_rail_event(db, tenant, "bank", proof["reference"], "redemption-payout", proof["proof_hash"])
            db.execute("UPDATE redemptions SET state='paid',payment_reference=? WHERE tenant=? AND id=?", (proof["reference"], tenant, request_id))
            db.execute("UPDATE issuer SET reserve=reserve-? WHERE tenant=?", (row["amount"], tenant))
            fiat_units = row["amount"] // 10_000
            self.ledger._post(db, tenant, "redemption-paid", request_id, state["currency"], [("reserve-bank", -fiat_units), ("reserve-source", fiat_units)], {"proof_hash": proof["proof_hash"]})
            return {"state": "paid", "mode": "sandbox"}
