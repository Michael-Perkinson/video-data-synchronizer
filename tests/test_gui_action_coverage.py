"""Offscreen tests that dispatch real menu QActions through cancel and failure paths.

Native dialogs are replaced by recording stubs. Exceptions raised inside a slot
invoked by ``QAction.trigger()`` are printed by PySide rather than raised into
the test, so these tests assert on recorded calls and on window state
afterwards instead of failing from inside a stub.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from qt_teardown import dispose_widget

import video_data_sync.settings.presets as presets_module
from video_data_sync import __version__
from video_data_sync.gui.main_window import MainWindow, _DemoWorker


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


class Recorder:
    """Callable stub that records positional arguments and returns queued results."""

    def __init__(self, *results):
        self.calls: list[tuple] = []
        self._results = list(results)

    def __call__(self, *args, **_kwargs):
        self.calls.append(args)
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0] if self._results else None


def _action(window: MainWindow, label: str) -> QAction:
    for action in window.findChildren(QAction):
        if action.text().replace("&", "") == label:
            return action
    raise AssertionError(f"No menu action labelled {label!r}")


def _project_files(directory) -> list:
    """Project files written to ``directory``, ignoring conftest's recovery snapshot."""
    return [p for p in directory.glob("*.vdsp.json") if p.name != "recovery.vdsp.json"]


