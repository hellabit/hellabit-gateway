# One command for each operating outcome

Run commands from the source root. The clean launcher drops inherited account,
proxy, token and SSH-agent environment settings. Files below `.private/` stay
private. Commands return one JSON result with real-money activation false.

## Recover a payout

```sh
python3 scripts/operate.py status
python3 scripts/operate.py tick
```

Select a workspace explicitly with `--tenant WORKSPACE_ID` before the subcommand
when the database has more than one. Use `--state PATH` for a separate state file.

An authenticated provider receipt that confirms durable acceptance and pins
the original provider reference moves a payout to `awaiting_settlement`. Its
hold stays in place while other payouts on the connection can proceed. A
provisional `pending` receipt or lost reply leaves the outcome unknown. A
conflicting or reused provider reference preserves the hold and pauses new
submissions on that connection for reconciliation.
`tick` first checks one unknown or expired submission using its original provider
identity. It does not replace an inconclusive payment. Otherwise it processes
one prepared intent with the separate synthetic provider. It is bounded work;
no daemon, hosted service or live bank account is created.

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID reconcile --intent INTENT_ID
python3 scripts/operate.py --tenant WORKSPACE_ID packet --output .private/responsibilities.json
```

Packets exclude operator identity, account references and document contents.
They list functions and unresolved responsibilities, not a legal determination.
Unknown payouts pause new submissions on the same connection. A lost status
reply preserves the original identity and hold. A terminal rejection can return
the exact hold through a compensating posting; absence is not rejection.

`examples/recover-payment.py` demonstrates acceptance followed by a lost reply,
then original-identity recovery against an independent synthetic provider.

The owner console stores the reviewed terms of a test payout, issuance, or
redemption in the workspace. Confirming commits the financial change and its
receipt together. Reopen the console to read that receipt or resume the same
unconfirmed draft. Only one draft for each action can await review per workspace.
Check the payout history if you are upgrading and your previous browser address
is gone. A saved 0.8 payout key in the same browser can be reviewed and recovered
with its original terms.

The default launcher saves the loopback port in the ledger on first launch. If
another process uses it, free that port or choose an explicit `--port`. An
override leaves the saved address intact. The workspace keeps its drafts and
receipts even when you open a different address. Retired history without a
saved port opens at a disposable address for inspection.

## Encrypted backup and recovery

Install the optional tools once with `python3 scripts/bootstrap_api.py`.

```sh
python3 scripts/operate.py drill
python3 scripts/operate.py backup keygen --output .private/recovery.key
python3 scripts/operate.py backup create --key-file .private/recovery.key --output .private/snapshot.hbbackup
python3 scripts/operate.py backup restore --key-file .private/recovery.key --input .private/snapshot.hbbackup --output .private/restored.sqlite3
```

Key generation and restore refuse an existing destination. Keys are 32 raw
random bytes in owner-only files; never put them in command arguments or source.
Keep production keys outside the backup's failure domain using an enrolled
custody system. The tool does not enroll that custody or upload offsite copies.
The rehearsal uses an ephemeral key and removes its temporary recovery files.

Restore authenticates every chunk and checks database integrity before making a
new destination visible. It never replaces the running database. Version 2
backups stream bounded 1 MiB chunks and support databases up to 1 GiB. Legacy
version 1 backups remain readable. Status reports the remaining size budget and
minimum working space and warns at 80 percent. A backup checks space on its
destination volume before taking a snapshot. At the warning, plan capacity and
prove a restore before growth reaches the limit. Keep the financial history and
the off-volume recovery copy available through that transition.

Every restored copy starts in quarantine. New holds, financial postings, worker
claims and external bearer-token reads fail closed, even if the snapshot predates
a payout or access revocation. Owner-local inspection remains available.

For a single-host synthetic handover when the current primary is intact:

```sh
python3 scripts/operate.py --state .private/restored.sqlite3 recover-handover --authoritative-state .private/ledger.sqlite3 --provider-state .private/ledger.sqlite3.sandbox-provider.sqlite3 --output .private/resumed.sqlite3
```

The command locks both histories, verifies that the primary extends the restored
snapshot, and checks the exact provider identity. It records the retirement and
provider fence durably. It then processes independent receipts and corrections in
pages, preserving progress and the candidate. The new ledger pins the same provider
store and its new writer generation. `tick`, `reconcile` and `cancel-unknown` keep
using that store; a missing or replaced store blocks operation.

If interrupted, rerun the same handover command with the same source, provider,
restored copy and destination. The old writer stays retired. The command verifies a
candidate that became visible before directory synchronization and completes that
same activation. Recovered access requires fresh enrollment.

For a **planned cutover with independently retained custody**, add
`--seal-to PATH --key-file KEY_PATH` to the handover. This prepares an encrypted
checkpoint and leaves the destination inactive. Keep the checkpoint and key in
separate custody, and retain the independent provider store. To finish from the
checkpoint, including after losing the original primary and its candidate:

```sh
python3 scripts/operate.py recover-sealed --input PATH --key-file KEY_PATH --provider-state PROVIDER_PATH --output SAME_DESTINATION
```

The store pins the archive hash, destination and fence. If recovery is interrupted
before publication and its staging file disappears, the same command can
reconstruct it from the authenticated archive and recheck provider coverage.
An existing candidate or a published destination must match its exact byte
fingerprint. The same command can finish a pending activation from the pinned
destination when the staging file is absent. A consumed checkpoint
cannot recreate a lost active primary from stale history. This profile does not
recover changes missing from an unplanned crash after activation. No live bank
credential revocation, cross-host writer fence or production promotion is provided.
There is no unconditional quarantine-clearing command. See [conformance](CONFORMANCE.md).

## Close a payment proven never accepted

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID cancel-unknown --intent INTENT_ID
```

