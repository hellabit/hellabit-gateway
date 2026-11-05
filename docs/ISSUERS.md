# White-label issuance

Each deployment has an immutable issuer identity and reference currency, its
own token supply, reserve ceiling, role assignments and redemption records.
Branding is configurable; rights and liabilities belong to the actual issuer.

The contract grants no minter, attestor or settler automatically. A delayed
administrator can assign them. Production enrollment must establish distinct
operational principals and governance rather than assuming separate addresses
mean separate organizations.

An authorized attestor supplies an observed ceiling, observation time, expiry,
monotonically increasing observation sequence, covered settlement sequence and
proof hash. The observed balance must account for every debit confirmed through
the contract's current settlement sequence. A confirmation invalidates previous
minting authority until new evidence covers the debit. The attestor must verify
coverage against the bank's records before signing it.

A newer authenticated lower
balance is recorded even when it is below liabilities. It removes stale minting
headroom immediately. An old or out-of-order observation cannot restore authority.
A proof hash is not a bank statement. Attestor and administrator trust still matter.
Contracts are not upgradeable; migration needs an explicit plan.

A holder burns tokens to request redemption. Each claim is keyed by holder and
request ID, so another holder cannot consume that identity. The unpaid claim
continues to count as a liability. Only the enrolled settler confirms the exact
holder and request, using independently verified, nonempty and unreused payout
proof. A verified payout can close an existing claim during a shortfall. It
reduces the ceiling and advances the settlement sequence.

Real issuance needs legal operator identity, reserve ownership and segregation,
custody arrangements, redemption rights, liquidity and reconciliation policies,
governance, incident response and applicable jurisdictional requirements.
No public claim of government or bank use is permitted without actual evidence.

As reviewed November 4, 2026, the October 24 Canada Gazette publication contains
**proposed** Stablecoin Regulations, with intended commencement in fall 2027.
It must not be described as an already effective authorization for Hellabit.
The current obligations and classification need qualified review for the exact
operator and functions. Sources: [Canada Gazette](https://gazette.gc.ca/rp-pr/p1/2026/2026-10-24/html/reg1-eng.html),
[FINTRAC registration scope](https://fintrac-canafe.canada.ca/msb-esm/questions/2-eng).


The offchain lab separates new deposits from full reserve-balance evidence.
A small deposit cannot refresh an older reserve pool. The full test bank observation is separate from the booked balance and liabilities.
An authenticated contradiction is retained as a reconciliation case and stops new
issuance until matching fresh evidence is available. It never proves real reserves.
Customers, merchant proceeds and platform fees each have a redemption path.
Burning any holder's balance keeps the unpaid claim in liabilities until the
separate test payout is recorded. Branded issuance remains an experimental
profile, separate from the payment-recovery workflow.
