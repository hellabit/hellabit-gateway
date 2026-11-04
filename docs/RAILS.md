# Independent settlement connections

| Connection | Implemented proof | Still outside the local build |
|---|---|---|
| CAD/USD bank money | Authenticated synthetic credit/debit events, cross-purpose replay protection, funding and payout books | Named bank adapter, institutional agreement, authenticated statement/readback, connectivity certification |
| Native USDC | Exact receipt/log verification, chain and address binding, canonical block and confirmation checks; synthetic funding/payout journey | Enrolled RPC, signer, gas, public-network submission, live payment and independent reconciliation |
| White-label issuer | Issuance/redemption books and compiled contracts executed in a private EVM | Legal issuer, actual reserves, custodian, production policy, deployment and indexer |

The console's USDC preparation is limited to Base Sepolia (84532), native token
`0x036CbD53842c5426634e7929541eC2318f3dCF7e`. Its identity was checked against
[Circle's address registry](https://developers.circle.com/stablecoins/usdc-contract-addresses).
Testnet tokens are not backed money. A production asset is a new explicit binding.

`UsdcReceiptVerifier` receives a backend-owned RPC transport. Receipt JSON from
an HTTP customer is never settlement evidence. The transport must validate the
endpoint, enforce response/time bounds and bind the expected network. Its
confirmation depth is a policy threshold, not a guarantee against all reorgs.

`PaymentRouter` accepts one explicitly deployed token contract. The buyer signs
the chain, router, recipient, token, amount, fee recipient, fee, deadline and
operation identity. Both token transfers occur atomically or revert. It retains
no proceeds. Fee-on-transfer/rebasing tokens are outside its supported contract;
token selection needs an independent compatibility review.

This router is an EIP-712 settlement primitive, not an implemented x402 server.
An x402/MPP integration requires its own protocol conformance and authorization
tests. Neither protocol is advertised as active by the gateway.

Bank access cannot be manufactured by a connector interface. [Payments Canada](https://www.payments.ca/connect/membership/payment-service-provider-participation)
describes institution/membership and settlement-account/agent requirements for
the planned Real-Time Rail. A processor account is not silently substituted.
