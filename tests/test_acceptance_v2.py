"""Unit checks for the v2 landing protocol, not an acceptance replay."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = Path("tests/acceptance/public-readiness-v2")


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "acceptance_v2_runner", ROOT / REVISION / "runner.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protected_paths_are_recursive_and_explicit() -> None:
    contract = json.loads((ROOT / REVISION / "contract.json").read_text(encoding="utf-8"))
    assert contract["protected_paths"] == ["tests/", "tests/acceptance/public-readiness-v2/"]
    assert all(path.endswith("/") for path in contract["protected_paths"])


@pytest.mark.parametrize("copied", [False, True], ids=["sibling", "copied-runner"])
def test_runner_contract_lookup_preserves_pytest_selection(runner, tmp_path, monkeypatch, copied):
    root = tmp_path / "checkout"
    (root / "src/video_data_sync").mkdir(parents=True)
    runner_dir = tmp_path / "isolated" if copied else root / REVISION
    runner_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / REVISION / "runner.py", runner_dir / "runner.py")
    contract_dir = root / REVISION if copied else runner_dir
    contract_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / REVISION / "contract.json", contract_dir / "contract.json")
    expected = json.loads((ROOT / REVISION / "contract.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(runner, "__file__", str(runner_dir / "runner.py"))
    assert runner.load_contract(root) == expected

    # Fake only the pytest dispatch: no acceptance tests run and no passing result is invented.
    calls = []

    def fake_pytest_main(args, **kwargs):
        calls.append(args)
        return 0

    monkeypatch.setitem(sys.modules, "pytest", SimpleNamespace(main=fake_pytest_main))
    output = tmp_path / "unit-dispatch.json"
    monkeypatch.setattr(
        sys, "argv", ["runner.py", "--repo-root", str(root), "--output-json", str(output)]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setenv("QT_QPA_PLATFORM", "unit-test")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")
    assert runner.main() == 2  # Zero executed tests must not be accepted as a successful replay.
    assert calls == [
        [
            *expected["test_selection"],
            "--count=5",
            "--repeat-scope=session",
            "--randomly-seed=20260323",
            "-ra",
            "-p",
            "no:cacheprovider",
        ]
    ]
    assert json.loads(output.read_text(encoding="utf-8"))["executed_test_count"] == 0


def test_missing_contract_fails_closed(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "__file__", str(tmp_path / "isolated/runner.py"))
    with pytest.raises(FileNotFoundError):
        runner.load_contract(tmp_path)


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"task_id": "public-readiness"},
        {"runner": "other.py"},
        {"protected_paths": ["tests"]},
        {"test_selection": []},
        {"test_selection": [None]},
    ],
)
@pytest.mark.parametrize("copied", [False, True], ids=["sibling", "copied-runner"])
def test_invalid_contract_is_rejected(runner, tmp_path, monkeypatch, patch, copied):
    runner_dir = tmp_path / "isolated"
    runner_dir.mkdir()
    contract_dir = tmp_path / REVISION if copied else runner_dir
    contract_dir.mkdir(parents=True, exist_ok=True)
    contract = json.loads((ROOT / REVISION / "contract.json").read_text(encoding="utf-8"))
    contract.update(patch)
    (contract_dir / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(runner, "__file__", str(runner_dir / "runner.py"))
    with pytest.raises(ValueError, match="Invalid public-readiness-v2 contract"):
        runner.load_contract(tmp_path)


@pytest.mark.parametrize("content", ["{", "[]"])
def test_bad_sibling_is_not_replaced_by_valid_checkout_contract(
    runner, tmp_path, monkeypatch, content
):
    contract_dir = tmp_path / REVISION
    contract_dir.mkdir(parents=True)
    shutil.copyfile(ROOT / REVISION / "contract.json", contract_dir / "contract.json")
    runner_dir = tmp_path / "isolated"
    runner_dir.mkdir()
    (runner_dir / "contract.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr(runner, "__file__", str(runner_dir / "runner.py"))
    with pytest.raises(ValueError):
        runner.load_contract(tmp_path)
