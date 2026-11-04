# Product critique and changes — 0.3

Review date: 2026-11-04. Scope: Hellabit Gateway's code, merchant journey,
connection selection, recovery behavior and unit economics. This is a product
and engineering review, not an independent security audit or regulatory approval.

## The product decision

Make the first product useful to an API seller and an agent operator: reserve an
explicit spending budget, deliver once, charge once, preserve unknown outcomes,
and retrieve a clear receipt. Keep ordinary money, USDC and a branded currency as
separate connections behind that workflow. Adoption follows a useful purchase;
a new coin alone does not create demand or solve distribution.

The existing payment ledger is useful groundwork. The original console led with
breadth and an abstract headline, offered several invalid actions, under-explained
the price split, and made preparation too easy to mistake for connectivity.
Large-volume tiny fees were an ambition without a visible cost check.

## Existing-product comparison

| Reference | Relevant strength | Decision for Hellabit |
| --- | --- | --- |
| [Formance](https://docs.formance.com/getting-started/modules-fit-together?deployment=cloud&license=ee) | Separates internal ledger truth, external provider data and reconciliation | Name the scope of integrity checks; never style saved references or internal hashes as independently settled money |
| [Modern Treasury](https://www.moderntreasury.com/) | Connects payment operations and ledgering around business money movement | Show available, held, merchant and fee amounts together; give every action a visible result |
| [Circle Mint](https://www.circle.com/circle-mint) | Institutional direct USDC access and explicit onboarding | Select direct issuer access for USDC; keep institutional approval and exact asset binding explicit |
| [Brale issuance](https://brale.xyz/issuance) | Branded stablecoin operation with a named legal issuer and redemption responsibility | Separate Hellabit's brand from issuer-of-record status and evaluate an established program before public issuance |
| [x402](https://github.com/x402-foundation/x402/blob/main/docs/core-concepts/facilitator.md) | Agent payments through an existing protocol and optional facilitator role | Select it for the first future external adapter; do not advertise the custom router as compatible |

These comparisons are based on published capabilities, not an account-level
hands-on test, vendor endorsement or a comprehensive market ranking. Partner
eligibility and costs remain subject to provider confirmation.

The interaction review also applies the original
[Nielsen Norman Group heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/):
visible outcomes, familiar language, error prevention and reduced recall. No
formal usability study or accessibility conformance audit is claimed.

## Changes applied

| Finding | Change | Verification |
| --- | --- | --- |
| A user could not see a clear first useful step | State-based Budget → Reserve → Deliver guidance, with the next action tied to current funds and work | Browser journey from an empty spending account through delivery |
| Price and fee relationship was unclear | Show customer gross, included platform fee and merchant net before reserving; disable underfunded reservations | Native-unit fee checks and browser split assertions |
| Refunded work still offered download and refund | Add explicit refunded and delivery projections; hide invalid actions while preserving original ledger history | HTTP delivery denial, ledger projection and rendered refunded-state checks |
| Paid-out proceeds could still invite an unfundable refund | Disable that refund and explain the funded-reversal requirement | Payout then rendered refund-control check, plus the existing balance invariant |
| A completion assertion could be presented as a downloadable report | Show Download only when an actual delivery exists and has not been refunded | Snapshot projection and action rendering |
| Merchant and initial workspace keys could not be revoked | Owner-only metadata listing and immediate revocation for both; agent revocation remains customer-bound | Authentication denial, cross-workspace isolation and rendered key-management journey |
| One-time credentials could remain on another screen | Mask by default and clear on leaving Developers, workspace change, revocation or page exit; no browser secret storage | Browser navigation/storage proof |
| A timeout could leave the SDK without a usable reservation ID | Add original-purchase-key lookup with tenant and agent-customer checks; no automatic mutation retry | Reopen lookup, scoped HTTP lookup and SDK contract checks |
| Prices and reserved terms could be altered in the database | Add immutable quote and operation-term triggers, retaining explicit state transitions | Direct write rejection and original amount preserved |
| Currency branding could change after every token was redeemed | Lock identity once any reserve evidence has been recorded, even when balances later reach zero | Full issue → redeem → pay → attempted rename test |
| Inputs encouraged actions that were guaranteed to fail | Disable invalid reservations, oversize payouts, stale/over-limit minting, fractional-cent redemptions and used issuer identities; show the reason | Browser action states and underlying money-invariant tests |
| Busy controls accepted clicks without visible effect | Disable money controls while a request is active; keep durable success/error feedback and refresh the ledger after failure | Browser in-flight request gate and recovery checks |
| Refresh lost asset and navigation choices | Store only workspace ID, selected asset and view in session storage | Reload of an uncertain job and exact USDC asset selection |
| USD was missing from asset tabs | Make CAD, USD, native test USDC and issued assets reachable | Browser accessibility and asset-count checks |
| Journal rows omitted amounts and timing | Add gross native amounts, UTC timestamps, expandable receipt hashes and a visible scope count | Rendered receipt journey and native amount projection checks |
| Exports could imply a full statement | Export the visible selected-asset rows, with integer units and decimals; explicitly state the bounded scope | Download-content and scope checks |
| Tiny-fee revenue hid operating costs | Add a calculator using integer micro-USD and explicit variable/fixed costs; expose negative contribution | Positive and loss-making calculator cases |
| Connections were anonymous reference forms | Add a source-linked, dated Peoples/Circle/Brale plan with unresolved eligibility and cost conditions | Owner-only plan endpoint and rendered not-enrolled states |
| Small type and repeated hero copy slowed scanning | Preserve the paper/forest visual identity, improve type contrast/size, shorten headings, emphasize balances, actions and receipts | Desktop and all-view phone/tablet containment proof |

## What the build still does not establish

The service remains a loopback sandbox. It does not move real funds, open a bank
account, connect an institution, provision a production signer, issue a redeemable
currency, establish reserves, or prove public-chain behavior. The custom router
has no x402 adapter or contract-wallet signature support. The offchain issuer
books and token contract still need an enrolled production indexer binding.
An independent security audit, institution-specific settlement adapter, production
identity/permissions, production backup/recovery procedures, monitoring, and live settlement
and return proof remain required for their respective operating roles.

The invoice arithmetic example demonstrates software delivery and charging; it is not
a claim of a valuable external data service. Sell and validate a specific useful
API before treating synthetic paid calls as customer revenue. There is no claim
of millions of users, bank/government adoption, guaranteed popularity, passive
income or maintenance-free financial operations.

## Independent ChatGPT review completed

A separate ChatGPT Pro critique was obtained through the owner-requested XXV
Operator background Safari route on 2026-11-04. A dedicated window received three
sanitized product screenshots and the implementation summary. Existing windows,
selected tabs and foreground focus were preserved. No cookies, private backend
endpoints, account stores, runtime secrets or customer data were used.

The reviewer did not inspect the source or execute tests. Its assessment is a
screenshot/description critique, not a security audit. The original reply and
submission/readback proof are retained privately outside source packages.

The main recommendation is a recoverable purchase/result workflow for one useful
API, rather than launching several financial operating roles at once. Keep all
three settlement capabilities available, but treat issuance as an experimental
path and choose one future live rail only after its program is approved.

### Applied in 0.3

- Separate customer, merchant and platform balance ownership; show payout holds.
- Label the owner sandbox and simulated payout outcomes more clearly. Provider
  enquiries distinguish sent from queued mail, with fit unconfirmed and zero live connections.
- Replace competing navigation treatments with one current-page marker. The phone
  Menu exposes all five destinations without a clipped navigation strip.
- Collapse budget funding when sufficient credits exist and show the next purchase
  action. Make payout shortfalls exact, with an explicit Use available action.
- Translate work states into charges, returned credits and reserved-fund
  consequences. Preserve raw state, input hash, identifiers and times in details.
- Filter work and journal activity before bounded row limits. Unknown work is
  prioritized and can be checked without creating a replacement purchase.
- Retain the original non-secret request identity after a lost reservation reply;
  explicit recovery reuses its quote/key. Price/input changes are blocked during
  recovery. One-time access secrets remain excluded from browser storage.
- Add actual invoice arithmetic, immutable quote-input identity, an inspectable
  result/receipt, and a repeatable SDK example that recovers its original purchase.
  This is an integration example, not an OCR business or validated paid product.
- Distinguish whole-cent native fiat postings from sub-cent economic modeling.
  Self-hosted fee postings are explicitly not collectible Hellabit revenue.
- Check the loaded SQLite WAL fix, exercise backup/restore and computation rollback,
  and provide a redacted owner-only diagnostic export.

The screenshot critique inferred a 211-pixel phone image. The source screenshots
were captured at a 390 CSS-pixel viewport; layout decisions and assertions use
rendered geometry. No physical-iPhone or formal accessibility-conformance claim
is made. Future retrieval revocation is explicitly distinguished from retracting
a result already downloaded.

### Publication decision

Reach out to providers now. The three nonbinding enquiries were submitted once
from a@epv.ai with owner BCC and independent Sent/MTA acceptance proof. No partner
approval, bank account, paid program or financial contract resulted from that send.
Inbox placement and responses are not established by submission proof.

Publish an honestly labeled sandbox after local release hygiene; provider approval
is not a prerequisite for publishing software. A public payment-service launch is
a distinct milestone. Keep live payment adapters, operational authentication,
reserve/indexing integration, custody, applicable approvals and real settlement
proof outside publication claims until actually completed. No repository or public
payment service was published by this work.

The commercial next test remains one independent merchant and an authorized buyer
using a valuable service and recovery without founder intervention. The included
invoice computation and automated tests do not establish demand, willingness to
pay, institutional adoption or maintenance-free operation.
