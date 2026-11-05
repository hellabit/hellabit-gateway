# Payment outcome and recovery contract

The supported reference implementation is a synthetic provider. No live bank,
public-chain payout or redeemable issuer adapter is enabled. The contract below
states what a future adapter must prove, without implying interoperability or
independent certification.

Run the failure cases with the configured Python environment:

```sh
python3 -m unittest discover -s tests -p 'test_recovery_contract.py'
```

## Identity and uncertainty

A business command has a caller key, immutable terms and an exact provider/account
scope. A new payout's identity derives from the workspace and original caller
key. Changing terms cannot change that identity into permission for a second
payment. Existing identities are preserved during upgrades. Retain caller keys
and authoritative command terms outside the database's recovery failure domain.

`prepared` records a hold and intent. `submitting` records worker ownership, not
provider acceptance. `unknown` preserves the hold. `confirmed` records an observed
settlement under the adapter's declared policy. It does not promise irreversible
banking finality. `failed` releases the hold only after authoritative rejection
or a permanent no-effect close. A timeout or empty lookup is never that proof.

The synthetic provider can atomically close an absent original key. A competing
acceptance wins; otherwise a permanent tombstone prevents even a delayed worker
from sending that key. Providers without this guarantee must keep the hold and
escalate to the institution; the gateway cannot manufacture cancellation.

Returns and reversals are separately identified movements referencing the
original settlement. Their canonical identity includes workspace, connection and external event ID.
They append compensating postings, deduplicate independently,
and cannot exceed the original amount. Fees, disputed balances and other provider
adjustments require their own supported event contracts.

## Restore and writer authority

An authenticated restore always starts in financial quarantine, including a
snapshot taken before a payment intent existed. It supports inspection and
internal reconciliation; financial postings, new holds and worker claims fail
closed. External credentials also fail during quarantine. The single-host synthetic
handover command requires a complete intact authoritative primary, verifies its
history extends the snapshot, fences provider workers, retains all later commands
and revokes all previous access. It creates a new path; source histories remain
preserved. A balanced database alone does not establish complete history.

Before supporting a live restore, an adapter must reconstruct all missing
commands, provider movements and later corrections; reconcile their exact terms;
and fence every previous writer with authority outside the restored database.
The included one-host file lock and independent fixture-store generation prove
the synthetic handover profile. Database leases alone cannot provide live
fencing or recover a lost command archive. The operator must also prove
that provider lookup and idempotency retention cover the entire recovery window.
Expiry cannot authorize reusing an identity. Those live guarantees are absent
here, so real execution remains disabled.

## Adapter declaration

Declare the exact provider, account, assets, operating role, credential custody,
identity scope and retention horizon. Specify authoritative lookup completeness,
receipt authentication, ordering, settlement/finality policy, cancellation and
later correction semantics. State limits, deadlines, incident ownership and the
independent statement or chain reconciliation source. Unsupported guarantees
remain visible rather than being normalized away.

An adapter must pass lost replies, death before/after acceptance, duplicate and
delayed events, conflicting terms, pre-intent and post-submission restore,
credential expiry/renewal, retention expiry, later returns and interrupted writes.
The core suites exercise synthetic cases; actual provider conformance, production
capacity and independent interoperability remain unverified.
