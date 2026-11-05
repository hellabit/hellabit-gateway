# Connection profiles

Hellabit preserves three separate configuration paths: ordinary bank money,
native USDC, and branded issuance. These are technical profiles, not enrolled
partners or account relationships. Choose a provider that supports the actual
operating role, customers, jurisdictions and recovery requirements.

The console stores opaque preparation references only. It never accepts bank
credentials or enables a live adapter. CAD and USD synthetic execution have
separate account scopes; a real implementation must name the exact provider and
account and must not silently reuse a broad rail label.

Every adapter needs the executable guarantees in [the adapter contract](CONFORMANCE.md).
A missing lookup is inconclusive unless the provider can atomically close the
original command against every earlier writer. A returned payment is a later
financial event, not permission to rewrite its original settlement.

Public provider links are documentation examples. They do not establish a
recommendation, approval, eligibility, negotiated price or business relationship.
