# Contributing

Thank you for helping improve Video Data Synchronizer. Please keep changes focused and describe how they affect the user workflow, timing assumptions, and exported results.

## Development setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

## Before opening a pull request

Run the checks used by CI:

```bash
pytest
ruff check .
mypy src
pyright
pip-audit --skip-editable
python -m build
python -m twine check dist/*
```

Pyright checks both application code and tests; `mypy src` alone does not check test helpers.
Run it with the development virtual environment activated so it can resolve the installed
Qt, NumPy, and pandas packages. When using an external Pyright installation, pass
`--pythonpath` with the full path to that environment's Python executable. Do not
exclude tests or suppress diagnostics to obtain a clean result. Tests that intentionally
pass invalid inputs should keep that intent explicit and limit any type escape to that input.

For GUI tests on headless Linux, set `QT_QPA_PLATFORM=offscreen`. CI exercises Windows, macOS, and Linux on Python 3.11, 3.12, and 3.13.

Add or update tests for behavior changes. In particular, changes to time-column inference, offset conventions, trace transforms, crop/rotation, or export timing need regression coverage and explicit documentation. Keep sample recordings synthetic or document redistribution permission; never add study media or confidential participant/animal data. Do not include absolute paths or local project files in fixtures.

## Pull requests

Use the pull request template. Summarize the user-visible change, its scientific/timing implications, and verification performed. Note any platform or codec limitations. Avoid unrelated formatting or generated outputs.
