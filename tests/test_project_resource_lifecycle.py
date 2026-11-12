"""Project loads must not leak native video handles from replaced or staged tabs."""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow
from video_data_sync.video_processing.capture import VideoCapture


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *args: None)
    win = MainWindow()
    win._preload_done = True
    win._ensure_data_tab()
    win._ensure_animated_tab()
    yield win
    win._set_dirty(False)
    dispose_widget(win)


@pytest.fixture
def opened_captures(monkeypatch):
    """Record every VideoCapture constructed while the test runs."""
    created: list[VideoCapture] = []
    original_init = VideoCapture.__init__

    def recording_init(self, path):
        original_init(self, path)
        created.append(self)

    monkeypatch.setattr(VideoCapture, "__init__", recording_init)
    return created


def _is_open(capture: VideoCapture) -> bool:
    return bool(capture._cap.isOpened())


def _live_captures(win: MainWindow) -> list[VideoCapture]:
    live = [win.video_tab._cap]
    if win.animated_tab is not None:
        live.append(win.animated_tab._cap)
    return [cap for cap in live if cap is not None]


def _video_document(path, crop=None) -> dict:
    video = {"path": str(path), "start_s": 0, "end_s": 1}
    if crop is not None:
        video["crop"] = crop
    return {"schema_version": 1, "video": video}


def test_successful_project_load_releases_replaced_video_tab(
    window, synthetic_video, tmp_path, opened_captures
):
    old_video_tab = window.video_tab
    old_video_tab._load_video(str(synthetic_video))
    old_capture = old_video_tab._cap
    assert old_capture is not None and _is_open(old_capture)

    assert window._load_project_path(
        str(tmp_path / "replacement.vdsp.json"),
        remember=False,
        clear_recovery=False,
        document=_video_document(synthetic_video),
    )

    assert window.video_tab is not old_video_tab
    assert window.tabs.widget(0) is window.video_tab
    assert old_video_tab._cap is None
    assert not _is_open(old_capture)
    live = _live_captures(window)
    assert window.video_tab._cap in live and _is_open(window.video_tab._cap)
    leaked = [cap for cap in opened_captures if _is_open(cap) and cap not in live]
    assert leaked == []


def test_rejected_project_load_releases_staged_tabs_and_keeps_current_project(
    window, synthetic_video, tmp_path, opened_captures
):
    current_tabs = (window.video_tab, window.data_tab, window.animated_tab)
    window.video_tab._load_video(str(synthetic_video))
    current_capture = window.video_tab._cap
    assert current_capture is not None
    before = set(map(id, opened_captures))

    assert not window._load_project_path(
        str(tmp_path / "fractional-crop.vdsp.json"),
        remember=False,
        clear_recovery=False,
        document=_video_document(synthetic_video, crop=[0.9, 1, 32, 24]),
    )

    staged = [cap for cap in opened_captures if id(cap) not in before]
    assert staged, "the rejected load should have staged at least one capture"
    assert [cap for cap in staged if _is_open(cap)] == []
    assert (window.video_tab, window.data_tab, window.animated_tab) == current_tabs
    assert window.tabs.widget(0) is window.video_tab
    assert window.video_tab._cap is current_capture
    assert _is_open(current_capture)
    assert window.video_tab._cap.path == str(synthetic_video)
