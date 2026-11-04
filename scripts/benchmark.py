#!/usr/bin/env python3
"""A bounded private-ledger baseline; never a network TPS or adoption claim."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hellabit.ledger import Ledger
from hellabit.rails import SandboxBank

parser = argparse.ArgumentParser(); parser.add_argument("--operations", type=int, default=500); args = parser.parse_args()
if not 10 <= args.operations <= 2000: raise SystemExit("Use 10–2000 operations")
with tempfile.TemporaryDirectory() as tmp:
    ledger = Ledger(Path(tmp) / "ledger.sqlite3")
    tenant = ledger.create_tenant("Benchmark Merchant")["tenant"]
    SandboxBank(ledger).fund(tenant, "agent-01", "USD", args.operations * 100, "funding-1")
    quote = ledger.create_quote(tenant, "USD", 100, 1)["quote"]
    times = []; start = time.perf_counter()
    for n in range(args.operations):
        before = time.perf_counter()
        op = ledger.reserve(tenant, "agent-01", quote, "operation-" + str(n))["id"]
        ledger.run_report(tenant, op)
        times.append((time.perf_counter() - before) * 1000)
    elapsed = time.perf_counter() - start
    assert ledger.reconcile(tenant)["ok"]
    values = sorted(times)
    print(json.dumps({"schema": "hellabit.local-baseline.v1", "mode": "single-writer-private-sqlite", "operations": args.operations,
        "seconds": round(elapsed, 4), "completed_jobs_per_second": round(args.operations / elapsed, 2),
        "latency_ms_p50": round(statistics.median(values), 3), "latency_ms_p95": round(values[int(len(values) * .95) - 1], 3),
        "external_transactions": 0, "production_capacity_verified": False}))
