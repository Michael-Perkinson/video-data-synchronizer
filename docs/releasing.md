# Release guide

## Release checklist

1. Confirm that all bundled/sample data are synthetic or have documented redistribution permission.
2. Confirm that `Repository` and `Documentation` under `[project.urls]` in `pyproject.toml`, `repository-code` in `CITATION.cff`, and the README links match the public repository location.
3. Update the version in `src/video_data_sync/__init__.py` and `CITATION.cff`; `pyproject.toml` reads it dynamically from the package. Update the README wheel filename example and prepare release notes. There is no `CHANGELOG.md` in the repository yet; do not refer users to one unless it is added.
4. In a clean development environment, install with `python -m pip install -e ".[dev]"`; run `pre-commit run --all-files`, `pip-audit --skip-editable`, `mypy src`, `python -m pyright`, `pytest`, `python -m build`, and `python -m twine check dist/*`.
5. Create a second clean environment, install the built wheel from `dist/` (not an editable install), run `video-data-sync --help`, open **Help → Open Demo Project**, and smoke-test preview and export.
6. Smoke-test import, preview, alignment, cancellation, and export on Windows, macOS, and Linux. The CI matrix exercises these operating systems with Python 3.11, 3.12, and 3.13; check actual codec support on each release platform.
7. Test CSV and XLSX, an unsupported/corrupt file, no-overlap data, opening a saved project, restoring settings from Export History, and a long enough clip to inspect synchronization at both ends.
8. Push the matching `vX.Y.Z` tag. `.github/workflows/release.yml` reruns release checks, builds the wheel and source archive, creates `SHA256SUMS`, and publishes a GitHub Release with generated notes.

The release workflow does not publish to PyPI or build standalone GUI installers. Users need Python 3.11 or newer and install the platform-independent wheel with pip; codec availability can vary by operating system. No signed/notarized macOS app is published, so do not claim drag-and-drop Mac installation. The repository and documentation URLs are set in `pyproject.toml` and `CITATION.cff`; keep them in step with the repository location if it changes.

## Versioning

Use semantic versioning. Treat changes to offset convention, time inference, transformations, or export timing as user-visible behavior requiring prominent release notes and regression tests.
