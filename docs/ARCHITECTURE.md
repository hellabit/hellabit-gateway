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
must be reconciled through a future enrolled production indexer; the sandbox
does not silently equate them.

The journal hash chain detects internal inconsistencies. An attacker who can
rewrite the entire database and its triggers can rewrite that chain. Independent
signed checkpoints and external settlement records are required for stronger
production evidence. The source checkpoint is not a financial-ledger attestation.
