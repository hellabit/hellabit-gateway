"""Capture one interrupted payout from a disposable ledger and provider."""

from pathlib import Path
import secrets
import tempfile
import time

from . import __version__
from .fixtures import SandboxProvider
from .ledger import Ledger
from .operations import Operations
from .payouts import Payouts
from .rails import SandboxBank


def _command(job):
    return {"scope":job["connection"], "key":job["external_key"], "reference":job["payout"],
            "asset":job["asset"], "recipient_ref":job["recipient"], "amount_units":job["amount"]}


def capture_sandbox():
    started = time.time_ns() // 1_000_000
    origin = time.monotonic_ns()
    attempts = []
    interrupted_at = None

    def observed_at():
        return started + (time.monotonic_ns() - origin) // 1_000_000

    def lose_reply():
        raise TimeoutError("interrupted after synthetic provider acceptance")

    class InterruptedProvider(SandboxProvider):
        def submit(self, job):
            nonlocal interrupted_at
            try:
                return super().submit(job)
            except TimeoutError:
                interrupted_at = observed_at()
                attempts.append({"command":_command(job), "reply":"lost", "payment_id":None})
                raise

    with tempfile.TemporaryDirectory(prefix="hellabit-recovery-probe-") as root:
        path = Path(root) / "ledger.sqlite3"
        book = Ledger(path)
        tenant = book.create_tenant("Recovery Exercise")["tenant"]
        SandboxBank(book).fund(tenant, "agent-01", "CAD", 2800, "probe-funding")
        quote = book.create_quote(tenant, "CAD", 2800, 0)["quote"]
        book.run_report(tenant, book.reserve(tenant, "agent-01", quote, "probe-work")["id"])
        payout = Payouts(book).request(tenant, "CAD", 2800, "sample-recipient", "probe-payment")
        intent = payout["intent"]
        provider = SandboxProvider.for_ledger(book)
        interrupted = InterruptedProvider(provider.path, after_accept=lose_reply, require_existing=True)
        Operations(book).process_one(tenant, interrupted, intent)

        def readback(ledger):
            row = Operations(ledger).get(tenant, intent)
            with ledger.db() as db:
                held = ledger._balance(db, tenant, row["asset"], "payout:" + row["payout"])
            return {"command":_command(row), "intent_id":intent, "state":row["state"],
                    "payment_id":row["reference"], "held_units":held,
                    "settled_units":row["original_settled_units"], "returned_units":row["returned_units"],
                    "observed_at_ms":observed_at()}

        before = readback(Ledger(path))
        operations = Operations(Ledger(path))
        operations.reconcile(tenant, intent, provider)
        command = before["command"]
        provider.return_payment(command["key"], "probe-return", 600)
        operations.reconcile(tenant, intent, provider)
        after = readback(Ledger(path))

        receipt = provider.lookup(command["key"])
        adjustments = provider.adjustments(command["key"])
        return {
            "schema":"hellabit.payout-evidence.v2", "environment":"synthetic",
            "revision":"hellabit-" + __version__, "command":command, "attempts":attempts,
            "run":{"id":"sandbox-" + secrets.token_hex(12), "profile":"settled-after-lost-reply.v1",
                   "started_at_ms":started, "interrupted_at_ms":interrupted_at, "deadline_ms":10_000},
            "application":{"before":before, "after":after},
            "lookup":{"scope":command["scope"], "reference":command["reference"], "coverage":"all-matches",
                      "records":[{"payment_id":receipt["reference"], "reference":command["reference"],
                                  "asset":receipt["asset"], "recipient_ref":receipt["recipient"],
                                  "amount_units":receipt["amount"], "state":receipt["status"]}],
                      "returns":[{"return_id":row["event_id"], "payment_id":row["original_reference"],
                                  "asset":row["asset"], "amount_units":row["amount"]} for row in adjustments]},
        }
