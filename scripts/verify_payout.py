#!/usr/bin/env python3
"""Verify a lost-reply payout or run a local synthetic recovery exercise."""
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
INPUT_SCHEMA = "hellabit.payout-evidence.v2"
LEGACY_SCHEMA = "hellabit.payout-evidence.v1"
RECOVERY_PROFILE = "settled-after-lost-reply.v1"
TERMS = {"reference", "asset", "recipient_ref", "amount_units"}
COMMAND = TERMS | {"scope", "key"}
PAYMENT_STATES = ("accepted", "pending", "settled", "failed")
READBACK = {"command", "intent_id", "state", "payment_id", "held_units",
            "settled_units", "returned_units", "observed_at_ms"}


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


def recovery_result(evidence, command, payments, settled, returned, expected_run_id, max_recovery_ms):
    violations, gaps = set(), set()
    if evidence["schema"] == LEGACY_SCHEMA:
        return violations, {"application_readback_missing", "execution_context_missing"}, None

    run = evidence["run"]
    fields(run, {"id", "profile", "started_at_ms", "interrupted_at_ms", "deadline_ms"})
    identifier(run["id"])
    if run["profile"] != RECOVERY_PROFILE:
        raise Error("unsupported_recovery_profile", 400)
    units(run["deadline_ms"])
    if run["deadline_ms"] > 600_000:
        raise Error("recovery_deadline_out_of_range", 400)
    started, interrupted = run["started_at_ms"], run["interrupted_at_ms"]
    if type(started) is not int or type(interrupted) is not int or not 0 < started <= interrupted <= 10**15:
        raise Error("invalid_capture_time", 400)
    if expected_run_id is not None and run["id"] != expected_run_id:
        violations.add("execution_run_mismatch")
    application = evidence["application"]
    fields(application, {"before", "after"})
    before, after = application["before"], application["after"]
    for record in (before, after):
        fields(record, READBACK)
        check_command(record["command"])
        identifier(record["intent_id"])
        if record["payment_id"] is not None:
            identifier(record["payment_id"])
        for field in ("held_units", "settled_units", "returned_units"):
            units(record[field], zero=True)
        observed = record["observed_at_ms"]
        if type(observed) is not int or not interrupted <= observed <= 10**15:
            raise Error("invalid_capture_time", 400)
        if record["state"] not in ("prepared", "submitting", "unknown", "awaiting_settlement", "confirmed", "failed"):
            raise Error("invalid_application_state", 400)
        if record["command"] != command:
            violations.add("application_command_mismatch")
    if before["intent_id"] != after["intent_id"]:
        violations.add("application_intent_changed")
    if (before["state"], before["payment_id"], before["held_units"], before["settled_units"],
            before["returned_units"]) != ("unknown", None, command["amount_units"], 0, 0):
        violations.add("application_interruption_not_observed")
    if after["observed_at_ms"] < before["observed_at_ms"]:
        raise Error("capture_time_out_of_order", 400)
    elapsed = after["observed_at_ms"] - interrupted
    deadline = min(run["deadline_ms"], max_recovery_ms) if max_recovery_ms is not None else run["deadline_ms"]
    if elapsed > deadline:
        violations.add("recovery_deadline_exceeded")
    if len(payments) != 1 or settled != command["amount_units"]:
        gaps.add("settled_provider_record_required_for_recovery")
    else:
        payment_id = next(iter(payments))
        if (after["state"], after["payment_id"]) != ("confirmed", payment_id):
            violations.add("application_not_recovered")
        if (after["held_units"], after["settled_units"], after["returned_units"]) != (0, settled, returned):
            violations.add("application_accounting_mismatch")
    return violations, gaps, elapsed


def verify(raw, *, expected_revision=None, expected_run_id=None, max_recovery_ms=None):
    if max_recovery_ms is not None and (type(max_recovery_ms) is not int or not 0 < max_recovery_ms <= 600_000):
        raise Error("recovery_deadline_out_of_range", 400)
    evidence = strict_json(raw)
    schema = evidence.get("schema") if isinstance(evidence, dict) else None
    expected = {"schema", "environment", "revision", "command", "attempts", "lookup"}
    fields(evidence, expected | {"run", "application"} if schema == INPUT_SCHEMA else expected)
    if schema not in (LEGACY_SCHEMA, INPUT_SCHEMA) or evidence["environment"] not in ("synthetic", "test"):
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

    identity_verdict = "fail" if violations else "review" if gaps else "pass"
    state = "unknown"
    if not violations and len(payments) == 1:
        state = next(iter(payments.values()))["state"]
        if returned:
            state = "fully-returned" if returned == settled else "partially-returned"
    recovery_violations, recovery_gaps, elapsed = recovery_result(
        evidence, command, payments, settled, returned, expected_run_id, max_recovery_ms,
    )
    if expected_revision is not None and revision != expected_revision:
        recovery_violations.add("application_revision_mismatch")
    recovery_verdict = "fail" if recovery_violations else "review" if recovery_gaps or identity_verdict != "pass" else "pass"
    verdict = "fail" if violations or recovery_violations else "review" if gaps or recovery_gaps else "pass"
    return {
        "schema": "hellabit.payout-verification.v2",
        "profile": schema, "revision": revision, "environment": evidence["environment"],
        "evidence_sha256": hashlib.sha256(raw).hexdigest(), "verdict": verdict,
        "provider_identity_verdict": identity_verdict, "application_recovery_verdict": recovery_verdict,
        "recovery_violations": sorted(recovery_violations), "recovery_evidence_gaps": sorted(recovery_gaps),
        "recovery_elapsed_ms": elapsed, "execution_run_id": evidence["run"]["id"] if schema == INPUT_SCHEMA else None,
        "violations": sorted(violations), "evidence_gaps": sorted(gaps), "payment_state": state,
        "asset": command["asset"], "accepted_payment_count": len(accepted | acknowledgements),
        "observed_settled_units": settled if not violations else None,
        "observed_returned_units": returned if not violations else None,
        "unsettled_command_units": command["amount_units"] - settled + returned if identity_verdict == "pass" else None,
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
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--capture-output", type=Path)
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-run-id")
    parser.add_argument("--max-recovery-ms", type=int)
    args = parser.parse_args()
    if args.evidence is not None and args.capture_output is not None:
        parser.error("--capture-output is for the local sandbox exercise")
    ci = (args.expected_revision, args.expected_run_id, args.max_recovery_ms)
    if any(value is not None for value in ci) and (None in ci or args.evidence is None):
        parser.error("CI checks require --evidence, --expected-revision, --expected-run-id and --max-recovery-ms")
    try:
        if args.evidence is None:
            from hellabit.recovery_probe import capture_sandbox
            raw = (canonical(capture_sandbox()) + "\n").encode()
            result = verify(raw)
            if args.capture_output is not None:
                from hellabit.backup import _write_new
                _write_new(args.capture_output, raw)
            result["executed_local_sandbox"] = True
        else:
            result = verify(read_evidence(args.evidence), expected_revision=args.expected_revision,
                            expected_run_id=args.expected_run_id, max_recovery_ms=args.max_recovery_ms)
            result["executed_local_sandbox"] = False
    except (Error, OSError, RecursionError) as exc:
        print(canonical({"error": exc.code if isinstance(exc, Error) else "evidence_unreadable"}))
        return 2
    print(canonical(result))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
