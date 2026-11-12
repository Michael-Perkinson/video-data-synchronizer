# Public-readiness baseline and frozen acceptance

This is a test-only characterization at source base
`096408c7be3e93585894201353329b20fea7c1b7`. It does not approve publication,
change application behavior, or claim a complete GUI/usability audit.
`contract.json` identifies the runner and protected test sources. Preserve those
sources when comparing later worktrees; changing the tests changes the contract.

## Replay

Use an isolated environment with the project's documented development dependencies
and the explicitly approved audit-only shuffle/repeat plugins:

```text
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install -e ".[dev]" pytest-randomly pytest-repeat
.venv/Scripts/python tests/acceptance/public-readiness/runner.py --repo-root . --output-json .agent/runs/public-readiness.json
```

On POSIX use `.venv/bin/python`. To test a different checkout, pass its absolute
path to `--repo-root`; the runner prioritizes that checkout's `src/` imports and
runs its protected tests. The runner path in the contract is relative to this
directory; protected paths are relative to the target repository root. Keep the
trusted runner/contract and protected tests unchanged during implementation.
The output JSON's sibling `.log` contains complete pytest diagnostics.

The runner requests five session-scoped repetitions and shuffles with
`pytest-randomly`, seed `20260323`. Plugin ordering controls the final collected
item order. It emits schema-v1 JSON with a positive executed count, stable
failed/flaky IDs (repeat suffix removed), skips, and pytest exit
code. Setup, teardown, collection and invocation failures are not discarded.
An ID that both passes and fails is flaky and remains in the failed list.
Skipped tests do not count as execution. An invalid invocation removes a stale
report, and zero executed tests never returns success. Recorder regression tests
are in `test_runner.py`; `test_isolation.py` protects recovery-file isolation.

Before implementation, `baseline.json` is committed as the reference result.
Compare fresh results with it: require matching schema/task, positive execution,
no new failed/flaky IDs, and unchanged protected tests. Missing/malformed reports
or invocation errors cannot pass. Review skips separately; do not silently treat
unavailable codecs as tested. The runner does not itself replace the coordinator's
trusted acceptance/land comparison and independent review gate.

## Behavioral scope

The selected existing tests assert outputs and state, not just mocked calls:

- Real CSV/XLSX files, second/minute conversion, nonuniform sampling, arbitrary
  numeric channels, invalid timestamps/units, corrupt files, finite signals,
  z-score rejection, and deterministic synthetic demo/video generation.
- Source-checkout CLI help using a child process.
- Qt mouse-driven video load/crop/apply, data import, playback/pause, real worker
  export, decoded output frame count, export-history search/settings restore,
  project save/reopen and resumed playback. File choosers and message boxes are
  replaced; video/data reading, widgets, timers, encoding and history are real.
- Decoded crop/colour/layout/speed correctness; relative trace sample timing;
  nonblank encoded trace; cancellation/corrupt input preserve an existing output.
- Full project round-trip including rotation, crop, trace settings, legends,
  preview proportions and reusable preferences; invalid projects preserve active
  identity and canceling dirty-close leaves the window open.

These expectations match the current README/user-guide and timing documentation.
No undocumented feature decision, production bug fix, or expected-failure marker
is introduced. In particular, relative timestamp assertions are **not** proof of
absolute scientific alignment against an independent acquisition clock.

## Environment and evidence

Validation uses Windows, CPython 3.13.3, pytest 9.2.0, pytest-randomly 5.0.0,
pytest-repeat 0.9.4, PySide6 Essentials 6.12.0, OpenCV headless 4.14.0.94,
NumPy 2.4.6, pandas 2.3.3 and Matplotlib 3.11.2. The established repository
venv/pip workflow was retained; nothing was installed globally. Initial dependency
installation hit a 240-second tool limit before installing the editable project;
a bounded retry completed, followed by successful imports and `pip check`.

Available checks are the CI pytest matrix, ruff/pre-commit, mypy, pip-audit,
build/twine, and installed-wheel smoke. Nonmutating ruff checks/format checking
were used rather than pre-commit's auto-fix hooks. Ruff 0.17.0 and mypy 1.20.2
passed; build and twine checks passed; a wheel installed into an isolated target
imported from that target and its module help worked. Initial `pip-audit` found
12 advisory entries (six unique IDs) for the virtualenv's bootstrap pip 25.0.1,
not runtime dependencies. Upgrading only that virtualenv's pip to 26.3 yielded
no known vulnerabilities and a clean `pip check`.

