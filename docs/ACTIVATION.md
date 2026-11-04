# Activation is an exact enrollment, not a global switch

The source deliberately contains no production network submission, bank account
discovery, issuer registration, signer enrollment or public deployment command.
`LiveRail.submit` stops with `live_connection_not_enrolled` before external I/O.

The next external foundation is a named operating/issuer entity and reserve or
settlement relationship. Until those are resolved, software tests cannot make
the product financially live. The selected connection then needs:

1. Exact institution or chain/token identity, operator authority and scoped signer.
2. Provider-specific onboarding, applicable classification and agreements.
3. Independent receipt/statement verification, reconciliation and recovery.
4. A bound installed runtime, encrypted state, signer custody and backups.
5. Explicit limits, customer authorization and a reviewed canary plan.
6. One exact reversible live batch, with competing writers fenced and readback.

White-label issuance additionally needs reserve and redemption arrangements,
independent security review, governance and a contract/indexer reconciliation
binding. A token contract does not create backing. A sandbox test does not issue
a real coin. Publication of source does not authorize a live financial operation.

No credentials, wallet material, customer records or existing production authority
belong in the public archive. New deployments enroll their own authority.
