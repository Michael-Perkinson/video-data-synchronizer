"""Replay the frozen public-readiness selection against an explicit checkout."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path


class Results:
    """Retain failures from every phase and compare all five repetitions."""

    def __init__(self) -> None:
        self.executed = 0
        self.invocations: dict[str, str] = {}
        self.failed: set[str] = set()
        self.skipped: set[str] = set()

    @staticmethod
    def stable_id(nodeid: str) -> str:
        # pytest-repeat appends [1-5] or the final -1-5 parameter component.
        return re.sub(r"(\[|-)[1-5]-5\]$", lambda m: "" if m[1] == "[" else "]", nodeid)

    @property
    def outcomes(self) -> dict[str, set[str]]:
        grouped: dict[str, set[str]] = {}
        for nodeid, outcome in self.invocations.items():
            grouped.setdefault(self.stable_id(nodeid), set()).add(outcome)
        return grouped

    def pytest_runtest_logreport(self, report) -> None:
        test_id = self.stable_id(report.nodeid)
        if report.failed:
            self.failed.add(test_id)
            self.invocations[report.nodeid] = "failed"
        if report.when == "call" and not report.skipped:
            self.executed += 1
            # A passing call must not hide a setup/teardown failure in the same repetition.
            self.invocations.setdefault(report.nodeid, report.outcome)
        if report.skipped:
            self.skipped.add(test_id)

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.failed.add(f"collection::{report.nodeid}")


def load_contract(root: Path) -> dict:
    """Load a sibling contract or the landing gate's BaseRef-protected checkout copy."""
    path = Path(__file__).with_name("contract.json")
    if not path.exists():
        path = root / "tests/acceptance/public-readiness-v3/contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(contract, dict)
        or type(contract.get("schema_version")) is not int
        or contract["schema_version"] != 1
        or contract.get("task_id") != "public-readiness-v3"
        or contract.get("runner") != "runner.py"
        or contract.get("protected_paths") != ["tests/", "tests/acceptance/public-readiness-v3/"]
        or not isinstance(contract.get("test_selection"), list)
        or not contract["test_selection"]
        or any(not isinstance(item, str) or not item for item in contract["test_selection"])
    ):
        raise ValueError(f"Invalid public-readiness-v3 contract: {path}")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_json.resolve()
    # A failed invocation must not leave an older successful report for a gate to consume.
    output.unlink(missing_ok=True)
    root = args.repo_root.resolve(strict=True)
    contract = load_contract(root)
    if not (root / "src/video_data_sync").is_dir():
        parser.error("--repo-root must contain src/video_data_sync")
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    # Prefer the requested source tree even when this interpreter has an editable install elsewhere.
    sys.path[:0] = [str(root / "src"), str(root)]
    os.chdir(root)
    import pytest

    results = Results()
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = int(
                pytest.main(
                    [
                        *contract["test_selection"],
                        "--count=5",
                        "--repeat-scope=session",
                        "--randomly-seed=20260323",
                        "-ra",
                        "-p",
                        "no:cacheprovider",
                    ],
                    plugins=[results],
                )
            )
    if code > 1:
        results.failed.add(f"acceptance::pytest-exit-{code}")
    normalized = {
        "schema_version": 1,
        "task_id": contract["task_id"],
        "executed_test_count": results.executed,
        "failed_test_ids": sorted(results.failed),
        "flaky_test_ids": sorted(
            test_id
            for test_id, outcomes in results.outcomes.items()
            if {"passed", "failed"} <= outcomes
        ),
        "skipped_test_ids": sorted(results.skipped),
        "pytest_exit_code": code,
        "random_seed": 20260323,
        "repetitions": 5,
    }
    output.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(normalized))
    return code if code else (0 if results.executed > 0 else 2)


if __name__ == "__main__":
    raise SystemExit(main())
