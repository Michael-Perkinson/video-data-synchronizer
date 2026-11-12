"""Regression tests: export ownership survives until the QThread really returns.

Result signals (succeeded/error/cancelled) are emitted from inside ``run()``, so
the thread is still alive when the GUI handles them. Ownership of
``MainWindow._export_worker`` and the export busy state must therefore be
released only by ``QThread.finished``. These tests hold the worker thread open
between its terminal signal and the return from ``run()`` with an explicit gate,
so the ordering is deterministic rather than timing dependent.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from PySide6.QtCore import QDeadlineTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow, _ExportWorker
from video_data_sync.video_processing.exporter import ExportCancelled


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
    win._set_dirty(False)
    dispose_widget(win)


def _pump_until(app, predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def _prepare_export(window, synthetic_video, tmp_path, monkeypatch) -> None:
    source = tmp_path / "data.csv"
    source.write_text("Time (s),signal\n0,1\n0.5,2\n1,4\n", encoding="utf-8")
    assert window.data_tab.load_file(str(source), time_unit="Seconds")
    window.video_tab._load_video(str(synthetic_video))
    window.video_tab.end_input.setText("1")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "out.mp4"), "")
    )


def _succeed(**kwargs):
    kwargs["progress_callback"](1, 1)


def _fail(**kwargs):
    raise RuntimeError("synthetic export failure")


def _cancel(**kwargs):
    raise ExportCancelled("Export cancelled.")


@pytest.mark.parametrize(
    "outcome, fake_export",
    [("succeeded", _succeed), ("failed", _fail), ("cancelled", _cancel)],
)
def test_worker_retained_until_thread_finishes_after_terminal_signal(
    app, window, synthetic_video, tmp_path, monkeypatch, outcome, fake_export
):
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda _parent, title, *_: notices.append(("info", title))
    )
    monkeypatch.setattr(
        QMessageBox, "critical", lambda _parent, title, *_: notices.append(("critical", title))
    )
    monkeypatch.setattr("video_data_sync.video_processing.exporter.export_video", fake_export)

    terminal_emitted = threading.Event()
    gate = threading.Event()
    original_run = _ExportWorker.run

    def gated_run(self):
        original_run(self)
        # The terminal signal has been emitted; keep the thread alive so the GUI
        # handles it while run() has not yet returned.
        terminal_emitted.set()
        gate.wait(30)

    monkeypatch.setattr(_ExportWorker, "run", gated_run)
    _prepare_export(window, synthetic_video, tmp_path, monkeypatch)

    window._on_export_requested([1])
    worker = window._export_worker
    assert isinstance(worker, _ExportWorker)
    try:
        assert terminal_emitted.wait(30), "worker never reached its terminal signal"

        def notified() -> bool:
            if outcome == "succeeded":
                return ("info", "Export complete") in notices
            if outcome == "failed":
                return ("critical", "Export failed") in notices
            return "cancelled safely" in window.statusBar().currentMessage()

        assert _pump_until(app, notified), (outcome, notices)

        # Terminal notification handled, thread still inside run(): ownership
        # and the busy state must be retained.
        assert worker.isRunning()
        assert window._export_worker is worker
        assert not window.animated_tab.btn_export.isEnabled()
        assert not window.animated_tab.btn_export_multiple.isEnabled()

        # A second export must still be refused while the thread is alive.
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *args: pytest.fail("dialog opened")
        )
        window._on_export_requested([1])
        assert "already running" in window.statusBar().currentMessage()
        assert window._export_worker is worker
    finally:
        gate.set()
        assert worker.wait(30000)

    # QThread.finished is the release point.
    assert _pump_until(app, lambda: window._export_worker is None)
    assert window.animated_tab.btn_export.isEnabled()
    assert window.animated_tab.btn_export_multiple.isEnabled()
    # Exactly one terminal notification for this outcome.
    if outcome == "succeeded":
        assert notices == [("info", "Export complete")]
    elif outcome == "failed":
        assert notices == [("critical", "Export failed")]
    else:
        assert notices == []


def test_close_while_thread_running_after_terminal_signal_requests_cancel_and_waits(
    app, window, synthetic_video, tmp_path, monkeypatch
):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr("video_data_sync.video_processing.exporter.export_video", _succeed)
    terminal_emitted = threading.Event()
    gate = threading.Event()
    original_run = _ExportWorker.run

    def gated_run(self):
        original_run(self)
        terminal_emitted.set()
        gate.wait(30)

    monkeypatch.setattr(_ExportWorker, "run", gated_run)
    _prepare_export(window, synthetic_video, tmp_path, monkeypatch)
    window._on_export_requested([1])
    worker = window._export_worker
    assert isinstance(worker, _ExportWorker)
    waits: list[QDeadlineTimer | QDeadlineTimer.ForeverConstant | int] = []
    try:
        assert terminal_emitted.wait(30)
        app.processEvents()

        # The close guard must still see the running worker; it must not wait
        # forever, so report that the thread did not stop in time.
        # Instance stub (not monkeypatch) so teardown never touches a worker
        # that deleteLater may already have destroyed; removed in finally.
        def record_wait(
            deadline: QDeadlineTimer | QDeadlineTimer.ForeverConstant | int = 0,
        ) -> bool:
            waits.append(deadline)
            return False

        worker.wait = record_wait
        monkeypatch.setattr(window, "_confirm_discard", lambda: True)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
        event = QCloseEvent()
        window.closeEvent(event)
        assert waits, "close did not wait for the still-running export thread"
        assert not event.isAccepted()
        assert window._export_worker is worker
    finally:
        gate.set()
        try:
            del worker.wait
        except AttributeError:
            pass
        # Really join the thread (class method, bypassing any instance stub).
        assert _ExportWorker.wait(worker, 30000)
    assert _pump_until(app, lambda: window._export_worker is None)


def test_stale_finished_does_not_clear_newer_worker(window):
    anim = window.animated_tab
    # No inputs are loaded, so the Animated tab page (an ancestor of the export
    # buttons) is disabled and isEnabled() would be False regardless of the busy
    # state. isEnabledTo(anim) reports only the buttons' own busy-controlled
    # enabled flag, so it isolates what _clear_export_worker is responsible for.
    buttons = (anim.btn_export, anim.btn_export_multiple)

    def export_controls_enabled() -> list[bool]:
        return [button.isEnabledTo(anim) for button in buttons]

    assert export_controls_enabled() == [True, True]

    old = _ExportWorker({})
    new = _ExportWorker({})
    window._export_worker = new
    anim.set_export_busy(True)
    assert export_controls_enabled() == [False, False]

    # A stale finished signal from an older worker must not release the newer one.
    window._clear_export_worker(old)
    assert window._export_worker is new
    assert export_controls_enabled() == [False, False]

    # The current worker's finished signal releases ownership and the busy state.
    window._clear_export_worker(new)
    assert window._export_worker is None
    assert export_controls_enabled() == [True, True]
