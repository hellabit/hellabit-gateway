# Hellabit Gateway

**Recoverable purchases and durable results for API teams.**

Reserve a budget before work begins. Record its charge with delivery. Reconcile
uncertainty without creating a second payment. Keep each currency, issuer and
merchant's books separate.

Version 0.3 is an operational **local sandbox foundation** with a merchant
console, paid reference API, SDK, ledger, three enrollment profiles, settlement
verification interfaces and executable white-label token contracts. It has no
Stripe integration. No bank, public blockchain or real customer is connected.

## Try the console

Use Python 3.11 or newer with a SQLite library containing the WAL-reset fix
(3.51.3+, or the supported 3.44.6/3.50.7 backport branches). The service has
zero third-party Python dependencies. The launcher checks the loaded SQLite library.

```sh
python3 scripts/run.py --demo
```

Open the loopback URL printed by the command. The clean launcher removes
inherited account, token, SSH-agent, proxy and Python-injection environment
settings. State is private to `.private/`; quit and reopen the same command to
resume it. The service deliberately binds only to `127.0.0.1`.

1. In **Paid work**, add test credits and reserve the invoice total check.
2. Run the check, inspect the computed result and receipt, and return credits if needed.
3. Mark another operation uncertain; its budget stays held until reconciliation.
4. Settle merchant proceeds through a recorded test payout.
5. In **Issuer lab**, choose a brand, record reserve evidence, issue tokens
   and follow a redemption through its unpaid claim and test payout.
6. In **Settlement planning**, prepare separate institution, native-USDC and issuer
   references. Preparation never activates a connection.

All console balances and settlement evidence are synthetic and have no monetary
value. References are opaque identifiers; do not enter credentials or bank
account numbers. This is not a public production server.

## What is implemented

- Transactional reservations with stable operation identities and integer units.
- Balanced, append-only postings and an internally verifiable hash chain.
- Explicit uncertain states, frozen reservations and reconciliation outcomes.
- Real invoice arithmetic with immutable quoted input hashes and atomic result/charge storage.
- Original-identity recovery after a lost reservation response; compensating refunds revoke future retrieval.
- Filtered activity before bounded row limits, with unknown outcomes first.
- Separate merchant and platform proceeds; payout intents prevent overspending.
- Authenticated bank harness and a backend USDC receipt verifier checking chain,
  exact token, payer, recipient, value, canonical block and confirmation depth.
- Separate issuer books: reserve proofs, supply limits, expiry, burns, pending
  redemption claims and verified test payout records.
- A JavaScript SDK with TypeScript declarations and no automatic mutation retry.
- Customer-bound agent capabilities that cannot reprice work or authorize settlement.
- Solidity contracts based on pinned OpenZeppelin components: buyer-signed
  settlement with disclosed fees, role-controlled white-label issuance, reserve
  caps, delayed administrator handover, freezes, pauses and redemption claims.

The contract and the offchain issuer books are independently tested components.
An enrolled production indexer binding their state has not been implemented.
The USDC verifier is tested against controlled RPC responses, not Circle or a
public chain. The EVM proof uses an explicitly named private test dollar.

## Verify and package

Contract and browser development additionally require Node 24 and the locked
development dependencies. One-time setup:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
```

Then run the single verification command:

```sh
python3 scripts/verify.py
```

It checks public dependency advisories, runs the Python/API/process-recovery suite, compiles and executes the contracts
in a private EVM, checks the SDK, proves the browser journeys, packages the source
and emits `evidence/terminal-result.json`. Only the npm advisory lookup contacts
an external service; financial proofs use no public payment network. It does not
install dependencies or perform external transactions. `--skip-browser` is an explicit verification
limit, never equivalent to browser acceptance.

The source archive, its manifest and signature live in `release/`. Runtime data,
keys, browser evidence, owner registration and dependencies are excluded. A
portable checkpoint establishes content custody, not financial readiness.

The console's Developers page issues one-time merchant or customer-bound agent
keys. They are masked by default, clear when leaving Developers, and never
persist in browser storage. Owner-only revocation covers merchant, initial
workspace and customer-bound agent keys. The SDK can look up an operation by
its original purchase key after a timeout. The
runtime is locally validated on macOS. Hosted Linux verification is reported by
GitHub Actions. Its disposable Python environment builds checksum-pinned SQLite
3.54.1 from official source and checks the loaded library before testing; runner
images may otherwise provide a SQLite version without the required WAL fix.

## Connections and issuance

See the [runnable purchase example](docs/QUICKSTART.md), [permission matrix](docs/API.md),
and [product critique and changes](docs/PRODUCT_REVIEW.md), [partner decisions](docs/CONNECTIONS.md),
[architecture](docs/ARCHITECTURE.md), [rail contracts](docs/RAILS.md),
[issuer responsibilities](docs/ISSUERS.md), [economics](docs/ECONOMICS.md),
[activation requirements](docs/ACTIVATION.md) and [security](SECURITY.md).

Bank connectivity needs an institution-specific adapter and agreement. A live
USDC route needs an exact network/token binding, verified RPC and enrolled
signer. A redeemable white-label stablecoin needs an actual legal issuer,
custodian, reserves, redemption arrangements and applicable approvals. Software
does not establish backing, regulatory status or adoption by banks or governments.

New Hellabit code is licensed under Apache-2.0. OpenZeppelin notices accompanying
compiled contract artifacts are retained in `licenses/`. Development dependencies
and their licenses are recorded in the generated dependency inventory.

## Publication sequence

Three nonbinding fit enquiries were submitted on 2026-11-04. There is no approved
provider relationship or live connection. Publish the honestly labeled sandbox
source after release checks; partner approval is not a prerequisite for publishing
software. A public payment-service launch is a different milestone. Do not expose
this loopback owner console through a public tunnel. No repository is published
by the local verification command.
