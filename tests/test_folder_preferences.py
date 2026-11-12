import builtins
import json
import os
from pathlib import Path

import pytest

from video_data_sync.data import loader


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "preferences" / "settings.json"
    path.parent.mkdir()
    monkeypatch.setattr(loader, "_SETTINGS_FILE", str(path))
    return path


@pytest.mark.parametrize(
    "content",
    [
        b"[]",
        b"null",
        b"true",
        b"123",
        b'"folder"',
        b"{broken",
        b"\xff",
        b"{}",
        b'{"folder_path": null}',
        b'{"folder_path": []}',
        b'{"folder_path": true}',
        b'{"folder_path": 123}',
        b'{"folder_path": ""}',
    ],
)
def test_read_folder_path_falls_back_for_malformed_preferences(settings_file: Path, content: bytes):
    settings_file.write_bytes(content)

    assert loader.read_folder_path() == os.path.expanduser("~")
    assert settings_file.read_bytes() == content


def test_read_folder_path_falls_back_for_missing_file(settings_file: Path):
    assert loader.read_folder_path() == os.path.expanduser("~")
    assert not settings_file.exists()


@pytest.mark.parametrize("folder", ["relative/../folder", "Z:/not-a-real-folder", "  "])
def test_read_folder_path_preserves_nonempty_string(settings_file: Path, folder: str):
    settings_file.write_text(json.dumps({"folder_path": folder}), encoding="utf-8")

    assert loader.read_folder_path() == folder


@pytest.mark.parametrize("error", [PermissionError, IsADirectoryError])
def test_read_folder_path_falls_back_for_unreadable_file(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
):
    def unreadable(*args, **kwargs):
        raise error("cannot read preferences")

    monkeypatch.setattr(loader, "open", unreadable, raising=False)

    assert loader.read_folder_path() == os.path.expanduser("~")


@pytest.mark.parametrize("content", [b"[]", b"null", b"true", b"123", b'"x"', b"{", b"\xff"])
def test_write_folder_path_recovers_malformed_preferences(settings_file: Path, content: bytes):
    settings_file.write_bytes(content)

    loader.write_folder_path("new/folder")

    assert json.loads(settings_file.read_text(encoding="utf-8")) == {"folder_path": "new/folder"}


def test_write_folder_path_preserves_unrelated_keys(settings_file: Path):
    original = {"folder_path": "old", "other": {"list": [1, True, None]}, "enabled": False}
    settings_file.write_text(json.dumps(original), encoding="utf-8")

    loader.write_folder_path("new")

    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        **original,
        "folder_path": "new",
    }


def test_write_folder_path_creates_missing_preferences(settings_file: Path):
    settings_file.parent.rmdir()

    loader.write_folder_path("new")

    assert json.loads(settings_file.read_text(encoding="utf-8")) == {"folder_path": "new"}


@pytest.mark.parametrize("failed_mode", ["r", "w"])
def test_write_folder_path_propagates_permission_errors_without_overwriting(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch, failed_mode: str
):
    original = b'{"folder_path": "original", "other": 42}'
    settings_file.write_bytes(original)

    def restricted_open(file, mode="r", **kwargs):
        if mode == failed_mode:
            raise PermissionError("preferences access denied")
        return builtins.open(file, mode, **kwargs)

    monkeypatch.setattr(loader, "open", restricted_open, raising=False)

    with pytest.raises(PermissionError, match="preferences access denied"):
        loader.write_folder_path("new")
    assert settings_file.read_bytes() == original


def test_write_folder_path_propagates_directory_creation_error(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
):
    def denied(*args, **kwargs):
        raise PermissionError("cannot create preferences directory")

    monkeypatch.setattr(loader.os, "makedirs", denied)

    with pytest.raises(PermissionError, match="cannot create preferences directory"):
        loader.write_folder_path("new")
    assert not settings_file.exists()
