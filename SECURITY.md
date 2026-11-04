# Security boundary

Version 0.1 is not approved for custody, real payments or redeemable issuance.
Do not expose its loopback owner console through a reverse proxy or tunnel.
Owner sessions assume the current local user controls the machine; there is no
public identity provider, operator MFA, HSM enrollment or production principal.

Sandbox keys are workspace-scoped and hash-stored. The JavaScript SDK keeps its
key in a private field and sends it only to HTTPS or a loopback origin. Neither
service nor SDK logs request bodies, bearer values or settlement payloads.

Only the owner session may generate test funding, prepare connections or modify
issuer/payout state. Those actions require exact origin, Host and CSRF checks.
Anonymous clients cannot inspect workspace state. Asset selection and prices
are server-owned records, and inputs have bounded integer units and exact fields.

The service has no automatic external payment retries. A future adapter must
persist intent before submission, preserve unknown outcomes and reconcile the
original provider identity before any further mutation. A receipt transport
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
production details in public issues. A private reporting contact must be enrolled
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
