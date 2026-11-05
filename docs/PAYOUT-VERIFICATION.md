# Verify recovery on your payout integration

Run the synthetic exercise from the source root with the same Python and SQLite
versions as the console. It interrupts an actual sandbox submission after the
provider accepts it, reads the retained hold from a reopened ledger, and reads
the settlement and return after recovery. Use `--capture-output PATH` to save
the exact evidence for a later check. The command creates a private file at a
new path.

```sh
python3 scripts/verify_payout.py
```

For your integration, select one payout operation in a provider test account.
Record the application's outbound request at the test boundary, let the
provider receive it, and interrupt the reply before the application sees it.
Observe the application after the interruption and again after its normal
recovery procedure. Read durable application state and fetch matching payments
and returns independently from the provider. Record each submission attempt.
Keep the evidence file private and use opaque references for recipients and
account scopes. The local exercise shows the capture with a synthetic provider.
[Example evidence](../examples/payout-recovery.json)

```sh
python3 scripts/verify_payout.py --evidence /path/to/test-payout-evidence.json
```

| Evidence | Required fields and meaning |
| --- | --- |
| Context | `schema` is `hellabit.payout-evidence.v2`, `environment` is `test` or `synthetic`, and `revision` identifies the tested application revision. |
| Run | Record a unique `id`, profile `settled-after-lost-reply.v1`, start time `started_at_ms`, interruption time `interrupted_at_ms`, and recovery budget `deadline_ms`. Use millisecond timestamps anchored to one clock. |
| Command | `scope` selects one provider, environment and account. `reference` identifies the original business instruction and `key` is its submission idempotency key. Include the exact `asset`, positive integer `amount_units` and `recipient_ref`. |
| Attempts | For each submission, include the actual `command`, `reply` of `lost`, `acknowledged` or `rejected`, and the observed `payment_id` for an acknowledgement. Use `null` for the other replies. |
| Lookup | Include the exact queried `scope` and `reference`. Set `coverage` to `all-matches` only when the lookup or export covers every matching payment and return, including pagination and the provider's visibility window. Otherwise use `partial`. |
| Records | Supply the latest record per `payment_id` with its `reference`, `asset`, `amount_units`, `recipient_ref` and normalized `state`. Use `accepted`, `pending`, `settled` or `failed` only when supported by the provider's observed status. |
| Returns | Use `return_id`, `payment_id`, `asset` and `amount_units` for each observed return. An empty list means no return was captured. |
| Application | Record `before` and `after` readbacks with the full `command`, `intent_id`, `state`, `payment_id`, `held_units`, `settled_units`, `returned_units`, and `observed_at_ms`. The interrupted payout starts unknown with the original hold. Recovery confirms that same intent and provider payment with the hold cleared and settlement and return amounts matching the independent records. |

Use the exact asset and its native units on every row. A CAD 28.00 payment uses
2,800 cents. Each array is limited to 1,000 rows and the file to 1 MB. A partial
lookup or an empty result calls for review. Keep the original payment identity
while you gather evidence.

The report contains separate provider identity and application recovery verdicts.
A provider identity check can pass while payment is pending. A full pass requires
settlement, the matching application outcome and recovery inside the declared
deadline. Version 1 evidence still supplies the provider verdict and reports
application recovery as unproven. The unsettled amount describes the provider
record as observed, before any decision about the remaining obligation.

In CI, capture from the build under test and check against independently
supplied pipeline identifiers.

```sh
python3 scripts/verify_payout.py --evidence "$PAYOUT_EVIDENCE" \
  --expected-revision "$APPLICATION_COMMIT" --expected-run-id "$CAPTURE_RUN_ID" \
  --max-recovery-ms 5000
```

| Exit code | What CI can do |
| --- | --- |
| 0 | Record the matched identity, confirmed application readback, amounts and elapsed recovery time. |
| 1 | Stop the check. `fail` identifies conflicting evidence, such as two accepted payment IDs. `review` identifies a missing or incomplete proof. |
| 2 | Correct the evidence format or file selection and rerun. |

## Decide whether the integration is worth changing

Pick one payout flow with a meaningful cost when the original outcome goes
missing. Before testing, write down the provider test account, application build,
asset, interruption to reproduce, recovery deadline and who owns any manual
reconciliation. Use the same case for the existing flow and any proposed change.

| Observed result | Next decision |
| --- | --- |
| The original payment is found once, the application clears the exact hold and both readbacks agree inside the deadline. | Keep the regression check. The test gives no reason to replace a working integration. |
| The provider exposes the original identity, but the application retries with a different one, cannot recover, or records the wrong balances. | Fix that path and repeat the same capture. An outbox or a targeted accounting fix may be enough. |
| Provider lookup is partial, no independent application readback is available, or the agreement does not define who resolves ambiguity. | Keep the result open. Assign an owner and get the missing evidence before making an operating decision. |

Keep the private evidence file and verifier output together. Record the build,
provider lookup and retention terms, observed time, deadline and any untested
failure cases. A pass applies to that captured test. Changes to the build,
account, lookup contract, configuration or operating responsibilities call for
another review. A signed package proves source identity, internal reconciliation
checks journal consistency, and a provider settlement statement is separate
evidence of actual money movement.

The verifier checks the observations supplied by your test harness. Provider
identity, completeness, lookup retention, and the meaning of an accepted or
settled status depend on that provider and account. Record those guarantees
alongside your test setup. File verification uses no network or ledger writes.
The built-in exercise writes only to a disposable sandbox. For supplied files,
the capture process establishes the provenance of the application and provider
observations.

For Hellabit's own synthetic queue, an enrolled read-only operator can inspect
the exact intent and its current outcome through the SDK. [Operator API](API.md#optional-asgi-interface)
