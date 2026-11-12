"""Regression tests: an accepted main-window close releases native media handles.

Child tabs never receive their own ``closeEvent`` when the main window closes,
so the main window must release the OpenCV captures and stop preview playback
itself. Otherwise those handles survive until interpreter teardown. A close
that is refused (unsaved-changes cancel, export still stopping) must leave the
session untouched.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    win = MainWindow()
    win._preload_done = True
    win._ensure_data_tab()
    win._ensure_animated_tab()
    yield win
    # Destroy this test's window while the QApplication is alive instead of
    # leaving it for interpreter teardown. The helper drops fake workers but
    # joins (or reports) a real running one rather than discarding it.
    win._set_dirty(False)
    dispose_widget(win)


def _open_media(window, synthetic_video):
    """Open the video in both tabs and start preview playback."""
    window.video_tab._load_video(str(synthetic_video))
    video_cap = window.video_tab._cap
    anim = window.animated_tab
    assert anim is not None
    anim.update_video(str(synthetic_video), 10, 0, 1.0, None, None)
    anim_cap = anim._cap
    assert video_cap is not None and video_cap._cap.isOpened()
    assert anim_cap is not None and anim_cap._cap.isOpened()
    anim._timer.start(1000)
    assert anim._timer.isActive()
    return video_cap, anim_cap


def test_accepted_close_releases_captures_and_stops_playback(window, synthetic_video):
    video_cap, anim_cap = _open_media(window, synthetic_video)
    window._set_dirty(False)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert window.video_tab._cap is None and not video_cap._cap.isOpened()
    assert window.animated_tab._cap is None and not anim_cap._cap.isOpened()
    assert not window.animated_tab._timer.isActive()
    assert not window._recovery_timer.isActive()


def test_cancelled_dirty_close_keeps_media_open(window, synthetic_video, monkeypatch):
    video_cap, anim_cap = _open_media(window, synthetic_video)
    window._set_dirty(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert window.video_tab._cap is video_cap and video_cap._cap.isOpened()
    assert window.animated_tab._cap is anim_cap and anim_cap._cap.isOpened()
    assert window.animated_tab._timer.isActive()


def test_close_blocked_by_running_export_keeps_media_open(window, synthetic_video, monkeypatch):
    class Worker:
        def isRunning(self):
            return True

        def request_cancel(self):
            pass

        def wait(self, timeout):
            return False

    video_cap, anim_cap = _open_media(window, synthetic_video)
    window._set_dirty(False)
    window._export_worker = Worker()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert window.video_tab._cap is video_cap and video_cap._cap.isOpened()
    assert window.animated_tab._cap is anim_cap and anim_cap._cap.isOpened()
