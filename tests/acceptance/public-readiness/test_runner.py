"""Tests of the acceptance recorder, independent of Qt and media codecs."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "readiness_runner", Path(__file__).with_name("runner.py")
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RecorderTests(unittest.TestCase):
    def test_repeat_suffix_removed_without_losing_parameters(self):
        self.assertEqual(
            runner.Results.stable_id("tests/a.py::test_it[2-5]"), "tests/a.py::test_it"
        )
        self.assertEqual(
            runner.Results.stable_id("tests/a.py::test_it[mp4-2-5]"),
            "tests/a.py::test_it[mp4]",
        )

    def test_non_repeat_id_unchanged(self):
        self.assertEqual(
            runner.Results.stable_id("tests/a.py::test_it[mp4]"), "tests/a.py::test_it[mp4]"
        )

    def test_failure_and_pass_retained_across_repetitions(self):
        recorder = runner.Results()
        for index, outcome in enumerate(("failed", "passed"), 1):
            recorder.pytest_runtest_logreport(
                SimpleNamespace(
                    nodeid=f"tests/a.py::test_it[{index}-5]",
                    when="call",
                    outcome=outcome,
                    failed=outcome == "failed",
                    skipped=False,
                )
            )
        self.assertEqual(recorder.executed, 2)
        self.assertEqual(recorder.failed, {"tests/a.py::test_it"})
        self.assertEqual(recorder.outcomes["tests/a.py::test_it"], {"failed", "passed"})

    def test_setup_and_teardown_failures_not_lost(self):
        for phase in ("setup", "teardown"):
            recorder = runner.Results()
            recorder.pytest_runtest_logreport(
                SimpleNamespace(
                    nodeid="tests/a.py::test_it[1-5]",
                    when=phase,
                    failed=True,
                    skipped=False,
                )
            )
            self.assertEqual(recorder.failed, {"tests/a.py::test_it"})
            self.assertEqual(recorder.executed, 0)

    def test_repeated_teardown_failure_is_not_misclassified_as_flaky(self):
        recorder = runner.Results()
        for index in range(1, 6):
            nodeid = f"tests/a.py::test_it[{index}-5]"
            recorder.pytest_runtest_logreport(
                SimpleNamespace(
                    nodeid=nodeid,
                    when="call",
                    outcome="passed",
                    failed=False,
                    skipped=False,
                )
            )
            recorder.pytest_runtest_logreport(
                SimpleNamespace(
                    nodeid=nodeid,
                    when="teardown",
                    failed=True,
                    skipped=False,
                )
            )
        self.assertEqual(recorder.executed, 5)
        self.assertEqual(recorder.outcomes["tests/a.py::test_it"], {"failed"})

    def test_skips_do_not_count_as_execution(self):
        recorder = runner.Results()
        recorder.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="tests/a.py::test_it[1-5]",
                when="call",
                failed=False,
                skipped=True,
            )
        )
        self.assertEqual(recorder.executed, 0)
        self.assertEqual(recorder.skipped, {"tests/a.py::test_it"})

    def test_collection_failures_are_retained(self):
        recorder = runner.Results()
        recorder.pytest_collectreport(SimpleNamespace(nodeid="tests/a.py", failed=True))
        self.assertEqual(recorder.failed, {"collection::tests/a.py"})


class InvocationTests(unittest.TestCase):
    def test_invalid_checkout_cannot_leave_a_stale_success_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.json"
            output.write_text('{"executed_test_count": 10}', encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("runner.py")),
                    "--repo-root",
                    str(root),
                    "--output-json",
                    str(output),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
