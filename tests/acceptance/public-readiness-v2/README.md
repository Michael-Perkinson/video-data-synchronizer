# Public-readiness fixture-cleanup revision

This is a **post-fix acceptance revision**, not a pre-implementation baseline.
The owner approved ownership-scoped Qt fixture teardown changes after the original
full suite exposed native shutdown failures. All original behavioral assertions
remain; test-owned windows, captures and figures now have explicit teardown.
The original contract, runner, baseline and suite observations in
`../public-readiness/` remain unchanged as historical evidence.

The copied runner retains the original 46-case selection, five session repetitions
and seed 20260323. Only task identity and the protected-path set differ: this
revision protects all tests, including new regression coverage and teardown.
The revision does not hide the original full-suite timeout/flaky ID or abnormal
native exit. New successful full-suite and native Windows checks are separate
parent audit evidence, not a rewrite of those observations.

Replay from the repository root:

```text
python tests/acceptance/public-readiness-v2/runner.py --repo-root . --output-json .agent/runs/public-readiness-v2.json
```

The landing gate copies only `runner.py` into an isolated temporary directory.
The runner prefers its sibling `contract.json`; when that sibling is absent, it
loads `tests/acceptance/public-readiness-v2/contract.json` from `--repo-root`.
Before executing the runner, landing checks this protected path against the pinned
BaseRef. This check is the trust boundary for the fallback selection; invoking the
runner alone does not verify the checkout contract against BaseRef. Missing or
invalid contracts fail closed, including incorrect schema or task identity.
`protected_paths` uses `tests/` for recursive protection and explicitly includes
`tests/acceptance/public-readiness-v2/` as required by the landing protocol.

The first v2 attempt timed out at 210 seconds and is excluded from the baseline.
`baseline.json` records the completed replay: 230 tests in 223 seconds, with no
failed, flaky or skipped IDs. Require positive execution,
matching schema/task identity, no new failed/flaky IDs, reviewed skips, and
unchanged protected tests after this revision is committed. No result proves
absolute scientific alignment, all codecs/platforms, OS shell dialog behavior or
complete accessibility. See the original README for detailed selection limits.
