# Purchase API and authority

All routes are under `/api/v1/workspaces/{workspace}`. The loopback console uses
its owner session/CSRF token; its SDK interface also accepts scoped sandbox keys.
The separate optional ASGI API verifies access-token signatures and local grants.
It never accepts console cookies or sandbox bearer keys. The matrix below describes
the loopback interface; the narrower deployment API is listed separately.

| Action | Owner console | Merchant key | Customer-bound agent key |
| --- | --- | --- | --- |
| Quote an approved service with integer price, fee and input | Yes | Yes | No |
| Reserve a quote using a stable purchase key | Yes | Yes | Assigned customer only |
| Look up a purchase by its original key | Yes | Yes | Assigned customer only |
| Read work state, run a registered pure service, retrieve a result | Yes | Yes | Own customer purchases only |
| Complete arbitrary work, reconcile an unknown outcome, refund | Yes | Complete/refund only | No |
| Fund test credits, request/confirm payouts, operate issuer controls | Yes | No | No |
| Issue merchant keys, list key metadata, download diagnostics | Yes | No | No |
| Issue/revoke customer-bound agent keys | Yes | Yes | No |

`POST /quotes` accepts `asset`, `amount`, `fee`, optional `resource`, and an `input`
for `invoice-check`. Amount and fee use the payment asset's integer native units.
The fee is included in the total. Input is normalized, validated and bound by a
SHA-256 digest to an immutable quote. A quote belongs to exactly one workspace.
No currency conversion is inferred.

`POST /operations` accepts `customer`, `quote` and `key`. The same key and same
request return the original operation. Changing the request under that key is a
conflict. Expired quotes cannot start a new reservation.

`POST /operations/lookup` accepts the original `key`. It is an authenticated read
operation, including when used after a network timeout. A missing lookup does not
justify changing the purchase identity. A customer-bound key cannot look up another
customer's purchase.

`POST /operations/{id}/run` accepts an empty object. The caller cannot replace the
quoted input, resource, price or fee. Registered computation, durable result and
local charge commit together. Repeated execution returns the existing entitlement.
A process failure may repeat pure computation after rollback; it cannot create a
second committed local charge for the same operation.

`GET /operations/{id}` reads work state. `GET /deliveries/{id}` retrieves a stored
result after authorization and denies future retrieval following a refund.
`POST /operations/{id}/refund` performs a funded, full compensating reversal.

`GET /activity/{asset}` filters before a 30-row limit. `GET /activity/all` explicitly
includes all assets. Unknown and reserved work come before terminal work. The
response includes retained counts and its scope. This bounded view and its CSV
export are not a complete statement or backup.

`GET /diagnostics` is owner-only and excludes keys, account identifiers, enrollment
references and work inputs. It reports runtime versions, native precision and an
internal consistency result. It makes no external settlement claim.

CAD and USD ledger postings use whole cents. The separate usage meter accumulates
exact micro-units, closes completed UTC months and carries sub-cent residuals.
It creates invoices without collection; it does not change ledger precision.
Native test USDC and workspace test tokens use six decimals. The economics model
must not be confused with supported per-call fiat postings or collectible revenue.

The `http.server` implementation is a loopback development server. It must not be
exposed as a public payment service by adding a tunnel or TLS wrapper. See the
[official Python server documentation](https://docs.python.org/3/library/http.server.html)
and the [security contract](../SECURITY.md).

## Optional ASGI interface

Use [the exact enrollment procedure](OPERATIONS.md), not a tunnel around the
owner console. Access tokens require pinned RSA keys, RS256, expected issuer and
audience, key ID, subject, issuance/expiry and access-token purpose. Grants are
local, expiring and revocable; workspace/admin token claims cannot add permission.
No identity realm or public certificate is enrolled by the source release.

| Role | Exposed capabilities | Restrictions |
| --- | --- | --- |
| Merchant | Own workspace, quote, reserve, lookup, registered run, operation/result read, funded refund | No funding, issuer, payout, principal or key-management mutation |
| Agent | Assigned customer state, reserve, lookup, registered run and own results | Cannot reprice, refund, cross customers or inspect another workspace |
| Read-only | Own workspace and result/operation reads | Every POST denied, including lookup POST |
| Operator | Own workspace, payout queue and readiness reads | Signed MFA and recent auth_time required; every POST denied |

`GET /api/health` is unauthenticated, contains no account data and explicitly
states that live payments are disabled. Other paths need a bearer access token.
Exact Host binding, no cookies/Origin, bounded JSON and one total body deadline, duplicate-field
rejection and persistent per-principal rate limits apply. No webhook or settlement
POST is exposed. The HMAC inbox is a backend library until an actual provider's
authentication and event contract are enrolled.

Owner-only loopback routes added in 0.4: `GET /ops`, `GET /readiness`,
`GET /readiness/packet`, `POST /ops/{intent}/process`, `/interrupt`, `/reconcile`,
`POST /readiness/recovery-drill`, and `POST /readiness/evidence`. Mutation bodies
are exact-field validated. The three execution actions are synthetic only; the
evidence route accepts a reference, document hash and expiry, never a verified
flag. None can open the live submission boundary.


Deployment API `GET /api/health` reports process liveness only. Authenticated
`GET /api/v1/workspaces/WORKSPACE_ID/ready` checks database access and restore
quarantine. Neither establishes provider readiness. Unexpected errors include a
`correlation_id` matching a redacted diagnostic event.

Subject grant renewals, permission replacement and deliberate reinstatement are
local operator actions with expected-version checks. They are never authorized
by token-supplied admin claims. See [identity operation](OPERATIONS.md).

## Current payment outcomes and grant generations

Payout and execution-intent projections expose `original_settled_units`,
`returned_units`, `net_paid_units`, `effective_outcome` and `latest_adjustment`.
`effective_outcome` becomes `partially-returned` or `fully-returned`; the original
settlement state remains immutable history. A return carries its canonical
`event_key` and exact `connection`. The console uses the current outcome for its
headline.

Signed access tokens must include the current opaque `hellabit_grant` returned
by local enrollment or renewal. The issuer signs that generation reference; local
grants still determine workspace, role and customer authority. Renewing, replacing
or reinstating a grant rotates the reference. Old tokens cannot gain the new
authority through issuance-time leeway. Legacy tokens need fresh issuance after
a storage upgrade.
