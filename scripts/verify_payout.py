#!/usr/bin/env python3
"""Check one lost-reply payout from a bounded, read-only evidence file."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hellabit.ledger import Error, canonical, identifier, units
from hellabit.server import strict_json

MAX_BYTES = 1_000_000
MAX_ROWS = 1000
INPUT_SCHEMA = "hellabit.payout-evidence.v1"
TERMS = {"reference", "asset", "recipient_ref", "amount_units"}
COMMAND = TERMS | {"scope", "key"}
PAYMENT_STATES = ("accepted", "pending", "settled", "failed")


def fields(value, names):
    if not isinstance(value, dict) or value.keys() != names:
        raise Error("invalid_evidence_fields", 400)


def rows(value):
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise Error("invalid_evidence_row_count", 400)
    return value


def check_terms(value):
    for field in ("reference", "asset", "recipient_ref"):
        identifier(value[field])
    units(value["amount_units"])


def check_command(value):
    fields(value, COMMAND)
    check_terms(value)
    identifier(value["scope"])
    identifier(value["key"])


def verify(raw):
    evidence = strict_json(raw)
    fields(evidence, {"schema", "environment", "revision", "command", "attempts", "lookup"})
    if evidence["schema"] != INPUT_SCHEMA or evidence["environment"] not in ("synthetic", "test"):
        raise Error("unsupported_evidence_profile", 400)
    revision = identifier(evidence["revision"])
    command = evidence["command"]
    check_command(command)
    lookup = evidence["lookup"]
    fields(lookup, {"scope", "reference", "coverage", "records", "returns"})
    identifier(lookup["scope"])
    identifier(lookup["reference"])
    if lookup["coverage"] not in ("all-matches", "partial"):
        raise Error("invalid_lookup_coverage", 400)

    violations, gaps, acknowledgements = set(), set(), set()
    lost_reply = False
    for attempt in rows(evidence["attempts"]):
        fields(attempt, {"command", "reply", "payment_id"})
        terms = attempt["command"]
        check_command(terms)
        if any(terms[k] != command[k] for k in ("scope", "reference", "key")):
            violations.add("original_command_identity_changed")
        if any(terms[k] != command[k] for k in TERMS - {"reference"}):
            violations.add("original_command_terms_changed")
        reply = attempt["reply"]
        if reply not in ("lost", "acknowledged", "rejected") or (
                reply != "acknowledged" and attempt["payment_id"] is not None):
            raise Error("invalid_attempt_reply", 400)
        if reply == "acknowledged":
            acknowledgements.add(identifier(attempt["payment_id"]))
        lost_reply |= reply == "lost"
    if not lost_reply:
        gaps.add("lost_reply_not_exercised")
    if lookup["scope"] != command["scope"] or lookup["reference"] != command["reference"]:
        violations.add("lookup_used_wrong_scope_or_reference")
    if lookup["coverage"] != "all-matches":
        gaps.add("complete_reference_lookup_required")

    payments = {}
    for record in rows(lookup["records"]):
        fields(record, TERMS | {"payment_id", "state"})
        check_terms(record)
        payment = identifier(record["payment_id"])
        if record["state"] not in PAYMENT_STATES:
            raise Error("invalid_payment_state", 400)
        if any(record[k] != command[k] for k in TERMS):
            violations.add("provider_record_terms_conflict")
        if payment in payments and payments[payment] != record:
            violations.add("conflicting_payment_snapshots")
        payments[payment] = record

    accepted = {payment for payment, record in payments.items() if record["state"] != "failed"}
    if len(accepted | acknowledgements) > 1:
        violations.add("multiple_accepted_payment_ids")
    if acknowledgements - payments.keys():
        gaps.add("acknowledged_payment_missing_from_lookup")
    if not accepted:
        gaps.add("original_payment_acceptance_unresolved")
    if len(payments) > 1 and len(accepted | acknowledgements) <= 1:
        gaps.add("multiple_provider_records_require_review")

    returns = {}
    for row in rows(lookup["returns"]):
        fields(row, {"return_id", "payment_id", "asset", "amount_units"})
        adjustment = identifier(row["return_id"])
        payment = identifier(row["payment_id"])
        identifier(row["asset"])
        units(row["amount_units"])
        if adjustment in returns and returns[adjustment] != row:
            violations.add("conflicting_return_identity")
        returns[adjustment] = row
        original = payments.get(payment)
        if not original or original["state"] != "settled" or row["asset"] != original["asset"]:
            violations.add("return_without_matching_settlement")

    settled = sum(record["amount_units"] for record in payments.values() if record["state"] == "settled")
    returned = sum(row["amount_units"] for row in returns.values())
    if returned > settled:
        violations.add("returns_exceed_settlement")

    verdict = "fail" if violations else "review" if gaps else "pass"
    state = "unknown"
    if not violations and len(payments) == 1:
        state = next(iter(payments.values()))["state"]
        if returned:
            state = "fully-returned" if returned == settled else "partially-returned"
    return {
        "schema": "hellabit.payout-verification.v1",
        "profile": INPUT_SCHEMA, "revision": revision, "environment": evidence["environment"],
        "evidence_sha256": hashlib.sha256(raw).hexdigest(), "verdict": verdict,
        "violations": sorted(violations), "evidence_gaps": sorted(gaps), "payment_state": state,
        "asset": command["asset"], "accepted_payment_count": len(accepted | acknowledgements),
        "observed_settled_units": settled if not violations else None,
        "observed_returned_units": returned if not violations else None,
        "unsettled_command_units": command["amount_units"] - settled + returned if verdict == "pass" else None,
        "provider_authenticated_by_verifier": False, "financial_writes": 0,
    }


def read_evidence(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise Error("bounded_regular_evidence_file_required", 400)
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Error("evidence_too_large", 400)
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "examples/payout-recovery.json")
    args = parser.parse_args()
    try:
        result = verify(read_evidence(args.evidence))
    except (Error, OSError, RecursionError) as exc:
        print(canonical({"error": exc.code if isinstance(exc, Error) else "evidence_unreadable"}))
        return 2
    print(canonical(result))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
