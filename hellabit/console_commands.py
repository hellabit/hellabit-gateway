"""Recoverable two-step money commands for the loopback owner console."""
import json
import secrets

from .issuer import Issuer
from .ledger import Error, canonical, identifier, units
from .payouts import Payouts

SCHEMA = """
CREATE TABLE console_commands(
 tenant TEXT NOT NULL REFERENCES tenants(id), action TEXT NOT NULL
  CHECK(action IN ('payout','mint','redeem')),
 id TEXT NOT NULL UNIQUE, terms TEXT NOT NULL, legacy_key TEXT,
 state TEXT NOT NULL CHECK(state IN ('ready','recorded','discarded')),
 result TEXT, PRIMARY KEY(tenant,action),
 CHECK((state='recorded')=(result IS NOT NULL)),
 CHECK(legacy_key IS NULL OR action='payout'));
"""

RECEIPTS_SCHEMA = """
CREATE TABLE console_commands_v10(
 tenant TEXT NOT NULL REFERENCES tenants(id), action TEXT NOT NULL
  CHECK(action IN ('payout','mint','redeem')),
 sequence INTEGER NOT NULL CHECK(sequence>0),
 id TEXT NOT NULL UNIQUE, terms TEXT NOT NULL, legacy_key TEXT,
 state TEXT NOT NULL CHECK(state IN ('ready','recorded','discarded')),
 result TEXT, PRIMARY KEY(tenant,action,sequence),
 CHECK((state='recorded')=(result IS NOT NULL)),
 CHECK(legacy_key IS NULL OR action='payout'));
INSERT INTO console_commands_v10
 SELECT tenant,action,1,id,terms,legacy_key,state,result FROM console_commands;
DROP TABLE console_commands;
ALTER TABLE console_commands_v10 RENAME TO console_commands;
CREATE UNIQUE INDEX console_single_draft ON console_commands(tenant,action) WHERE state='ready';
"""

TERMS = {
    "payout": {"asset", "amount", "recipient"},
    "mint": {"customer", "amount"},
    "redeem": {"customer", "amount", "source"},
}


def _view(row):
    if row is None:
        return None
    return {
        "id": row["id"], "state": row["state"], "terms": json.loads(row["terms"]),
        "legacy_key": row["legacy_key"],
        "result": json.loads(row["result"]) if row["result"] else None,
    }


class ConsoleCommands:
    def __init__(self, book):
        self.book = book

    def list(self, tenant):
        with self.book.db() as db:
            db.execute("BEGIN")
            self.book.tenant(db, tenant)
            result = {}
            for action in TERMS:
                row = self._latest(db, tenant, action)
                if row is not None:
                    result[action] = _view(row)
            return result

    def get(self, tenant, action, command_id):
        identifier(command_id)
        with self.book.db() as db:
            row = self._exact(db, tenant, action, command_id)
            if row is None:
                raise Error("console_command_not_found", 404)
            return _view(row)

    def prepare(self, tenant, action, terms, after, legacy_key=None):
        if not isinstance(terms, dict) or terms.keys() != TERMS[action]:
            raise Error("invalid_command_terms", 400)
        units(terms["amount"])
        if action == "payout":
            identifier(terms["asset"])
            identifier(terms["recipient"])
        elif action == "mint" or terms["source"] == "customer":
            identifier(terms["customer"])
        elif terms["source"] not in ("merchant", "platform-fees") or terms["customer"] is not None:
            raise Error("invalid_redemption_source", 400)
        if action == "redeem" and terms["amount"] % 10_000:
            raise Error("redemption_requires_whole_fiat_minor_units", 400)
        if after is not None:
            identifier(after)
        if legacy_key is not None:
            if action != "payout":
                raise Error("legacy_payout_key_only", 400)
            identifier(legacy_key)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            self.book.assert_payment_writes(db)
            row = self._latest(db, tenant, action)
            if (row["id"] if row else None) != after or (row and row["state"] == "ready"):
                raise Error("console_command_changed")
            if action == "payout":
                if self.book.asset(db, tenant, terms["asset"])["rail"] not in ("bank", "usdc"):
                    raise Error("issuer_redemption_required")
            elif terms["customer"] is not None:
                self.book.customer(db, tenant, terms["customer"])
            command_id = "console-" + secrets.token_hex(24)
            sequence = row["sequence"] + 1 if row else 1
            db.execute("""INSERT INTO console_commands(tenant,action,sequence,id,terms,legacy_key,state)
                VALUES(?,?,?,?,?,?,'ready')""", (tenant, action, sequence, command_id, canonical(terms), legacy_key))
            return _view(self._exact(db, tenant, action, command_id))

    def finish(self, tenant, action, command_id, *, discard=False):
        identifier(command_id)
        payout = Payouts(self.book) if action == "payout" and not discard else None
        with self.book.transaction() as db:
            row = self._exact(db, tenant, action, command_id)
            if row is None:
                raise Error("console_command_changed")
            if row["state"] != "ready":
                if not discard and row["state"] == "discarded":
                    raise Error("console_command_discarded")
                return _view(row)
            result = None
            if not discard:
                self.book.assert_payment_writes(db)
                terms = json.loads(row["terms"])
                if action == "payout":
                    result = payout._request(db, tenant, **terms, key=row["legacy_key"] or command_id,
                                             source="merchant")
                elif action == "mint":
                    result = Issuer(self.book)._mint(db, tenant, **terms, request_id=command_id)
                else:
                    result = Issuer(self.book)._request_redemption(db, tenant, **terms, request_id=command_id)
            # The receipt and the effect commit together, including the payout hold and outbox.
            db.execute("UPDATE console_commands SET state=?, result=? WHERE tenant=? AND action=? AND id=?",
                       ("discarded" if discard else "recorded", canonical(result) if result else None,
                        tenant, action, command_id))
            return _view(self._exact(db, tenant, action, command_id))

    @staticmethod
    def _latest(db, tenant, action):
        return db.execute("""SELECT * FROM console_commands WHERE tenant=? AND action=?
                          ORDER BY sequence DESC LIMIT 1""",
                          (tenant, action)).fetchone()

    @staticmethod
    def _exact(db, tenant, action, command_id):
        return db.execute("SELECT * FROM console_commands WHERE tenant=? AND action=? AND id=?",
                          (tenant, action, command_id)).fetchone()
