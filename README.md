# Hellabit Gateway

**When a payment reply disappears, recover the original payment.**

A merchant-controlled payment operations engine for API teams: durable payout
intent, held budgets, original-identity recovery, inspectable results and exact
usage accounting. Free Apache-2.0 core. No Stripe dependency or compulsory fee.

Version **0.4.0 is a developer pre-release**. The console moves synthetic value;
real-money submission remains blocked. Canada and the United States are intended
markets. No bank, public-chain signer, legal issuer or customer is enrolled.

## Try it in one command

```sh
python3 scripts/run.py --demo
```

Use Python 3.11+ with patched SQLite: 3.51.3+, or the supported 3.44.6/3.50.7
backport branches. The launcher checks the library actually loaded. Open its
printed loopback URL. Quit and reopen the command to resume your private state.
The core console uses only Python's standard library; it stays on `127.0.0.1`.

1. **Paid work:** add test credits, reserve an invoice total check and inspect its
   computed result. Its charge and durable delivery commit together.
2. Request a test payout. In **Settlement planning**, choose **Test an interrupted
   reply**. The synthetic provider accepts it, but the reply disappears.
3. Choose **Check original payment**. Recovery finds that accepted payment and
   records one settlement. The hold stays reserved while its outcome is unknown.
4. **Settlement planning** also exports a responsibility packet. Saved documents
   never establish legal approval or activate a financial connection.
5. **Issuer lab** keeps branded test issuance, reserve assertions and redemption
   claims separate. Test balances and tokens have no monetary value.

Encrypted recovery rehearsal and the optional ASGI API need a one-time free,
hash-locked dependency setup:

```sh
python3 scripts/bootstrap_api.py
python3 scripts/run.py --demo
```

[Review the exact build with Google](docs/REVIEW.md) using a nine-file bundle; no
GitHub repost or public history rewrite is needed for each review.

No subscription, account registration or paid provider is required for these
local examples. Setup downloads open-source packages. Never expose the owner
console through a public tunnel. Do not enter bank numbers or secrets in draft
connection forms; use opaque references.

## What 0.4 adds

| Capability | Proven software behavior | External enrollment still required |
| --- | --- | --- |
| Durable payout outbox | Intent and hold are atomic; leases fence workers; process-death recovery looks up the original identity | A named institution's submission/status contract and agreement |
| Authenticated event inbox | Signed, timestamp-bound events; replay and conflict checks; no late downgrade | Provider-specific signature and event binding |
| Chain projection and RPC | Bounded HTTPS reads, public-IP pinning, durable cursor, reorg retention, finalized-history stops | Exact endpoint/network/token and independent production readback |
| Optional ASGI API | Pinned RSA access-token signatures, local subject grants, operator MFA and recent-authentication assertions, revocation and rate limits | Your identity realm, keys, TLS certificate and deployment policy |
| Encrypted recovery | AES-GCM snapshots, authenticated new-path restore, ledger check and recovery rehearsal | Production key custody, offsite retention and incident ownership |
| Exact usage metering | Integer micro-units aggregate into native cents; residuals carry forward; closed invoices are immutable | Customer agreement, actual costs and an approved collection method |
| Operating readiness | Current source comparison, ledger/audit checks, opaque evidence records and responsibility packet | Actual entity, functions, jurisdictions and program review |

These components do not constitute a bank, an audited financial service or an
approved stablecoin program. The event indexer makes no financial postings. A
canonical event from one RPC is not independent proof of issuer reserves.

Existing capabilities include customer-bound agent permissions, quote/input
binding, compensating refunds, delivery revocation, separate currency books, an
SDK with TypeScript declarations and private-EVM tests of buyer-authorized
settlement and white-label issuance contracts. CAD/USD ledger postings use cents;
test USDC uses six decimals. No currency conversion is inferred.

## Use it in your project

- [Runnable purchase example](docs/QUICKSTART.md)
- [Recovery, backup, metering, identity and indexing commands](docs/OPERATIONS.md)
- [API routes and permissions](docs/API.md)
- [Google review bundle and release checklist](docs/REVIEW.md)
- [Architecture](docs/ARCHITECTURE.md), [rail contracts](docs/RAILS.md) and [security](SECURITY.md)
- [Operating-role review](docs/OPERATING_ROLE.md), [activation](docs/ACTIVATION.md) and [issuer responsibilities](docs/ISSUERS.md)
- [Commercial economics](docs/ECONOMICS.md), [connection decisions](docs/CONNECTIONS.md) and [product critique](docs/PRODUCT_REVIEW.md)

The SDK accepts a sandbox key or a fresh access-token callback. It never
silently retries a money mutation or changes a purchase key after a timeout.

## Verify and package

Development uses Node 24, locked npm dependencies and headless Chromium. Install
once, alongside the optional Python tools above:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
python3 scripts/verify.py
```

One command checks public npm/OSV advisories, Python/API/process recovery,
contracts in a private EVM, SDK and desktop/mobile journeys. It creates a
reproducible source capsule, source manifest and Ed25519 signature, then writes
`evidence/terminal-result.json`. Advisory lookups contact public services;
financial proofs use synthetic data and no public payment network. Verification
does not install packages, deploy, submit payments or publish a GitHub release.

Source, signatures and compiled contract artifacts are packaged separately from
runtime databases, private keys, owner identity and browser evidence. The public
checkpoint key identifies source content; it is not a settlement authority.
Hosted Linux verification is a separate GitHub Actions result. Its disposable
Python environment builds checksum-pinned SQLite 3.54.1 from official source.

## Inbound business, with a useful free core

Self-hosting stays free. Teams wanting the supported configuration operated for
them can [request a managed-deployment discussion](mailto:a@epv.ai?subject=Hellabit%20managed%20deployment).
Include the use case, intended jurisdiction, existing settlement provider and
approximate volume. Do not send credentials, account numbers or customer data.
Availability, responsibilities and price require an agreement; no paid service
or automatic collection is activated by this repository.

[Report a reproducible integration problem](https://github.com/hellabit/hellabit-gateway/issues/new/choose)
or contribute a narrowly scoped improvement. Private security reports go to
[a@epv.ai](mailto:a@epv.ai?subject=Hellabit%20security). Commercial demand, revenue
and popularity are not established by tests or GitHub stars.

New Hellabit code is Apache-2.0. OpenZeppelin redistribution notices remain in
`licenses/`; exact dependency inventories and locks are included. The published
[0.3.0 source release](https://github.com/hellabit/hellabit-gateway/releases/tag/v0.3.0)
remains immutable. Local verification never publishes its successor.
