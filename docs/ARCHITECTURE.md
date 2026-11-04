# Architecture

The Python process owns one SQLite WAL database with full synchronous writes.
Each mutation acquires a transaction before inspecting available balances. This
is a single-database, single-writer design with concurrent request admission;
it does not claim distributed or unbounded throughput.

```
merchant / agent application
          │ workspace capability
          ▼
quote → reserve → committed delivery
             └→ uncertain → explicit reconciliation
          │
          ▼
balanced journal + asset-specific positions
          │
verified settlement adapters / payout intents
```

The owner-only loopback console uses an HttpOnly SameSite cookie, exact Host and
Origin checks, CSRF token, bounded strict JSON and a fixed asset allowlist. SDK
capabilities are hash-stored and scoped to one workspace. They cannot issue
tokens, invent funding evidence, approve payouts or enable a live route.

Balances are signed integer postings. Ordinary customer positions cannot spend
reserved funds. Every posting group sums to zero in one explicitly identified
asset. CAD and USD use minor units; USDC and issued test tokens use six decimals.
Display symbols never determine asset identity. No exchange rate is inferred.

Funding requires a trusted backend adapter. The bank harness authenticates a
test event and binds workspace, subject, currency, amount, direction and reference.
A rail event can be consumed once across customer funding, reserve evidence and
payout. Unknown outcomes preserve reservations; neither a UI banner nor a
timeout establishes settlement.

The reference report and its charge share one database commit. Other applications
must authenticate their own completion evidence. A result hash identifies bytes;
it is not an independent assessment of work quality. A refund is a compensating
posting and revokes reference-report access. Already-disbursed proceeds need a
funded reversal before credits can be returned.

Issuer supply is a distinct liability. Redemption burns supply but creates a
pending fiat claim, so its reserve capacity cannot be reused. Reserve evidence
expires for further issuance. Offchain reserves and deployed contract supply
still need an enrolled reconciliation policy. The 0.4 event indexer retains
contract-scoped Transfer history, cursors and finality; it makes no financial
postings and does not silently equate events with bank reserves.

The journal hash chain detects internal inconsistencies. An attacker who can
rewrite the entire database and its triggers can rewrite that chain. Independent
signed checkpoints and external settlement records are required for stronger
production evidence. The source checkpoint is not a financial-ledger attestation.

## Recovery and execution in 0.4

A payout request atomically reserves merchant proceeds and stores an immutable
execution intent. A worker claims a short, fenced lease and checks the original
provider identity before submitting. A missing/invalid reply becomes unknown;
lease expiry does the same. Unknown or submitting work blocks another submission
on that connection. Reconciliation can read the original provider identity; a
missing lookup is inconclusive and cannot release the hold or create a new key.

The independent synthetic provider persists acceptance before replying. Tests
terminate a child process immediately after that acceptance, reopen the gateway,
expire its lease, recover the original receipt and prove one payout posting. This
is bounded recovery evidence, not a claim of exactly-once behavior for arbitrary
banks or networks. Actual adapters need equivalent supported identity semantics.

The signed inbox authenticates timestamp and raw bytes before parsing, retains
deduplication/conflict evidence and permits no terminal-state downgrade. Provider
receipt bindings, intent state, journal and observation commit together. Control
evidence has a separate append-only hash chain checked by the readiness report.
An administrator controlling the entire store can still rewrite it; independent
checkpoints are needed beyond that trust boundary.

Read-only TLS RPC pins a public resolved IP while preserving hostname certificate
validation. The persistent indexer has one file-lock writer, bounded backfill,
retained tentative reorg history and a stop on finalized-history contradictions.
One endpoint is a trust dependency; this is not a multi-provider consensus client.

The optional ASGI process uses maintained JWT/TLS/HTTP components and local subject
grants. Its capabilities exclude owner bootstrap and synthetic settlement. The
core console remains loopback-only. Encrypted snapshots use SQLite's backup API
and AES-GCM; authenticated restores always create a new database path. Key custody
and offsite retention remain explicit deployment responsibilities.

A separate usage meter records exact integer micro-units with original event keys,
immutable plan terms and carried invoice residuals. It never credits customer
balances, performs FX or collects a payment. Readiness reports distinguish current
source proof, machine-observed consistency, reported documents and external
approvals that have not been established.
