"""Exact usage invoices. These records never collect or debit money."""
from __future__ import annotations
from datetime import datetime, timezone
import re
import secrets
from .control import audit, ensure_schema
from .ledger import Error, digest, identifier, units

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_plans(
 tenant TEXT NOT NULL,id TEXT NOT NULL,currency TEXT NOT NULL,rate_micro INTEGER NOT NULL,
 base_minor INTEGER NOT NULL,PRIMARY KEY(tenant,id));
CREATE TRIGGER IF NOT EXISTS billing_plan_immutable BEFORE UPDATE ON billing_plans
 BEGIN SELECT RAISE(ABORT,'billing_terms_immutable'); END;
CREATE TABLE IF NOT EXISTS usage_events(
 tenant TEXT NOT NULL,key_hash TEXT NOT NULL,plan TEXT NOT NULL,period TEXT NOT NULL,
 quantity INTEGER NOT NULL,micro INTEGER NOT NULL,request_hash TEXT NOT NULL,created INTEGER NOT NULL,
 PRIMARY KEY(tenant,key_hash));
CREATE TRIGGER IF NOT EXISTS usage_no_update BEFORE UPDATE ON usage_events BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS usage_no_delete BEFORE DELETE ON usage_events BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TABLE IF NOT EXISTS usage_invoices(
 tenant TEXT NOT NULL,plan TEXT NOT NULL,period TEXT NOT NULL,id TEXT NOT NULL,
 currency TEXT NOT NULL,usage_micro INTEGER NOT NULL,opening_carry_micro INTEGER NOT NULL,
 usage_minor INTEGER NOT NULL,base_minor INTEGER NOT NULL,due_minor INTEGER NOT NULL,
 closing_carry_micro INTEGER NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(tenant,plan,period));
CREATE TRIGGER IF NOT EXISTS invoice_no_update BEFORE UPDATE ON usage_invoices BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_delete BEFORE DELETE ON usage_invoices BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

def period(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value):
        raise Error("invalid_billing_period", 400)
    return value

class UsageMeter:
    def __init__(self, book):
        self.book = book
        ensure_schema(book, "metering", SCHEMA)

    def current_period(self):
        return datetime.fromtimestamp(self.book.clock(), timezone.utc).strftime("%Y-%m")

    def configure(self, tenant, plan, currency, rate_micro, base_minor=0):
        identifier(plan); units(rate_micro, zero=True); units(base_minor, zero=True)
        if currency not in ("CAD", "USD"): raise Error("billing_currency_not_supported", 400)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            old = db.execute("SELECT * FROM billing_plans WHERE tenant=? AND id=?", (tenant, plan)).fetchone()
            if old:
                if any(old[k] != v for k, v in {"currency":currency,"rate_micro":rate_micro,"base_minor":base_minor}.items()):
                    raise Error("billing_plan_immutable", 409)
                return dict(old)
            db.execute("INSERT INTO billing_plans VALUES(?,?,?,?,?)", (tenant,plan,currency,rate_micro,base_minor))
            audit(self.book, db, tenant, "billing-plan-recorded", plan, {"currency":currency,"collection_enabled":False})
        return {"plan":plan,"currency":currency,"collection_enabled":False}

    def record(self, tenant, plan, key, quantity=1):
        identifier(plan); identifier(key)
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise Error("invalid_usage_quantity", 400)
        request_hash, key_hash = digest([plan,quantity]), digest(key)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            old = db.execute("SELECT * FROM usage_events WHERE tenant=? AND key_hash=?", (tenant,key_hash)).fetchone()
            if old:
                if old["request_hash"] != request_hash: raise Error("usage_idempotency_conflict", 409)
                return {"period":old["period"],"quantity":old["quantity"],"micro":old["micro"],"replayed":True}
            terms = db.execute("SELECT * FROM billing_plans WHERE tenant=? AND id=?", (tenant,plan)).fetchone()
            if not terms: raise Error("billing_plan_not_found", 404)
            month = self.current_period()
            if db.execute("SELECT 1 FROM usage_invoices WHERE tenant=? AND plan=? AND period>=?", (tenant,plan,month)).fetchone():
                raise Error("billing_period_already_closed", 409)
            amount = terms["rate_micro"] * quantity
            total = db.execute("SELECT COALESCE(SUM(micro),0) FROM usage_events WHERE tenant=? AND plan=? AND period=?",
                               (tenant,plan,month)).fetchone()[0]
            if amount + total > 2**63 - 10000: raise Error("usage_amount_limit", 409)
            db.execute("INSERT INTO usage_events VALUES(?,?,?,?,?,?,?,?)",
                       (tenant,key_hash,plan,month,quantity,amount,request_hash,self.book.clock()))
        return {"period":month,"quantity":quantity,"micro":amount,"replayed":False,"collection_enabled":False}

    def _preview(self, db, tenant, plan, month):
        terms = db.execute("SELECT * FROM billing_plans WHERE tenant=? AND id=?", (tenant,plan)).fetchone()
        if not terms: raise Error("billing_plan_not_found", 404)
        prior = db.execute("""SELECT * FROM usage_invoices WHERE tenant=? AND plan=?
            AND period<? ORDER BY period DESC LIMIT 1""", (tenant,plan,month)).fetchone()
        carry = prior["closing_carry_micro"] if prior else 0
        total = db.execute("SELECT COALESCE(SUM(micro),0) FROM usage_events WHERE tenant=? AND plan=? AND period=?",
                           (tenant,plan,month)).fetchone()[0]
        minor, remainder = divmod(total + carry, 10000)
        if minor + terms["base_minor"] > 10**12: raise Error("invoice_amount_limit", 409)
        return {"currency":terms["currency"],"period":month,"usage_micro":total,"opening_carry_micro":carry,
                "usage_minor":minor,"base_minor":terms["base_minor"],"due_minor":minor+terms["base_minor"],
                "closing_carry_micro":remainder,"collection_enabled":False,"native_decimals":2}

    def preview(self, tenant, plan, month=None):
        month = period(month or self.current_period()); identifier(plan)
        with self.book.db() as db:
            self.book.tenant(db, tenant)
            return self._preview(db,tenant,plan,month)

    def close(self, tenant, plan, month):
        period(month); identifier(plan)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            old = db.execute("SELECT * FROM usage_invoices WHERE tenant=? AND plan=? AND period=?", (tenant,plan,month)).fetchone()
            if old: return {**dict(old),"replayed":True,"collection_enabled":False}
            if month >= self.current_period(): raise Error("billing_period_still_open", 409)
            latest = db.execute("SELECT MAX(period) FROM usage_invoices WHERE tenant=? AND plan=?", (tenant,plan)).fetchone()[0]
            if latest and month <= latest: raise Error("billing_period_out_of_order", 409)
            # Never skip unbilled earlier usage when advancing the carry.
            first = db.execute("""SELECT MIN(period) FROM usage_events WHERE tenant=? AND plan=?
                AND period>? AND period<?""", (tenant,plan,latest or "",month)).fetchone()[0]
            if first: raise Error("earlier_usage_period_must_close_first", 409)
            quote = self._preview(db,tenant,plan,month)
            id_ = "invoice_" + secrets.token_hex(12)
            db.execute("INSERT INTO usage_invoices VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (tenant,plan,month,id_,quote["currency"],quote["usage_micro"],quote["opening_carry_micro"],
                 quote["usage_minor"],quote["base_minor"],quote["due_minor"],quote["closing_carry_micro"],self.book.clock()))
            audit(self.book, db, tenant, "usage-invoice-recorded", id_, {"period":month,"collection_enabled":False})
        return {"id":id_,**quote,"replayed":False}
