# Hellabit Gateway

**When a payment reply disappears, recover the original payment.**

A merchant-controlled payment operations engine for API teams. Test whether a
lost reply recovers the original payout and see what still needs attention after
settlement or a return. The Apache-2.0 core also includes durable payout holds,
original-identity recovery and exact usage accounting. Export a payout
reconciliation CSV with held, settled, returned and net paid amounts in their
native units. [Payout report command](docs/OPERATIONS.md#export-the-complete-financial-record)

Version **0.9.1 is a developer preview**. The console moves synthetic value;
real-money submission remains blocked. Canada and the United States are intended
markets. No bank, public-chain signer, legal issuer or customer is enrolled.

## Check a payout without opening an account

```sh
python3 scripts/verify_payout.py
```

The local exercise uses a disposable ledger and synthetic provider. It loses the
reply to a CAD 28.00 payout, reopens the ledger to check the hold, recovers the
original settlement and records a CAD 6.00 return. To check your own test
workflow, pass a bounded evidence file with `--evidence PATH`. The report
separates provider identity from application recovery. [Evidence format and CI use](docs/PAYOUT-VERIFICATION.md)

## Run the sandbox console

```sh
python3 scripts/run.py --demo
```

Use Python 3.11+ with patched SQLite: 3.51.3+, or the supported 3.44.6/3.50.7
backport branches. The launcher checks the library actually loaded. Open its
printed loopback URL. Quit and reopen the command to resume your private state at
the saved address.
The core console uses only Python's standard library; it stays on `127.0.0.1`.

1. **Paid work:** add test credits, reserve an invoice total check and inspect its
   computed result. Its charge and durable delivery commit together.
2. Review and confirm a test payout, then open the recovery queue from **Overview**. Choose
   **Test an interrupted reply**. The test provider accepts it, but the reply
   disappears. Overview shows the amount held and the affected connection.
3. Open the queue again and choose **Check original payment**. Recovery finds
   that accepted payment and records one settlement. The original hold stays
   reserved while its outcome is unknown.
4. **Settlement planning** also exports a responsibility packet. Saved documents
   never establish legal approval or activate a financial connection.
5. **Issuer lab** keeps branded test issuance, reserve assertions and redemption
   claims separate. Payout, issuance and redemption drafts and receipts stay
   with the workspace across tabs and restarts. Test balances and tokens have
   no monetary value.

Encrypted recovery rehearsal and the optional ASGI API need a one-time free,
hash-locked dependency setup:

```sh
python3 scripts/bootstrap_api.py
python3 scripts/run.py --demo
```

No subscription, account registration or paid provider is required for these
local examples. Setup downloads open-source packages. Never expose the owner
console through a public tunnel. Do not enter bank numbers or secrets in draft
connection forms; use opaque references.

## Recovery and operation

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

## Recover through the full lifecycle

The provider store stays bound across ledger relocation. The ordinary operator
commands keep consulting its original identity and reject missing stores or a
changed writer generation. Handover retains its fence, candidate and progress so
the same command can finish after interruption. Receipts and later corrections
are processed in bounded pages. An optional encrypted sealed checkpoint can finish
an already fenced handover after the primary and its local candidate are lost.

Payment views use one accounting snapshot. New movement identities include their
clearing account scope; legacy records keep their original identities. Indexed
account positions are checked against journal postings. API rate accounting is
separate from the ledger writer. Agents require an explicit spending policy and
one-use approval above its threshold.

In the issuer lab, authenticated reserve contradictions are retained, new issuance
stops and existing claims remain recorded. The contract accepts an adverse reserve
observation, checks its order and freshness, and scopes redemption IDs to each holder.

See the [operating commands](docs/OPERATIONS.md) and [conformance contract](docs/CONFORMANCE.md).
The implemented recovery profiles are synthetic. A live provider contract, an approved
account and recovery of arbitrary unarchived production changes are outside this proof.

## Use it in your project

- [Runnable purchase example](docs/QUICKSTART.md)
- [Recovery, backup, metering, identity and indexing commands](docs/OPERATIONS.md)
- [API routes and permissions](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md), [rail contracts](docs/RAILS.md) and [security](SECURITY.md)
- [Operating-role review](docs/OPERATING_ROLE.md), [activation](docs/ACTIVATION.md) and [issuer responsibilities](docs/ISSUERS.md)
- [Commercial economics](docs/ECONOMICS.md), [connection decisions](docs/CONNECTIONS.md)

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

Only the files explicitly listed in `PUBLIC_FILES.json` enter a source package.

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

## Verification for an existing payout workflow

The verifier stays free. For help setting up a regression check, identify one
test payout flow, its provider profile and the failure you need to exercise.
Agree on the capture, expected accounting outcome and deadline before work
starts. The resulting evidence and CI check belong with your application.
Share a synthetic reproduction when you enquire.
[Discuss payout verification](mailto:a@epv.ai?subject=Hellabit%20payout%20verification)

Managed gateway deployment is a separate engagement with its own operating
scope and agreement. [Discuss deployment](mailto:a@epv.ai?subject=Hellabit%20managed%20deployment)

[Report a reproducible integration problem](https://github.com/hellabit/hellabit-gateway/issues/new/choose)
or contribute a narrowly scoped improvement. Private security reports go to
[a@epv.ai](mailto:a@epv.ai?subject=Hellabit%20security). Commercial demand, revenue
and popularity are not established by tests or GitHub stars.

New Hellabit code is Apache-2.0. OpenZeppelin redistribution notices remain in
`licenses/`; exact dependency inventories and locks are included. The published
[0.3.0 source release](https://github.com/hellabit/hellabit-gateway/releases/tag/v0.3.0)
remains immutable. Local verification never publishes its successor.
