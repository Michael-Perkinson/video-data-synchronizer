# Publication-readiness acceptance revision4

This is a **post-fix** freeze, not a pre-implementation baseline. Revision3 exposed a missing persistence assertion during independent Sol high review: restoring a malformed line width displayed the correct fallback but rewrote the stored setting. The follow-up fix preserves the previous suspension state while applying the value. Sixteen added cases and stronger assertions now verify unchanged INI bytes/unrelated settings, valid and malformed values, finite clamping, prior suspension state, and normal user edits that must still persist. The source red/green evidence is separate:16failed53passed before the fix;69passed after.

All original assertions are retained. The previous public-readiness, public-readiness-v2 and public-readiness-v3 directories remain unchanged; no claim is made that revision3's immutable gate accepts the strengthened test bytes. This successor follows the owner's approved post-fix freeze-and-replay procedure, rather than editing an old contract or baseline.

The runner is revision3's runner with only the task/directory identifier changed. The selection, seed20260323 and five shuffled session repetitions are unchanged:146cases,730executions expected. Recursive `tests/` protection includes all behavioral tests and acceptance artifacts. Failures from setup, call, teardown and collection are retained; zero executions fail closed. The gate copies the committed runner and checks the checkout contract against pinned blobs; direct runner use alone is not the frozen gate.

Run with the approved private repeat/randomly plugins available to the interpreter:

```text
python tests/acceptance/public-readiness-v4/runner.py --repo-root . --output-json .agent/runs/public-readiness-v4.json
```

Commit baseline.json only after a positive completed run. Then replay with the independent landing helper, explicit commit/contract/baseline/runner pins and a separate clean verification anchor. Keep earlier failed attempts and all superseded evidence separately. A successful replay does not prove every OS/codec/accessibility case or authorize publication.

The completed revision baseline ran730executions with no failed, flaky or skipped IDs, native exit0, in339.96seconds. This positive result, not an expected count or a pending run, is the committed baseline.
