# Publication-readiness acceptance revision 3

This is a **post-fix** snapshot, not a pre-implementation baseline. The owner approved a new revision after the requested Pyright fixes changed three ordinary test modules. Existing behavioral assertions were retained; typing/narrowing and one formatter-only blank line changed. The previous `public-readiness` and `public-readiness-v2` directories remain unchanged. Earlier pre/post typing replays of revision 2 remain separate evidence, not a claim that its protected test bytes were unchanged.

The runner is revision 2's reviewed runner with only the task/directory identifier replaced. It keeps the same 46-case selection, seed `20260323`, five shuffled session repetitions and setup/teardown/collection failure accounting. This revision also selects both new malformed-preference regression modules. `protected_paths` recursively freezes all tests as well as the contract, runner and baseline. No existing behavioral assertion is removed, no expected failure added and no timeout inside a test relaxed.

Run from an isolated development environment with the previously approved `pytest-randomly` and `pytest-repeat` installed:

```text
python tests/acceptance/public-readiness-v3/runner.py --repo-root . --output-json .agent/runs/public-readiness-v3.json
```

`baseline.json` records the completed revision run. The independent landing replay must use a clean committed verification anchor with pinned contract/baseline/runner blobs and compare the candidate to that anchor. This is a post-fix freeze/replay, not evidence that source fixes passed the old byte-frozen pre-fix gate. A positive count and no new failed/flaky IDs are required.

The committed revision baseline completed **650 executions** (130 cases, five session repetitions), exit0, no failed/flaky/skipped IDs, in302.25seconds. An earlier invocation could not load the private repeat plugins: zero tests and pytest exit4. It was rejected, preserved separately, and is not the baseline. The successful command used the unchanged main interpreter with the previously installed private plugin target supplied through `PYTHONPATH`.