The synthetic provider arbitrates closing and sending in one transaction. An
already accepted payment is recovered; otherwise a permanent no-effect record
prevents a delayed worker from submitting the same identity. Only then does the
ledger return its hold. This does not implement any bank's cancellation protocol.
Unsupported adapters must retain the hold and escalate; an empty lookup is not proof.

Later synthetic provider returns are discovered by reconciling the original
confirmed intent. Separately identified compensating postings preserve its
original settlement and cannot exceed the paid amount. General fees, disputes
and other institution-specific adjustments remain outside this event contract.

## Exact usage invoices

```sh
python3 scripts/operate.py usage plan --plan api-basic --currency USD --rate-micro 1000
python3 scripts/operate.py usage record --plan api-basic --key usage-batch-001 --quantity 10
python3 scripts/operate.py usage preview --plan api-basic
```

One currency unit contains 1,000,000 micro-units; a CAD/USD cent contains 10,000.
Ten events at 1,000 micro-USD produce one cent. Smaller residuals carry into the
next invoice. Plans are immutable; choose a new plan identity for new terms.
Usage keys are stable and tenant-scoped; conflicting reuse fails. A replay
retains its original month even across a month boundary.

Close a completed UTC month with `usage close --plan api-basic --period YYYY-MM`.
The current month cannot close. Earlier unbilled usage must close first. Optional
`--base-minor` is a monthly base fee in cents starting in the UTC month the
plan was recorded. Close every active month in order, including empty months.
An empty gap from an older release can close if it preserves the already
recorded carry. New usage must fit the invoice limit. For an older overage,
preview the exact amount and pass `--expected-due-minor` to close that amount.
Invoices never debit a customer,
collect a payment or establish revenue. Refund/credit allocation for an actual
commercial usage service needs its own agreed policy before collection.

## Optional authenticated ASGI API

The owner console and the deployment API are separate interfaces. Install the
optional tools, then save an owner-only `.private/api.json`. Paths are relative
to the configuration file unless absolute. This is an example shape; replace
all identity, state, host and certificate references with your exact enrollment.
Do not place private keys or bearer tokens inside this JSON.

```json
{
  "state": "ledger.sqlite3",
  "issuer": "https://identity.example.invalid",
  "audience": "hellabit-api",
  "public_keys": {"issuer-key-1": "issuer-public.pem"},
  "allowed_hosts": ["api.example.invalid"],
  "bind": "127.0.0.1",
  "port": 8080,
  "tls_cert": "api-cert.pem",
  "tls_key": "api-key.pem"
}
```

Use owner-only mode `0600` for configuration, grant and TLS-key files. Start with
`python3 scripts/run.py --api-config .private/api.json`. An external bind requires
an enrolled TLS certificate/key. The server enforces an exact Host allowlist,
rejects cookies/Origin, does not trust forwarding headers and exposes only the
server-to-server API. Production networking and certificate renewal are not
configured by this command. Test TLS proof uses an ephemeral local CA, not a
public certificate or production identity realm.

