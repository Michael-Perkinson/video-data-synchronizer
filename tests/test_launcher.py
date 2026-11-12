"""Regression tests for launch paths used from a source checkout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_main_py_launches_from_source_checkout() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Video Data Synchronizer" in result.stdout
