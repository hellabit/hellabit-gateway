# One useful sample purchase

The included invoice-total check performs actual arithmetic on supplied integer
line items. It is a small computational integration example. It is not invoice
extraction, an authenticity check, tax advice, a live payment, or evidence of
commercial demand.

Start the sandbox with its one command:

```sh
python3 scripts/run.py --demo
```

Open the printed loopback URL. In **Paid work**, add test credits and reserve the
invoice check. The example invoice has two lines totaling CAD 35.00 and a supplied
tax amount of CAD 1.75. Its declared total is CAD 36.75. The service checks that
arithmetic; it does not determine what tax ought to apply.

The purchase itself costs CAD 1.00 in test credits: CAD 0.01 included platform fee
and CAD 0.99 merchant proceeds. The invoice value and the purchase price are
separate quantities. Running the check stores the result and its charge together.
Use **View result and receipt** to inspect both before downloading.

Change the declared total in the expandable example input to exercise a mismatch.
A mismatch is a valid computed answer, not an execution failure. Invalid input
cannot create a quote. Existing reservations retain their original input digest,
price and fee, even if the form is edited later.

## Run the SDK example

In Developers, create a merchant sandbox key. Supply it through your normal scoped
secret manager/environment as `HELLABIT_MERCHANT_KEY`, together with
`HELLABIT_URL` (the printed URL) and `HELLABIT_WORKSPACE` (the workspace identifier
shown in the code example). The customer account must have sufficient test credits.

```sh
node examples/invoice-check.mjs
```

The example uses `invoice-check-example-001` as its stable logical purchase key.
Running it again retrieves the original purchase and result rather than buying
another computation. Use `HELLABIT_PURCHASE_KEY` only when intentionally creating
a different purchase. Do not generate a replacement key after a timeout.

If a reservation reply is lost in the console, **Recover original reservation**
looks up the saved non-secret purchase identity. If needed it resubmits that same
quote and key; it never creates a replacement purchase identity. A successful
lookup clears the pending request. Unknown work stays held until an observed
outcome is recorded. Waiting alone never proves completion or cancellation.

A refund returns credits through a compensating posting and revokes future result
retrieval. It cannot retract a result already downloaded. If proceeds have been
paid out, a funded reversal is required before refunding.

## Extend the workflow deliberately

`hellabit/services.py` contains the bounded pure-computation contract. The included
handlers run inside the SQLite result-and-charge transaction. Keep them pure,
bounded and free of network or other external side effects. Changing a service's
contract requires an explicit versioned resource identity and compatible quotes.

Do not insert arbitrary upstream URLs or network calls into that transaction.
An external API needs a durable worker boundary, upstream idempotency/status
lookup, result validation and explicit uncertainty handling. A local atomic charge
does not make external work exactly once.

The next commercial test is one API merchant and an existing authorized buyer
completing useful work and recovery without founder intervention. The arithmetic
example supports development of that workflow; it does not establish willingness
to pay for it.
