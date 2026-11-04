# Security boundary

Version 0.4 is not approved for custody, real payments or redeemable issuance.
Do not expose its loopback owner console through a reverse proxy or tunnel.
Owner sessions assume the current local user controls the machine; the loopback interface is not a
public identity system. The optional ASGI API verifies pinned issuer signatures
and operator MFA assertions; no production realm, HSM or principal is enrolled.

Sandbox keys are workspace-scoped and hash-stored. The JavaScript SDK keeps its
key in a private field and sends it only to HTTPS or a loopback origin. Neither
service nor SDK logs request bodies, bearer values or settlement payloads.

Only the owner session may generate test funding, prepare connections or modify
issuer/payout state. Those actions require exact origin, Host and CSRF checks.
Anonymous clients cannot inspect workspace state. Asset selection and prices
are server-owned records, and inputs have bounded integer units and exact fields.

The service has no automatic external payment retries. The durable outbox persists
intent with its hold, fences workers and reconciles original identities after
unknown outcomes. Every real adapter must meet its provider-specific submission,
lookup and authenticated receipt contract before enrollment. A receipt transport
must be authenticated and source-bound; client JSON is never financial truth.

Tests cover concurrent overspending, replay, forgery, tenant isolation, expiry,
actual process termination inside a transaction, refund revocation, reserve
liabilities and EVM settlement rollback. They are bounded evidence, not a formal
audit or a guarantee covering all attacks. Smart contracts need independent
review and supported-token validation before deployment.

The source package contains no runtime database or private signing key. Its
included public checkpoint key identifies a source checkpoint only. It is not a
trusted bank, legal issuer or production signing identity.

The locked development graph overrides `tmp` to 0.2.7 to address
[GHSA-7c78-jf6q-g5cm](https://github.com/advisories/GHSA-7c78-jf6q-g5cm)
in the compiler's temporary-file dependency. The verification rail checks current
npm advisories against the exact lockfile and rejects high or critical findings.

Do not post security reports containing credentials, customer data or exploitable
production details in public issues. Send private reports to [a@epv.ai](mailto:a@epv.ai?subject=Hellabit%20security);
coordinated disclosure and response commitments need a separately agreed policy
before a public operational service launches.

The loopback owner bootstrap trusts the local operating environment. It is not a
multi-user authentication boundary against another local process or a compromised
machine. A public tunnel or TLS wrapper does not turn it into a production server.

Use a patched SQLite runtime for WAL operation. The service checks for SQLite
3.51.3+ or the supported 3.44.6/3.50.7 backport branches before opening its database.
The loaded runtime, not the Python version alone, matters. See the
[SQLite WAL-reset advisory](https://www.sqlite.org/wal.html#walreset).
The verification suite restores a synthetic database using SQLite's backup API,
including stored results and uncertain holds. That is not a production backup or
disaster-recovery certification. Preserve confidential runtime state separately
from source packages; a plain copy of an active WAL database is not a supported
backup procedure.

Registered computation in `hellabit/services.py` is pure and bounded. External
network work must have its own durable idempotency/status contract. The guarantee
is at most one committed local charge for a purchase identity; external delivery
is not made exactly once by a SQLite transaction.

Optional Python API/encryption dependencies are exact-version, wheel-hash locked.
Verification checks installed versions and public OSV advisories; it is not an
independent security audit. Public API tokens are short-lived RS256 access tokens
with pinned issuer/audience/key and local, expiring subject grants. Remote key
headers are rejected. Operator MFA is an assertion verified from the trusted
issuer, whose enrollment/policy must be reviewed separately. The SDK can obtain
a fresh token for each request; tokens never enter public examples or logs.

Encrypted backups use 256-bit keys, random 96-bit nonces and AES-GCM authentication.
Restore authenticates before creating a new destination and refuses overwrite.
The local drill is not evidence of production key custody, offsite retention or
incident staffing. Back up the separate provider's authoritative state according
to its contract; a gateway restore must reconcile original payment identities.
The encrypted backup size limit is 64 MiB; no horizontal scaling or
maintenance-free financial operation is claimed.

The additional publication pass rejects duplicate JWT JSON members, limits the
entire request-body read to five seconds, and requires a signed recent auth_time
for operator access. Webhook HMAC messages include a versioned provider domain;
financial application additionally requires the saved workspace/rail/provider
binding. A callback that wins a worker race cannot cause a replacement send.
Fencing cannot cancel a request already accepted by an external institution;
real adapters still need their own stable identity and status contract.

The Google review bundle is a reproducible copy of shareable source, not proof
that Google reviewed it. Review findings must name the exact source hash and be
independently reproduced. Any code change makes the older review stale.
