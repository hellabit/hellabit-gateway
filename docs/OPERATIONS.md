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

Restore checks authentication and database integrity before writing a new path.
It does not replace the running database. Inspect/reconcile a restore and fence
active workers before a separately authorized promotion. A restored database may
precede an accepted external payment: the worker checks the original provider
identity before any submission. Never invent a new key to clear an unknown.
The backup limit is 64 MiB; this release is a bounded single-node engine.

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