Save a private grant file with `subject`, `role`, Unix `expires` and optional
`rate_limit` (per minute). An `agent` grant also names `customer`. Then:

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID identity grant --binding .private/api.json --grant .private/grant.json
python3 scripts/operate.py --tenant WORKSPACE_ID identity revoke --binding .private/api.json --principal PRINCIPAL_ID
```

Renewal uses a private JSON file containing `expires`; permissions stay unchanged:

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID identity renew --binding .private/api.json --principal PRINCIPAL_ID --expected-version VERSION --grant .private/renewal.json
```

Each grant change returns a version. Stale versions fail rather than overwriting
newer authority. `replace` accepts deliberate role/customer/rate changes;
`reinstate` explicitly reauthorizes a revoked principal. Both use the same
principal/version/private-file arguments. A subject cannot be silently reenrolled.
Existing tokens do not gain renewed or reinstated authority: obtain an access
token containing the new opaque `token_claims.hellabit_grant` value returned by
the grant command. The enrolled identity provider must include that value in its
signed access token. Clock skew never transfers an old token to a new grant. Identity,
revocation and every grant version remain auditable.

The configuration state must match the selected database. RS256 signatures,
issuer, audience, key ID, token purpose, expiry and issuance time are checked.
Access tokens last at most 15 minutes; grants at most 31 days. Token-supplied
workspace/admin claims grant no authority. Operator tokens require a verified
`amr` MFA assertion and integer `auth_time` within the last 15 minutes. MFA policy belongs to the enrolled issuer, not a self-ticked
UI checkbox. Subject grants currently bind one issuer/subject to one workspace.
Key rotation is an explicit configuration change with review/restart; no remote
JWKS fetching or automatic unknown-key trust occurs.

## Read-only EVM projection

Save a private chain JSON with exactly `binding`, `chain_id`, `contract`,
`start_block`, `endpoint`, `allowed_hosts`. Use an approved HTTPS endpoint on
port 443 without URL credentials, query or fragment. Put any provider path
reference only in the private file. There is no key discovery or paid signup.

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID index bind --config .private/chain.json
python3 scripts/operate.py --tenant WORKSPACE_ID index status --config .private/chain.json
python3 scripts/operate.py --tenant WORKSPACE_ID index sync --config .private/chain.json --max-blocks 20
python3 scripts/operate.py --tenant WORKSPACE_ID index events --config .private/chain.json
```

Binding, status and event pages do no network I/O. Continue an event page with
`--cursor NEXT_CURSOR`. The cursor keeps the first page's finalized block
boundary. The result reports when that boundary is complete.
Sync checks chain ID and requests the bound contract's ERC-20 Transfer logs by
exact block hash. The provider must support [EIP-234](https://eips.ethereum.org/EIPS/eip-234).
The CLI accepts up to 4,096 matching events per block and up to 4,000,000 bytes
per RPC response. An over-limit or failed response leaves that block uncommitted.
Reader pages still contain up to 1,000 events. The cursor follows the supplied
RPC view. Signing, balances, and reserves use separate controls and evidence.
All resolved endpoint addresses must be public and the connection pins a resolved
address while validating hostname/TLS. No redirects or write RPC methods exist.

Tentative reorg history is retained. A contradiction to finalized history halts
for independent review; it is not silently repaired. Networks without the
supported finalized-block semantics need a separate reviewed finality policy.
Provider quotas/charges are external; no public endpoint is exercised by local
verification. `ProviderInbox` requires a workspace/rail/provider binding and signs a versioned,
provider-specific domain plus timestamp/raw body. It is a tested HMAC reference protocol, not a claimed
Circle/bank webhook implementation. Enroll actual provider signatures and status
semantics before adding any real financial adapter.

## Health, incidents and supported limits

`/api/health` is liveness only. An authenticated workspace `GET /ready` checks
database access and the recovery gate; it does not assert financial readiness.
Unexpected API failures return a correlation ID and emit a diagnostic record
containing only that ID and the exception type. They exclude messages, paths,
claims, tokens and request bodies. Capture the API logger in the deployment's
chosen incident system; this repository does not enroll one.

RPC timeout is one monotonic deadline across DNS, TLS, response and retries.
Outstanding system resolvers are bounded, and a watchdog closes a dripping
connection. Circuit and response-size limits still apply.

Usage ingestion updates an indexed running total in the insertion transaction.
Invoice close cross-checks the immutable event history before accepting that
total. Use batch identities for large quantities. The engine remains a bounded
single-node implementation. The included baseline reports observed local
latency, database size and metering timing. It also drains a queued batch
through a separate synthetic provider store, drops every fifth reply after
acceptance, and checks that original-identity recovery records one payment per
command. The report includes queue delay and recovery throughput for this
local workload.

## Export the complete financial record

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID export --output .private/history.jsonl
```

