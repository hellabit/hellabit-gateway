# Price the service, preserve the unit economics

The open-source core does not charge self-hosters. Usage revenue belongs to a
separately operated managed service with an explicit merchant agreement.

Quotes name an integer customer price and an integer platform fee in one asset.
The fee cannot exceed the price. It is disclosed before a reservation and
posted separately from merchant proceeds only when work completes. It is a
reference software policy, not a processor/network tariff or public price offer.

Tiny uses should be metered internally while funding and collection occur in
larger payments or an explicitly supported batched-settlement scheme. Costs of
network transactions, RPC/signing, storage, reconciliation, customer support,
fraud and incident response must fit under the actual price. A minimum monthly
service charge may be required. No default coin yield or reserve income is modeled.

Measure retained merchants, billable operations, contribution margin and support
minutes. GitHub stars, synthetic throughput and token supply do not establish
economic demand. Bank/government adoption and universal interoperability remain
ambitions, not product claims.

## Put the ambition through arithmetic

The console's illustrative calculator uses one million paid calls, a fee of
0.001 USD per call, variable cost of 0.0002 USD per call and 100 USD of monthly
fixed costs. That produces 1,000 USD in fee revenue, 200 USD in variable costs
and 700 USD in contribution before taxes and any omitted costs. Volume is an
input, not a forecast. If variable cost exceeds the fee, more volume makes the
loss larger.

Brale's [published pricing](https://brale.xyz/pricing), reviewed 2026-11-04,
lists issuance from 1,000 USD/month, alongside platform/usage costs depending on
plan. A public stablecoin program could consume the entire illustrative gross
fee revenue before other costs. Obtain a complete quote and prove demand first.

Prefer paid software value: a self-hosted core, a hosted usage service with a
minimum covering fixed operation, and separately priced institutional support.
Define one supported default configuration and a bounded upgrade policy. Do not
promise unlimited custom integrations or 24/7 support at a tiny per-call margin.
Customer receipts, exports and original-key recovery reduce routine support;
incident response and regulated responsibilities remain real work.

## Native precision and fee collection

CAD and USD ledger postings are integer cents. A modeled USD 0.001 per-call fee
cannot be posted individually as native USD in this implementation. Sub-cent fiat
billing requires a separately specified exact accumulator, aggregation/rounding
period, residual handling and refund allocation. That feature is not implemented.
The calculator labels these scenarios as an economic model only. Native test USDC
has six-decimal units; its supported precision is not a conversion to fiat USD.

A fee entry in a self-hosted ledger is not revenue collected by Hellabit. The
open-source core charges no compulsory fee and has no hidden collection or usage
telemetry. Any hosted usage service requires a separate chosen service, agreement,
billable usage definition and actual collection mechanism.