The corrected isolated acceptance baseline executed **230 tests** (46 cases ×
five repetitions), with no failures, flakes or skips, in 168.29 seconds. The fresh
replay executed the same 230 tests in 137.19 seconds and produced an exactly equal
normalized JSON result. Eight recorder/invocation regression tests also passed;
the final short isolation-plus-recorder smoke passed all nine tests in 0.34 seconds.

The separate full-suite diagnostic is **not green**: 1,442 passed, one failed,
and five skipped (unavailable `avc1` encoder). The GUI workflow failed at
`tests/test_end_to_end_audit.py:305`, waiting for `_export_worker` to become `None`;
the other four repetitions passed. Preserve the stable failed/flaky ID
`tests/test_end_to_end_audit.py::test_gui_load_crop_play_export_search_restore`
when evaluating subsequent work. It also passed all ten focused baseline/fresh
executions, so a green focused replay does not establish full-suite stability.
The process returned abnormal native exit `3221226505` (`0xC0000409`) after writing
a complete summary/JUnit report. Reported elapsed time was 28,626.35 seconds across
an interrupted worker session; uninterrupted execution and root cause are not
established. No additional long run was launched on resume. These observations
are frozen separately in `suite-observations.json`; they are not rewritten into
the actually green focused `baseline.json`. No `xfail` or timeout relaxation hides
the unresolved failure. It was observed during this baseline investigation, not
established as pre-existing by an independent clean-base comparison. Eight
unittest recorder tests ran once; pytest functions
were requested five times. Full-suite stability remains an audit follow-up.

### Superseded runs and recovery safety

An early concurrent run executed the original suite and a 225-execution focused
baseline before discovery that existing fixtures isolated QSettings/history but
not `MainWindow._recovery_path`. That focused run passed, but it and the incomplete
full/fresh runs are **superseded**, not acceptance evidence. Only the owned pytest
processes were stopped. The approved test-only autouse fixture now redirects
recovery snapshots and close-handler deletion to each test's temporary directory;
individual recovery tests can still override the path normally.

The prior default Qt test application name was `python`, distinct from the
production application's organization/name. Metadata-only inspection after
stopping the runs, and again after the serial driver exited, found neither
possible recovery file present. There is no
before-state, so previous creation/deletion or interference cannot be ruled out.
No real app-data file was modified or deleted during inspection, and no restore
was attempted. Exact local paths and process evidence are in the private worker
result, not publication artifacts. Corrected validation runs are serial to avoid
cross-process GUI/recovery interference.

## GUI coverage limitations and audit handoff

Qt runs offscreen. The additional captured workflow passed; the widget/menu text
in captured Qt screenshots appeared as square glyphs while Matplotlib axis and
exported scale text remained readable. This is an observed offscreen rendering
limitation, **not a confirmed native-desktop defect**. Screenshots of preview,
restored data view, history, and exported frames are local ignored artifacts;
preview/play and middle-export images were actually opened during inspection.
Native desktop readability, font fallback, keyboard/accessibility, high DPI,
file-picker UX, OS integration and cross-platform appearance remain unverified.
The suite uses synthetic media only; unavailable codec variants must remain
explicitly skipped. There was no user-data, real study-media, publication,
license/provenance, network-privacy, or broad security audit in this ticket.

Highest-risk implementation seams for the subsequent audit are
`gui/main_window.py` (transactional project load, recovery and export-worker
lifecycle), `gui/animated_tab.py` (timer/cache/preview synchronization),
`video_processing/exporter.py` plus `export_history.py` (frame scheduling,
atomic publication and durable failure records), `plotting/composition.py`
(preview/export geometry), and `data/processor.py` (time inference and numerical
transforms), all beneath `src/video_data_sync/`. Existing coverage is substantial
but not an independent scientific validation. Documented missing seek control,
clock-drift correction, standalone packaging and codec variability remain product
limitations rather than newly accepted features or authorized implementation.