The JSON Lines file contains a consistent workspace snapshot of journals,
postings, business-command identities, provider observations, later adjustments,
authorization history and usage records. A terminal checksum covers every
preceding line. Credentials, lease secrets and payment authority are excluded.
The file is confidential: it includes customer inputs and financial records.

For a scoped payout reconciliation sheet, export one row per intent:

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID payout-report --output .private/payout-report.csv
```

The CSV comes from one ledger snapshot. It records asset and decimal places,
the original provider reference, requested and held units, settlement, later
returns and net paid. Match its provider reference against the independent
statement or provider export. The command writes a private new file and reports
its checksum. It excludes recipient details and signing credentials.
It is owner-only, refuses overwrite, and is an audit export rather than a tool
that restores provider authority or replaces encrypted backups.

## Storage upgrades and request revocation

Opening a supported older database runs the versioned storage migration in one
SQLite transaction before any component serves a request. A failed migration
rolls back its schema and version; newer or foreign databases are rejected before
a writable connection opens. Original financial identities and journal hashes
are retained. Legacy reserve freshness is invalidated and needs a new full-balance
observation. Existing API subjects receive a fresh grant generation and require
new signed tokens. Keep an encrypted pre-upgrade backup outside the primary's
failure domain.

Authorization is checked at admission and again after the request body arrives.
Each financial transaction validates the same current local grant while holding
the SQLite write lock and before commit. A revocation committed first prevents the
financial write. An admitted read may finish if revocation occurs after read
admission; reads are not retroactively canceled.

## Agent purchase authority

An agent can read its assigned account immediately. To authorize purchases, give
the agent an explicit policy for one asset, priced service and workspace merchant.
Save a mode `0600` JSON file with `asset`, `resource`, `recipient` (the literal
`workspace-merchant`), `per_purchase`, `daily`, `approval_above` (integer native
units), and `expires` (Unix seconds). `0` as the expected version creates the policy;
use the returned version for a change.

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID agent-policy set --principal AGENT_ID --file POLICY_JSON --expected-version 0
python3 scripts/operate.py --tenant WORKSPACE_ID agent-policy approve --principal AGENT_ID --quote QUOTE_ID --key ORIGINAL_PURCHASE_KEY
python3 scripts/operate.py --tenant WORKSPACE_ID agent-policy renew --principal AGENT_ID --quote QUOTE_ID --key ORIGINAL_PURCHASE_KEY --expected-expiry EXPIRED_TIMESTAMP
python3 scripts/operate.py --tenant WORKSPACE_ID agent-policy adopt --principal AGENT_ID --operation HELD_OPERATION_ID
```

An approval binds the exact quote, original purchase key, policy version and grant
generation. It expires within five minutes and is consumed with the reservation.
An approval replay reports whether it is current, expired or used. Renew only
an unused expired approval by supplying the recorded expiry. The original
quote, purchase key and grant stay bound to the new expiry.
Reservations count against the gross UTC daily budget, including later releases or
refunds; replaying the original command does not count twice. An agent cannot run
another caller's reservation or execute one under a superseded policy or grant.

Version 7 backfills account positions atomically from existing postings. Internal
reconciliation compares the cached positions and agent budgets with authoritative
records. API rate counters use a separate local store; this does not change grant
or settlement authority.

Before the storage upgrade, stop old workers and finish any in-flight payout
attempts using the existing runtime. The migration refuses a submitting intent.
After cutover, SQLite rejects writes from already-open older gateways. If both
legacy and scoped funding records exist for the same movement, migration stops
for comparison with the provider record. The operator must resolve the accounting
before upgrading. Internal balance checks alone cannot establish external uniqueness.

A reservation without an agent authority record remains on its existing hold.
The workspace owner can complete the original purchase or release it using
**Paid work** after checking its outcome. The owner can also adopt a still-held
purchase for an active agent on the same customer account. Adoption applies
the current policy and budget to the existing hold and records that decision.

Run test payouts through the recovery queue or the payout action in **Paid work**.
Both use the independent test provider, so their acceptance receipts can be
checked during handover. The legacy loopback payout-confirmation endpoint
requires explicit `manual_test_only: true`. Its response marks that exercise
as outside the independent provider recovery profile.
