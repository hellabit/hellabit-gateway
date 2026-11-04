# Review the exact build, then recheck each change

The owner requested Google review after the developer update. Google review is
**pending** until an actual response is supplied; it is not a release claim or
a substitute for an independent security audit and operating-role review.

## Upload-ready package

After the single verification command succeeds, run:

```sh
python3 scripts/review.py
```

It consumes the accepted source capsule, checks its archive/manifest/source
hashes, and creates an immutable `*-google-review.zip` in `release/`. It does
no cloud upload and needs no Google API key, paid plan or new dependency.

The ZIP contains nine files: seven source bundles, a review request and a
manifest. Source headers retain original repository paths, hashes and line
numbers. All shareable source is included; generated compiled artifacts have
hashes in the manifest and remain in the separate runnable source capsule.
Runtime databases, keys, account records, operator identity and browser evidence
are excluded by the source package boundary. Never upload `.private/`, a local
state directory, the containing workspace or browser/profile files.

Google's [official file upload guide](https://support.google.com/gemini/answer/14903178?co=GENIE.Platform%3DDesktop&hl=en)
describes ZIP uploads containing up to ten files, subject to account availability.
Use Gemini's Upload Files option with the nine-file review ZIP, then ask it to
follow `08_REVIEW_REQUEST.txt` and start with `09_MANIFEST.json`. The source hash
must match the release you want reviewed. If the account has a lower limit,
extract and send the named bundles in order. Do not buy a plan automatically.

For a downloaded source capsule, an explicit bounded input is also supported:

```sh
python3 scripts/review.py --archive PATH_TO_SOURCE.zip --sha256 EXPECTED_ARCHIVE_SHA256
```

Obtain the expected hash from the release and verify its signature with the
previously trusted publisher key. A key downloaded beside a package alone does
not independently establish who published it.

## Checklist for Google and the internal review

1. State the source hash, files actually inspected, execution limits and any
   truncation. A static assessment must not claim it executed tests.
2. Prioritize cross-tenant access, overspending, duplicate charges, uncertain
   external outcomes, late workers, callbacks, replays and asset confusion.
3. Test identity signatures/purpose/claims, local grants, operator MFA freshness,
   revocation, exact hosts, request framing/deadlines and rate limits.
4. Challenge RPC binding, TLS/DNS pinning, backfill, reorgs and finality changes.
   One RPC source is not independent settlement or reserve evidence.
5. Challenge encrypted recovery, restored older state, invoice carry/replay,
   contract authorization and redemption liabilities.
6. Review the desktop/mobile journey for misleading readiness, invalid actions,
   clipped controls and unclear consequences of an unknown outcome.
7. Return each issue with severity, original path/line, trigger, demonstrated
   impact, minimal reproduction, smallest fix and a regression test. Distinguish
   confirmed defects from hypotheses.
8. Independently reproduce findings, implement valid fixes, rerun focused checks,
   then use `python3 scripts/verify.py` for a new immutable source checkpoint.
9. Create a fresh review bundle. A review of an older hash cannot approve the new
   source. Preserve the review text privately with its hash, not as a vague badge.
10. Before publishing, verify source privacy, current dependency checks, actual
    desktop/mobile behavior, hosted CI and independent public/download hashes.
    Before real payments, separately satisfy [ACTIVATION.md](ACTIVATION.md).

## Internal findings fixed before 0.4 publication

| Risk | Implemented control | Regression |
| --- | --- | --- |
| A slow lookup outlives the worker lease | Recheck/fence the claim before starting a send | Expired lookup performs no submission |
| Callback settles while worker is reading | Preserve terminal state and prevent a later send | Callback wins during lookup; one settlement |
| Reused key authenticates another provider's event | Provider-domain HMAC and immutable connection binding | Cross-provider signature/application rejection |
| Finality advances between an earlier read and rewind | Read/check/halt in the write transaction | Finality advance cannot be rewound |
| Small chunks keep resetting a read timeout | One total body deadline | Slow chunks receive 408 |
| Signed tokens contain ambiguous JSON or stale MFA | Strict token JSON and recent signed auth_time | Duplicate claims and stale operator rejection |
| Damaged or huge recovery input consumes/creates state | Copy-size bounds and authenticated SQLite validation | Oversize and damaged database leave no target |

## Publication and history

A developer pre-release can be removed from the owner's repository, but public
copies cannot be recalled reliably. GitHub states that deleting a public
repository [does not delete forks](https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository).
History rewrites also cannot erase other people's clones or all cached references;
see [GitHub's removal limits](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
A fresh repository can start a new visible commit history, but cannot establish
that the earlier publication never existed.

Keep revisions private while building; publish coherent, verified snapshots.
Version numbers describe the release, not whether it has been reviewed or
approved. No repository deletion, private conversion or history rewrite is
performed by either the verification or review-package command.
