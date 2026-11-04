# Connection decisions

Research date: 2026-11-04. These are selected evaluation targets, not active
connections, agreements or a representation of eligibility. The machine-readable
version powering Connections is `CONNECTIONS.json`.

| Responsibility | First partner to evaluate | Why | What remains unresolved |
| --- | --- | --- | --- |
| Canadian ordinary-money settlement | Peoples Group | Published EFT/Interac money movement and trust account services | Hellabit entity, program approval, API/event access, safeguarding structure, costs, return handling and stablecoin restrictions |
| Direct USDC mint/redemption | Circle Mint | Direct issuer relationship; supported wire list includes Canadian bank domiciles | Institutional approval, account binding, USD funding, network/token, receiver, signer, trusted RPC and finality |
| Branded USD coin | Brale | Published legal-issuer, reserves, custody and redemption offering | Canadian distribution, control and migration rights, program approval, total commercial cost |

Sources: [Peoples money movement](https://www.peoplesgroup.com/fintech/solutions/payments/money-movement),
[Peoples trust services](https://www.peoplesgroup.com/fintech/solutions/payments/trust-services),
[Circle Mint](https://www.circle.com/circle-mint),
[Circle supported wire countries](https://developers.circle.com/circle-mint/references/supported-countries),
[Brale issuance](https://brale.xyz/issuance), [Brale pricing](https://brale.xyz/pricing).

The selection is an engineering and business recommendation, inferred from those
published capabilities. None of these sources confirms Hellabit's eligibility.
Circle's wire-country list is not a promise of account approval or a CAD conversion
service. Brale would be the legal issuer of its program; Hellabit would be a
brand/distribution partner. A CAD-branded coin needs a separately approved issuer
and program; it must not be silently treated as an approved USD program.

Brale currently publishes issuance from USD 1,000/month, with other platform and
usage fees depending on the package. Do not incur that overhead before demand
and contribution cover the full quote. Retain the branded-currency capability in
software while the program is evaluated. Three nonbinding fit enquiries were submitted on 2026-11-04 from a@epv.ai,
with the required owner BCC and independent Sent/mail-server acceptance proof.
No subscription, financial-account application, agreement or paid program was
accepted. Responses, fit approval and inbox placement are not established by
submission proof.

The later delivery readback shows final submissions for Peoples Group and Circle.
Brale remains queued after a mail-domain DNSSEC validation error; the owner BCC
was delivered. No duplicate enquiry was sent and no mail resolver was changed.
This delivery state is dated evidence, not a live provider-health monitor.

[M0](https://docs.m0.org/build/overview) is the secondary issuer-portability option.
Evaluate a non-Stripe issuer, confirm jurisdiction and pricing, and prefer an
established audited template if it fits. The custom Hellabit contract is not an
M0 extension. Bridge is excluded from our selection in accordance with the
owner's preference.

## Agent distribution

Choose [x402](https://github.com/x402-foundation/x402/blob/main/docs/core-concepts/facilitator.md)
as the first external agent-payment protocol. Build against its exact supported
version, scheme, network, asset and facilitator behavior. Self-facilitation can
remove a mandatory intermediary, but transfers RPC, signing, finality and incident
responsibility to the operator. It does not remove network cost or obligations.
The existing buyer-signed Hellabit router is not an x402 implementation, and the
console explicitly labels this adapter as planned.

Do not open with bank/government procurement or create an incompatible token to
force adoption. Start with API sellers and agent operators who already need
spending limits, one charge per delivery, refund accounting and reliable receipts.
Banks and institutions are later distribution partners with a distinct diligence
and operating model. No adoption or popularity is guaranteed.

## One approved connection at a time

The first operational choice is the legal entity and its permitted role: software
operator, funds-holding PSP, distributor or issuer. This determines the partner
program, onboarding and local obligations. A software build cannot choose that
legal role on behalf of a bank.

For each shortlisted provider obtain written confirmation of:

1. Entity/jurisdiction and intended funds flow acceptance, including customer types.
2. Legal custody, safeguarding, reserve and redemption responsibilities.
3. Commercial minimums, marginal costs, returns, chargebacks and reserve requirements.
4. Sandbox API access, scoped credential broker, event signatures and duplicate handling.
5. Settlement identifiers, independent statements/RPC readback and finality/reversal rules.
6. Export, termination, key-control and migration rights, and incident ownership.

Bind the exact approved account or asset, then prove one synthetic journey before
a separately authorized small live transaction and refund. Keep ordinary money,
USDC and branded issuance as distinct connections. Prepared references do not
activate any of them. Do not onboard customers or market a redeemable Hellabit
coin until the applicable program and operational controls are actually approved.
