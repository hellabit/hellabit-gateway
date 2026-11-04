# White-label issuance

Each deployment has an immutable issuer identity and reference currency, its
own token supply, reserve ceiling, role assignments and redemption records.
Branding is configurable; rights and liabilities belong to the actual issuer.

The contract grants no minter, attestor or settler automatically. A delayed
administrator can assign them. Production enrollment must establish distinct
operational principals and governance rather than assuming separate addresses
mean separate organizations.

An authorized attestor states a reserve ceiling with a proof hash and expiry.
The contract prevents supply plus pending redemption claims from exceeding that
ceiling. A proof hash is not a bank statement and cannot prove that reserves
exist. A dishonest attestor or compromised administrator remains a material
trust boundary. Contracts are not upgradeable; migration needs an explicit plan.

A holder burns tokens to request redemption. The unpaid claim keeps reserve
capacity occupied. Only the enrolled settler records an external payout, once,
against a nonempty, unreused payment proof. Offchain payout evidence must be
verified independently before this method is called.

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
