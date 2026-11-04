# Hellabit 0.4 implementation and release contract

Owner decision: November 4, 2026. Target: an updated developer pre-release after
this hardening pass and verification, in hellabit/hellabit-gateway. The accepted 0.3.0
release stays immutable. No source publication is part of local verification.

## Product and commercial decision

Build a merchant-controlled payment operations engine. The core is free under
Apache-2.0. Optional managed operation, deployment and usage services are the
commercial path, subject to an actual customer agreement and verified costs.
Internal fee postings are not collected Hellabit revenue. No guaranteed demand,
profit, bank/government adoption or maintenance-free operation is claimed.

No new spending, paid provider enrollment, purchases or outbound prospecting is
authorized. The first market is developer self-service and inbound enquiries.
Actual operator identity, company/address and account evidence remain private.
Canada and the United States are intended markets, not approved operating scopes.

## Build scope

1. Persist external payout intent with the hold in one SQLite transaction.
   Separate submission from reconciliation. Expired worker leases become unknown;
   only the original provider identity is looked up. Never blindly submit again.
2. Verify signed incoming events, deduplicate their identities and retain conflict
   evidence. Reconcile external observations with the ledger. Synthetic provider
   evidence remains permanently labeled synthetic.
3. Add a scoped EVM event processor with durable cursors, backfill, tentative
   reorganization recovery and a stop on contradictions to finalized history.
4. Add bounded read-only HTTPS RPC transport, explicit host/network binding,
   certificate checks, address pinning, response limits and no redirects.
5. Add an optional production API foundation using a maintained ASGI server and
   signature-verifying identity library. Bind authenticated subjects to local
   permissions; never trust user-supplied tenant/role claims. Keep owner bootstrap
   and synthetic-money controls off the external API.
6. Add authenticated encrypted SQLite backups, safe restore to a new destination,
   tamper tests, redacted health/incident evidence and recovery exercises.
7. Add an exact sub-cent usage meter and native-unit invoice accumulator. The meter
   never debits funds or silently turns an accounting estimate into revenue.
8. Add role-aware readiness and responsibility evidence in the existing console.
   Distinguish machine-tested controls, operator-reported documents and externally
   verified approvals. A filled form cannot activate financial capabilities.
9. Add a runnable recovery example and inbound contact path, with no new website,
   paid hosting dependency, token promotion or mandatory proprietary intermediary.

## Acceptance

Prove concurrent intent creation, provider acceptance followed by process death,
one lookup after restart, stale-worker fencing, duplicate/out-of-order events,
signature rejection, ledger reconciliation, chain rewinds and finalized-history
contradictions. Prove RPC network/address/redirect/size restrictions, identity
signature/issuer/audience/expiry/subject permissions, backup restore and tamper
rejection, exact metering and tenant isolation. Render the desktop/mobile journey.
Use the existing single verification command and a signed portable source package.

## External activation remains a distinct milestone

Open source does not establish a legal exemption or an approved payment role.
A provider account name is account-reported identity, not incorporation evidence.
External activation needs the exact entity/functions/jurisdictions reviewed,
provider acceptance, real account/asset/signer/identity bindings, independent
security review where required, and a separately authorized bounded live proof.
The repository cannot manufacture those approvals. Bank-specific APIs cannot be
implemented or certified without their actual program contract and documentation.

The current live submission boundary stays closed. Readiness records and fixtures
must never clear it. Publication can honestly ship hardened software while those
external requirements remain unresolved.

## Current publication instruction

The owner superseded the November 5 schedule: finish the hardening pass, verify,
then publish the developer update. The old scheduled publisher is paused to
prevent a second writer. The owner will run the Google checklist after publication.
A Google review package is tied to the immutable source hash, with reviewer status
explicitly pending. No Google account, paid API or continuous upload is enrolled.

Deleting or replacing a public repository does not erase third-party clones,
forks or cached copies. No deletion or history rewrite is part of this update.
The prior 0.3 release remains a recovery point. See REVIEW.md for the repeatable
private review workflow and the remaining production activation requirements.
