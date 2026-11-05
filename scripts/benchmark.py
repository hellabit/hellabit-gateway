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
from hellabit.metering import UsageMeter

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
    meter=UsageMeter(ledger);meter.configure(tenant,"baseline","USD",1000)
    metering=[]
    for n in range(args.operations*4):
        before=time.perf_counter();meter.record(tenant,"baseline","usage-"+str(n))
        metering.append((time.perf_counter()-before)*1000)
    with ledger.db() as db:
        database_bytes=db.execute("PRAGMA page_count").fetchone()[0]*db.execute("PRAGMA page_size").fetchone()[0]
    print(json.dumps({"schema": "hellabit.local-baseline.v1", "mode": "single-writer-private-sqlite", "operations": args.operations,
        "seconds": round(elapsed, 4), "completed_jobs_per_second": round(args.operations / elapsed, 2),
        "latency_ms_p50": round(statistics.median(values), 3), "latency_ms_p95": round(values[int(len(values) * .95) - 1], 3),
        "database_bytes":database_bytes,"metered_events":len(metering),
        "metering_ms_first_100_p50":round(statistics.median(metering[:100]),3),
        "metering_ms_last_100_p50":round(statistics.median(metering[-100:]),3),
        "external_transactions": 0, "production_capacity_verified": False}))
