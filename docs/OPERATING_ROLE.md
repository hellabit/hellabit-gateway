# Assign the real operating role

The proposed first product is merchant-controlled software. Keep settlement
accounts and signing authority with the merchant or its approved institution.
That is a design direction, not an automatic legal exemption. Selling, hosting,
initiating payments, holding customer balances, signing transactions and issuing
redeemable value can create different obligations. Open-source licensing does
not decide the classification of the business operating that software.

Canada and the United States require separate assessments of the actual operator,
activities, customers and settlement arrangements.

The readiness report deliberately cannot mark legal requirements verified or
enable live funds. A document hash proves content identity only when its bytes
are supplied separately; an opaque reference does not prove its legal effect.
Records expire, remain reported-unverified and exclude private document contents.
The exported packet assigns the questions to responsible parties so a real
review can resolve them. It never substitutes an automated conclusion.

Primary sources reviewed November 4, 2026:

- [Bank of Canada registration criteria](https://www.bankofcanada.ca/2026/06/criteria-for-registering-payment-service-providers/): actual payment functions, geographic scope and applicable exclusions determine registration scope.
- [Bank of Canada supervision FAQ](https://www.bankofcanada.ca/2026/08/frequently-asked-questions-about-retail-payments-supervision/): lack of revenue and serving banks do not by themselves remove an in-scope PSP's obligations.
- [FINTRAC money services businesses](https://fintrac-canafe.canada.ca/msb-esm/msb-eng): review remittance/payment services and dealing in virtual currency separately from the Bank of Canada's scope.
- [FinCEN CVC business-model guidance](https://www.fincen.gov/resources/statutes-regulations/guidance/application-fincens-regulations-certain-business-models): developing software and operating a business that accepts/transmits value are distinct activities. Federal guidance does not settle every US state or issuance requirement.

Before a public financial operation: confirm the registered entity, map the actual
funds flow/functions/customer jurisdictions, resolve applicable legal roles and
registrations, obtain the named program's written acceptance, assign credential
custody and incident ownership, review security, and authorize one bounded live
canary. Do not accept real customer funds while these remain unresolved.

## First merchant, then a funded provider program

The first pilot profile is one existing Canadian merchant using one approved
CAD EFT payout account. Ask the institution for a test payout, a test return and
its separate records for both. Compare the payout command, settlement reference,
return reference and amounts using the [payout report](OPERATIONS.md#export-the-complete-financial-record)
and [recovery verifier](PAYOUT-VERIFICATION.md) before requesting execution access.
Treat each other company as a separate legal entity
until its actual ownership, activities and provider terms are documented. An
internal-transaction exclusion has specific conditions; common ownership alone
is not general permission to provide payment services.

A founder can negotiate a partner-funded pilot or revenue share. The settlement
partner supplies the account access and agrees in writing who performs onboarding,
funds handling, monitoring, returns, disputes, recovery and incident response.
Canada's agent or mandatary exclusion requires activity within the actual
authority granted by a registered PSP and inclusion on its maintained agent list.
It is not transferable merely by buying or sharing equity. In the United States,
federal money-transmission treatment and applicable state rules must be evaluated
for the particular funds flow and agreements.

Owning the software or brand is a separate decision from becoming the registered
operator. Direct participation requires a funded operating plan, registration
where applicable, the rail's admission and testing, and a settlement account or
settlement agent. A no-founder-cash arrangement is a commercial target, not a
promise that regulatory, provider, reserve or operating costs disappear.

## Terms used in the pilot

| Term | Meaning in this workflow |
| --- | --- |
| Gateway | The software that records instructions, checks authority and tracks the results returned by a payment provider. |
| Processor | The service that performs payment functions under its own operating arrangements. |
| Settlement institution | The institution responsible for the account and the movement of funds under the approved program. |
| Accepted | The provider has durably recorded the original payout and can retrieve it by its original identity. |
| Settled | Independent provider records confirm the movement at the agreed stage of the rail. |
| Return | A later movement back against the original payment. The original settlement stays in history. |
| Reconciliation | Matching instructions and ledger entries against independent settlement and return records. |

- [Canada RPAA section 10](https://laws-lois.justice.gc.ca/eng/acts/R-7.36/section-10.html)
- [Payments Canada PSP participation](https://www.payments.ca/connect/membership/payment-service-provider-participation)
- [FinCEN payment processor ruling](https://www.fincen.gov/resources/statutes-regulations/administrative-rulings/application-money-services-business)
- [California DFPI example of state-specific agent-of-payee treatment](https://dfpi.ca.gov/rules-enforcement/laws-and-regulations/opinion-letters-by-law-subject/agent-of-payee-payment-processing/)
