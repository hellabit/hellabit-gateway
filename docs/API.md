# Purchase API and authority

All routes are under `/api/v1/workspaces/{workspace}`. API callers use a scoped
sandbox bearer key. The owner console uses its loopback session and CSRF token.
There is no public-server authentication or production exposure contract.

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

CAD and USD use whole cents. Sub-cent CAD/USD per-call billing is not implemented.
Native test USDC and workspace test tokens use six decimals. The economics model
must not be confused with supported per-call fiat postings or collectible revenue.

The `http.server` implementation is a loopback development server. It must not be
exposed as a public payment service by adding a tunnel or TLS wrapper. See the
[official Python server documentation](https://docs.python.org/3/library/http.server.html)
and the [security contract](../SECURITY.md).
