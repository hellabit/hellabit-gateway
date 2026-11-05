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
backups stream bounded 1 MiB chunks and support databases up to 1 GiB; legacy
version 1 backups remain readable. Status reports capacity and warns at 80 percent.

Every restored copy starts in financial quarantine. New holds, financial postings
and payment-worker claims fail closed, even if the snapshot predates a payout.
Use the copy for inspection. No quarantine-clearing command or automatic
promotion is provided. A live recovery implementation must reconcile missing
commands, original provider movements and later corrections, and independently
fence old writers before authorizing execution. See [the recovery contract](CONFORMANCE.md).

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
`--base-minor` is a monthly base fee in cents. Invoices never debit a customer,
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
token issued at or after the returned `tokens_issued_at_or_after` time. Identity,
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
```

Binding and status do no network I/O. Sync performs bounded read-only RPC calls,
checks chain ID, processes contract-scoped ERC-20 Transfer logs and persists a
cursor. It does not sign/broadcast, credit customer balances or certify reserves.
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
latency, database size and metering timing, not a production throughput promise.

## Export the complete financial record

```sh
python3 scripts/operate.py --tenant WORKSPACE_ID export --output .private/history.jsonl
```

The JSON Lines file contains a consistent workspace snapshot of journals,
postings, business-command identities, provider observations, later adjustments,
authorization history and usage records. A terminal checksum covers every
preceding line. Credentials, lease secrets and payment authority are excluded.
The file is confidential: it includes customer inputs and financial records.
It is owner-only, refuses overwrite, and is an audit export rather than a tool
that restores provider authority or replaces encrypted backups.