def _pump_until(app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        app.processEvents()
        time.sleep(0.01)
    return True


# ----------------------------------------------------------------------
# File menu: open / save / new cancellation
# ----------------------------------------------------------------------


def test_open_project_action_cancelled_dialog_preserves_state(window, synthetic_video, monkeypatch):
    window.video_tab._load_video(str(synthetic_video))
    video_tab, cap = window.video_tab, window.video_tab._cap
    window._set_dirty(False)
    dialog = Recorder(("", ""))
    load = Recorder(True)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", dialog)
    # Class-level patches: monkeypatch undo runs after the window is deleted.
    monkeypatch.setattr(MainWindow, "_load_project_path", load)

    _action(window, "Open Project…").trigger()

    assert len(dialog.calls) == 1
    assert load.calls == []
    assert window.video_tab is video_tab and window.video_tab._cap is cap
    assert cap._cap.isOpened()
    assert window._project_path is None
    assert not window._dirty


def test_open_project_action_dirty_cancel_never_opens_dialog(window, tmp_path, monkeypatch):
    current = str(tmp_path / "current.vdsp.json")
    window._project_path = current
    window._set_dirty(True)
    question = Recorder(QMessageBox.StandardButton.Cancel)
    dialog = Recorder((str(tmp_path / "other.vdsp.json"), ""))
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", dialog)

    _action(window, "Open Project…").trigger()

    assert len(question.calls) == 1
    assert dialog.calls == []
    assert window._project_path == current
    assert window._dirty


def test_open_project_dirty_save_then_cancelled_save_as_aborts_open(window, tmp_path, monkeypatch):
    window._set_dirty(True)
    question = Recorder(QMessageBox.StandardButton.Save)
    save_dialog = Recorder(("", ""))
    open_dialog = Recorder((str(tmp_path / "other.vdsp.json"), ""))
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", open_dialog)

    _action(window, "Open Project…").trigger()

    assert len(question.calls) == 1
    assert len(save_dialog.calls) == 1
    assert open_dialog.calls == []
    assert window._project_path is None
    assert window._dirty
    assert _project_files(tmp_path) == []


def test_open_project_relink_cancel_keeps_current_identity(
    window, synthetic_video, tmp_path, monkeypatch
):
    window.video_tab._load_video(str(synthetic_video))
    video_tab, cap = window.video_tab, window.video_tab._cap
    current = str(tmp_path / "current.vdsp.json")
    window._project_path = current
    window._set_dirty(False)
    recent_before = window._recent_projects()
    moved = tmp_path / "moved.vdsp.json"
    moved.write_text(
        '{"schema_version": 1, "video": {"path": "missing.mp4", "start_s": 0, "end_s": 1},'
        ' "data": {}, "composition": {}}',
        encoding="utf-8",
    )
    dialog = Recorder((str(moved), ""), ("", ""))
    critical = Recorder()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", dialog)
    monkeypatch.setattr(QMessageBox, "critical", critical)

    _action(window, "Open Project…").trigger()

    assert len(dialog.calls) == 2
    assert dialog.calls[1][1] == "Relink missing video"
    assert len(critical.calls) == 1
    assert critical.calls[0][1] == "Open failed"
    assert "Missing video source" in critical.calls[0][2]
    assert window._project_path == current
    assert window.video_tab is video_tab and window.video_tab._cap is cap
    assert cap._cap.isOpened()
    assert not window._dirty
    assert window._recent_projects() == recent_before


@pytest.mark.parametrize("label", ["Save Project", "Save Project As…"])
def test_save_actions_cancelled_dialog_write_nothing(window, tmp_path, monkeypatch, label):
    window._set_dirty(True)
    recent_before = window._recent_projects()
    dialog = Recorder(("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)

    _action(window, label).trigger()

    assert len(dialog.calls) == 1
    assert window._project_path is None
    assert window._dirty
    assert window._recent_projects() == recent_before
    assert _project_files(tmp_path) == []


def test_new_project_action_dirty_cancel_preserves_media(window, synthetic_video, monkeypatch):
    window.video_tab._load_video(str(synthetic_video))
    video_tab, cap = window.video_tab, window.video_tab._cap
    window._set_dirty(True)
    question = Recorder(QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", question)

    _action(window, "New Project").trigger()

    assert len(question.calls) == 1
    assert window.video_tab is video_tab and window.video_tab._cap is cap
    assert cap._cap.isOpened()
    assert window._dirty


# ----------------------------------------------------------------------
# Presets menu: cancellation and read/write failures
# ----------------------------------------------------------------------


def test_save_preset_action_cancel_does_not_write(window, monkeypatch):
    folder_before = window._settings.value("folders/preset")
    dialog = Recorder(("", ""))
    save = Recorder()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    monkeypatch.setattr(presets_module, "save_preset", save)

    _action(window, "Save Appearance Preset…").trigger()

    assert len(dialog.calls) == 1
    assert save.calls == []
    assert window._settings.value("folders/preset") == folder_before
    assert not window.statusBar().currentMessage().startswith("Appearance preset saved")


def test_save_preset_action_write_error_reports_and_keeps_settings(window, tmp_path, monkeypatch):
    folder_before = window._settings.value("folders/preset")

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    critical = Recorder()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", Recorder((str(tmp_path / "look"), "")))
    monkeypatch.setattr(presets_module, "save_preset", fail)
    monkeypatch.setattr(QMessageBox, "critical", critical)

    _action(window, "Save Appearance Preset…").trigger()

    assert len(critical.calls) == 1
    assert critical.calls[0][1] == "Preset could not be saved"
    assert "disk full" in critical.calls[0][2]
    assert window._settings.value("folders/preset") == folder_before
    assert not window.statusBar().currentMessage().startswith("Appearance preset saved")


def test_load_preset_action_cancel_applies_nothing(window, monkeypatch):
    window._set_dirty(False)
    apply = Recorder()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", Recorder(("", "")))
    monkeypatch.setattr(MainWindow, "_apply_appearance_preset", apply)

    _action(window, "Load Appearance Preset…").trigger()

    assert apply.calls == []
    assert not window._dirty


def test_load_preset_action_bad_json_reports_and_keeps_state(window, tmp_path, monkeypatch):
    window._set_dirty(False)
    folder_before = window._settings.value("folders/preset")
    width_before = window._settings.value("data/lineWidth")
    bad = tmp_path / "bad.vdsp-preset.json"
    bad.write_text("{not json", encoding="utf-8")
    critical = Recorder()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", Recorder((str(bad), "")))
    monkeypatch.setattr(QMessageBox, "critical", critical)

    _action(window, "Load Appearance Preset…").trigger()

    assert len(critical.calls) == 1
    assert critical.calls[0][1] == "Preset could not be loaded"
    assert "Invalid JSON" in critical.calls[0][2]
    assert not window._dirty
    assert window._settings.value("folders/preset") == folder_before
    assert window._settings.value("data/lineWidth") == width_before


# ----------------------------------------------------------------------
# Help menu: About and demo worker lifecycle
# ----------------------------------------------------------------------


def test_about_action_dispatches_once_without_blocking(window, monkeypatch):
    about = Recorder()
    monkeypatch.setattr(QMessageBox, "about", about)

    _action(window, "About").trigger()

    assert len(about.calls) == 1
    parent, title, text = about.calls[0][:3]
    assert parent is window
    assert title == "About Video Data Synchronizer"
    assert __version__ in text


@pytest.fixture
def gated_demo(window, monkeypatch, tmp_path):
    """Demo worker that emits its result and then stays running until released.

    This reproduces the real ordering deterministically: ready/error is
    delivered to the UI thread before QThread.finished.
    """
    state = {
        "mode": "ready",
        "gate": threading.Event(),
        "emitted": threading.Event(),
        "workers": [],
        "loads": Recorder(True),
        "critical": Recorder(),
    }

    def run(self):
        try:
            if state["mode"] == "ready":
                self.ready.emit(str(tmp_path / "demo.mp4"), str(tmp_path / "demo.csv"))
            else:
                self.error.emit("generator exploded")
        finally:
            state["emitted"].set()
            state["gate"].wait(10)

    def start(self):
        state["workers"].append(self)
        QThread.start(self)

    monkeypatch.setattr(_DemoWorker, "run", run)
    monkeypatch.setattr(_DemoWorker, "start", start)
    monkeypatch.setattr(MainWindow, "_load_demo_files", state["loads"])
    monkeypatch.setattr(QMessageBox, "critical", state["critical"])
    window._set_dirty(False)
    yield state
    state["gate"].set()
    for worker in state["workers"]:
        try:
            worker.wait(5000)
        except RuntimeError:  # C++ object already deleted after finishing
            pass


def test_demo_worker_reference_survives_until_thread_finishes(app, window, gated_demo):
    _action(window, "Open Demo Project").trigger()
    assert len(gated_demo["workers"]) == 1
    worker = gated_demo["workers"][0]
    try:
        assert gated_demo["emitted"].wait(5)
        assert _pump_until(app, lambda: gated_demo["loads"].calls)
        # The result has been handled but the thread is still running.
        assert worker.isRunning()
        assert window._demo_worker is worker
        # A second request in this window is refused rather than racing.
        _action(window, "Open Demo Project").trigger()
        assert len(gated_demo["workers"]) == 1
    finally:
        gated_demo["gate"].set()
        worker.wait(5000)
    assert _pump_until(app, lambda: window._demo_worker is None)
    assert len(gated_demo["loads"].calls) == 1


def test_demo_worker_failure_reports_and_clears_after_finish(app, window, gated_demo):
    gated_demo["mode"] = "error"
    _action(window, "Open Demo Project").trigger()
    worker = gated_demo["workers"][0]
    try:
        assert gated_demo["emitted"].wait(5)
        assert _pump_until(app, lambda: gated_demo["critical"].calls)
        assert window._demo_worker is worker
    finally:
        gated_demo["gate"].set()
        worker.wait(5000)
    assert _pump_until(app, lambda: window._demo_worker is None)
    critical = gated_demo["critical"].calls
    assert len(critical) == 1
    assert critical[0][1] == "Demo could not be created"
    assert "generator exploded" in critical[0][2]
    assert gated_demo["loads"].calls == []


def test_close_after_demo_result_waits_for_running_thread(app, window, gated_demo, monkeypatch):
    warning = Recorder()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    _action(window, "Open Demo Project").trigger()
    worker = gated_demo["workers"][0]
    releaser = threading.Timer(0.2, gated_demo["gate"].set)
    try:
        assert gated_demo["emitted"].wait(5)
        assert _pump_until(app, lambda: gated_demo["loads"].calls)
        assert worker.isRunning()
        window._set_dirty(False)
        releaser.start()
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        # An accepted close must not leave the demo QThread running.
        assert not worker.isRunning()
        assert warning.calls == []
    finally:
        releaser.cancel()
        gated_demo["gate"].set()
        worker.wait(5000)
    # Deliver the queued finished -> clear/deleteLater before the window is torn down.
    assert _pump_until(app, lambda: window._demo_worker is None)
