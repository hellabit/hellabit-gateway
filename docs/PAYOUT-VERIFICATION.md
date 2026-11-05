# Verify recovery on your payout integration

Run the synthetic example from the source root. Python 3.11+ and the source
checkout are sufficient.

```sh
python3 scripts/verify_payout.py
```

For your integration, select one payout operation in a provider test account.
Record the application's outbound request at the test boundary, let the
provider receive it, and interrupt the reply before the application sees it.
Let your normal recovery procedure run. Capture every submission attempt and
fetch the latest matching payments and returns separately from the application.
Normalize the captured test data to the example format. Keep the evidence file
private and use opaque references for recipients and account scopes.
[Example evidence](../examples/payout-recovery.json)

```sh
python3 scripts/verify_payout.py --evidence /path/to/test-payout-evidence.json
```

| Evidence | Required fields and meaning |
| --- | --- |
| Run | `schema` is `hellabit.payout-evidence.v1`, `environment` is `test` or `synthetic`, and `revision` identifies the tested application revision. |
| Command | `scope` selects one provider, environment and account. `reference` identifies the original business instruction and `key` is its submission idempotency key. Include the exact `asset`, positive integer `amount_units` and `recipient_ref`. |
| Attempts | For each submission, include the actual `command`, `reply` of `lost`, `acknowledged` or `rejected`, and the observed `payment_id` for an acknowledgement. Use `null` for the other replies. |
| Lookup | Include the exact queried `scope` and `reference`. Set `coverage` to `all-matches` only when the lookup or export covers every matching payment and return, including pagination and the provider's visibility window. Otherwise use `partial`. |
| Records | Supply the latest record per `payment_id` with its `reference`, `asset`, `amount_units`, `recipient_ref` and normalized `state`. Use `accepted`, `pending`, `settled` or `failed` only when supported by the provider's observed status. |
| Returns | Use `return_id`, `payment_id`, `asset` and `amount_units` for each observed return. An empty list means no return was captured. |

Use the exact asset and its native units on every row. A CAD 28.00 payment uses
2,800 cents. Each array is limited to 1,000 rows and the file to 1 MB. A partial
lookup or an empty result calls for review. Keep the original payment identity
while you gather evidence.

The command writes one JSON report with the input hash, application revision,
outcome, observed amounts and reasons for review. A passing identity check can
report `pending`. The reported unsettled units are the command amount less the
observed settlement plus observed returns, for cases with complete matching
evidence. This calculation describes the supplied test record.

| Exit code | What CI can do |
| --- | --- |
| 0 | Record the original-identity result. If the case requires settlement, also assert the expected `payment_state` and units. |
| 1 | Stop the check. `fail` identifies conflicting evidence, such as two accepted payment IDs. `review` identifies a missing or incomplete proof. |
| 2 | Correct the evidence format or file selection and rerun. |

The verifier uses the provider data supplied by your test harness. Provider
identity, completeness, lookup retention, and the meaning of an accepted or
settled status depend on that provider and account. Record those guarantees
alongside your test setup. The verifier makes zero network calls and zero
financial writes. It reports no independent authentication of your provider.

For Hellabit's own synthetic queue, an enrolled read-only operator can inspect
the exact intent and its current outcome through the SDK. [Operator API](API.md#optional-asgi-interface)
