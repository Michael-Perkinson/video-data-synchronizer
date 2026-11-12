"""Offscreen P0 GUI interaction tests: export destination, batch dialog, progress
Cancel and Export History.

What is real here:

* Buttons are pressed with ``QTest.mouseClick`` on a shown window, list rows and
  dialogs receive ``QTest`` key events, and menu entries are dispatched through
  their real ``QAction``.
* The batch "Export Speeds" dialog and the Export History dialog are the real
  application ``QDialog`` objects running their real modal ``exec()`` loop. A
  ``ModalDriver`` timer interacts with them while they are modal.
* Progress cancellation drives the real ``_ExportWorker`` QThread and the real
  exporter; only a bounded gate is wrapped around its progress callback.

What is mocked (and therefore NOT native evidence):

* ``QFileDialog`` static methods (the native Windows shell save dialog,
  including its own overwrite prompt), ``QMessageBox`` static methods, and
  ``QDesktopServices.openUrl`` (so no Explorer window is ever launched). Their
  arguments are recorded and asserted instead.

Exceptions raised inside Qt slots are printed by PySide instead of failing the
test, so dialog steps record their failures in ``ModalDriver.errors`` and the
test asserts that list after ``exec()`` returns.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
)
from qt_teardown import dispose_widget

from video_data_sync import export_history
from video_data_sync.export_history import load_records
from video_data_sync.gui.history_dialog import ExportHistoryDialog
from video_data_sync.gui.main_window import MainWindow, _ExportWorker, _with_mp4_extension
from video_data_sync.video_processing import exporter

LEFT = Qt.MouseButton.LeftButton
RANGE_MESSAGE = "Choose at least one speed from 0.05x to 16x."
NUMBER_MESSAGE = "Custom speeds must be numbers."


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_gui_paths(tmp_path, monkeypatch):
    """Extra isolation that conftest does not provide for these GUI paths.

    ``history_dialog`` imported ``history_directory`` by name, so conftest's
    patch of ``export_history.history_directory`` does not reach the
    "Open history folder" button. The data tab's folder memory would otherwise
    write the user's ``~/.video_data_sync/settings.json``.
    """
    monkeypatch.setattr(
        "video_data_sync.gui.history_dialog.history_directory",
        lambda: tmp_path / "export-history",
    )
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", lambda _path: None)
    monkeypatch.setattr("video_data_sync.gui.data_tab.read_folder_path", lambda: str(tmp_path))


@pytest.fixture
def window(app, monkeypatch):
    # Class-level patches: monkeypatch undo runs after the window is deleted.
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_recovery_if_available", lambda self: None)
    win = MainWindow()
    win._preload_done = True
    win._ensure_data_tab()
    win._ensure_animated_tab()
    yield win
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


def _pump_until(app, predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def _action(window: MainWindow, label: str) -> QAction:
    for action in window.findChildren(QAction):
        if action.text().replace("&", "") == label:
            return action
    raise AssertionError(f"No menu action labelled {label!r}")


class ModalDriver:
    """Run one step each time a matching modal dialog is visible.

    A repeating timer fires inside the dialog's own nested ``exec()`` loop.
    Each step must finish by closing the dialog (OK, Cancel or Escape), so a
    re-shown dialog (the batch dialog's retry loop) gets the next step. On a
    step failure or at the fail-safe deadline, every visible dialog owned by
    ``owner`` is rejected so the suite cannot hang on a modal loop.
    """

    def __init__(self, owner, find, steps, timeout_s: float = 20.0) -> None:
        self._owner = owner
        self._find = find
        self.steps = list(steps)
        self.errors: list[str] = []
        self.handled = 0
        self._timeout_s = timeout_s
        self._deadline = 0.0
        self._timer = QTimer()
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._tick)

    def __enter__(self) -> ModalDriver:
        self._deadline = time.monotonic() + self._timeout_s
        self._timer.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._timer.stop()

    def _reject_visible(self) -> None:
        for dialog in self._owner.findChildren(QDialog):
            if dialog.isVisible():
                dialog.reject()

    def _tick(self) -> None:
        try:
            if time.monotonic() > self._deadline:
                self.errors.append(f"timeout with {len(self.steps)} step(s) left")
                self._timer.stop()
                self._reject_visible()
                return
            dialog = self._find()
            if dialog is None:
                return
            if not self.steps:
                self.errors.append("dialog shown again with no step left")
                self._reject_visible()
                return
            step = self.steps.pop(0)
            self.handled += 1
            step(dialog)
        except Exception:
            self.errors.append(traceback.format_exc())
            self._reject_visible()


def _visible_dialog(owner, cls=QDialog, title: str | None = None):
    # Search QDialog and filter with isinstance so Python subclasses match too.
    # Dialogs are never deleted after exec(), so hidden ones accumulate.
    for dialog in owner.findChildren(QDialog):
        if not isinstance(dialog, cls) or not dialog.isVisible():
            continue
        if title is None or dialog.windowTitle() == title:
            return dialog
    return None


def _write_csv(tmp_path: Path) -> Path:
    source = tmp_path / "data.csv"
    source.write_text("Time (s),signal\n0,1\n0.5,2\n1,4\n", encoding="utf-8")
    return source


def _make_export_ready(app, window: MainWindow, synthetic_video, tmp_path) -> None:
    """Load synthetic inputs, click Apply Video Settings, show the Animated tab."""
    assert window.data_tab is not None
    assert window.data_tab.load_file(
        str(_write_csv(tmp_path)), time_unit="Seconds", remember_folder=False
    )
    window.video_tab._load_video(str(synthetic_video))
    window.video_tab.end_input.setText("1")
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000)
    QTest.mouseClick(window.video_tab.btn_confirm, LEFT)
    assert window.tabs.isTabEnabled(2), "Apply Video Settings click was not handled"
    window.tabs.setCurrentIndex(2)
    app.processEvents()
    anim = window.animated_tab
    assert anim is not None
    # A click on a hidden or disabled button would silently do nothing and let
    # the negative assertions below pass for the wrong reason.
    for button in (anim.btn_export, anim.btn_export_multiple):
        assert button.isVisible() and button.isEnabled()


@pytest.fixture
def export_spy(monkeypatch):
    """Records any real export attempt instead of encoding."""
    spy = Recorder()
    monkeypatch.setattr(exporter, "export_video", spy)
    return spy


def _assert_export_idle(window: MainWindow) -> None:
    anim = window.animated_tab
    assert anim is not None
    assert window._export_worker is None
    assert anim.btn_export.isEnabled()
    assert anim.btn_export_multiple.isEnabled()


# ----------------------------------------------------------------------
# Single export: destination cancellation
# ----------------------------------------------------------------------


def test_export_button_destination_cancel_starts_nothing_and_keeps_file(
    app, window, synthetic_video, tmp_path, monkeypatch, export_spy
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    existing = tmp_path / "synthetic_sync.mp4"
    existing.write_bytes(b"previous export")
    save_dialog = Recorder(("", ""))  # mocked native Save Video dialog: Cancel
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    warning = Recorder()
    monkeypatch.setattr(QMessageBox, "warning", warning)

    QTest.mouseClick(window.animated_tab.btn_export, LEFT)

    assert len(save_dialog.calls) == 1, "Export Video click was not dispatched"
    assert save_dialog.calls[0][1] == "Save Video"
    assert save_dialog.calls[0][2].endswith("synthetic_sync.mp4")
    assert warning.calls == []
    assert export_spy.calls == []
    assert existing.read_bytes() == b"previous export"
    assert not window._settings.contains("folders/export")
    _assert_export_idle(window)


# ----------------------------------------------------------------------
# Batch export: the real "Export Speeds" dialog
# ----------------------------------------------------------------------


def _speed_dialog_finder(window):
    return lambda: _visible_dialog(window, QDialog, "Export Speeds")


def _speed_boxes(dialog) -> dict[str, QCheckBox]:
    return {box.text(): box for box in dialog.findChildren(QCheckBox)}


def _set_checked(box: QCheckBox, wanted: bool) -> None:
    if box.isChecked() != wanted:
        box.click()  # real QAbstractButton click: emits toggled/clicked
    assert box.isChecked() is wanted


def _uncheck_all(dialog) -> None:
    for box in _speed_boxes(dialog).values():
        _set_checked(box, False)


def _type_custom(dialog, text: str) -> None:
    field = dialog.findChild(QLineEdit)
    field.clear()
    if text:
        QTest.keyClicks(field, text)
    assert field.text() == text


def _summary(dialog) -> str:
    for label in dialog.findChildren(QLabel):
        text = label.text()
        if (
            text.startswith("No speeds selected")
            or text.startswith("Enter custom speeds")
            or "\nNames: " in text
        ):
            return text
    raise AssertionError("batch summary label not found")


def _click_box_button(dialog, which) -> None:
    box = dialog.findChild(QDialogButtonBox)
    QTest.mouseClick(box.button(which), LEFT)


def _ok(dialog) -> None:
    _click_box_button(dialog, QDialogButtonBox.StandardButton.Ok)


def _cancel(dialog) -> None:
    _click_box_button(dialog, QDialogButtonBox.StandardButton.Cancel)


def _run_batch_click(window, steps) -> ModalDriver:
    with ModalDriver(window, _speed_dialog_finder(window), steps) as driver:
        QTest.mouseClick(window.animated_tab.btn_export_multiple, LEFT)
    assert driver.errors == [], "\n".join(driver.errors)
    assert driver.steps == [], "batch dialog closed before every step ran"
    return driver


def test_batch_dialog_cancel_starts_nothing_and_saves_no_speeds(
    app, window, synthetic_video, tmp_path, monkeypatch, export_spy
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    save_dialog = Recorder(("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    seen: dict = {}

    def inspect_then_cancel(dialog):
        seen["checked"] = {text for text, box in _speed_boxes(dialog).items() if box.isChecked()}
        seen["summary"] = _summary(dialog)
        _cancel(dialog)

    driver = _run_batch_click(window, [inspect_then_cancel])

    assert driver.handled == 1
    # Fresh settings default to 0.5x, 1x and 2x.
    assert seen["checked"] == {"0.5x", "1x", "2x"}
    assert seen["summary"].startswith("Export 3 videos at: 0.5x, 1x, 2x")
    assert save_dialog.calls == []
    assert export_spy.calls == []
    assert not window._settings.contains("export/speeds")
    _assert_export_idle(window)


@pytest.mark.parametrize(
    "custom, message, summary_prefix",
    [
        ("", RANGE_MESSAGE, "No speeds selected."),
        ("abc", NUMBER_MESSAGE, "Enter custom speeds as numbers"),
        ("1, fast", NUMBER_MESSAGE, "Enter custom speeds as numbers"),
        ("nan", RANGE_MESSAGE, None),
        ("inf", RANGE_MESSAGE, None),
        ("0.049", RANGE_MESSAGE, None),
        ("16.0001", RANGE_MESSAGE, None),
        ("0", RANGE_MESSAGE, None),
        ("-1", RANGE_MESSAGE, None),
    ],
)
def test_batch_dialog_rejects_invalid_input_and_keeps_dialog_open(
    app,
    window,
    synthetic_video,
    tmp_path,
    monkeypatch,
    export_spy,
    custom,
    message,
    summary_prefix,
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    save_dialog = Recorder(("", ""))
    warning = Recorder()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    seen: dict = {}

    def enter_invalid(dialog):
        seen["dialog"] = dialog
        _uncheck_all(dialog)
        _type_custom(dialog, custom)
        seen["summary"] = _summary(dialog)
        _ok(dialog)

    def retry_then_cancel(dialog):
        # The same dialog is shown again with the user's input intact.
        seen["same_dialog"] = dialog is seen["dialog"]
        seen["retained"] = dialog.findChild(QLineEdit).text()
        _cancel(dialog)

    driver = _run_batch_click(window, [enter_invalid, retry_then_cancel])

    assert driver.handled == 2
    assert [call[1:] for call in warning.calls] == [("Invalid speed", message)]
    if summary_prefix is not None:
        assert seen["summary"].startswith(summary_prefix)
    assert seen["same_dialog"] is True
    assert seen["retained"] == custom
    assert save_dialog.calls == []
    assert export_spy.calls == []
    assert not window._settings.contains("export/speeds")
    _assert_export_idle(window)


def test_batch_dialog_accepts_boundaries_dedupes_and_persists_on_accept_only(
    app, window, synthetic_video, tmp_path, monkeypatch, export_spy
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    save_dialog = Recorder(("", ""))  # base filename dialog then cancelled
    warning = Recorder()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    seen: dict = {}

    def choose_boundaries(dialog):
        _uncheck_all(dialog)
        _set_checked(_speed_boxes(dialog)["1x"], True)
        _type_custom(dialog, "0.05, 16x, 1, 2X")
        seen["summary"] = _summary(dialog)
        _ok(dialog)

    _run_batch_click(window, [choose_boundaries])

    assert warning.calls == []
    assert seen["summary"] == (
        "Export 4 videos at: 1x, 0.05x, 16x, 2x\n"
        "Names: <base>_1x.mp4, <base>_0.05x.mp4, <base>_16x.mp4, <base>_2x.mp4"
    )
    assert len(save_dialog.calls) == 1
    assert save_dialog.calls[0][1] == "Choose Base Filename"
    # Accepted speeds are remembered even though the destination was cancelled.
    assert list(window._settings.value("export/speeds")) == ["1", "0.05", "16", "2"]
    assert export_spy.calls == []
    _assert_export_idle(window)


def test_batch_overwrite_decline_preserves_existing_bytes(
    app, window, synthetic_video, tmp_path, monkeypatch, export_spy
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    existing = tmp_path / "base_1x.mp4"
    existing.write_bytes(b"keep these bytes")
    save_dialog = Recorder((str(tmp_path / "base.mp4"), ""))
    question = Recorder(QMessageBox.StandardButton.No)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "question", question)

    _run_batch_click(window, [_ok])  # default 0.5x, 1x, 2x

    assert len(save_dialog.calls) == 1
    assert len(question.calls) == 1
    assert question.calls[0][1] == "Replace existing exports?"
    assert "1 generated output file(s) already exist" in question.calls[0][2]
    assert question.calls[0][4] == QMessageBox.StandardButton.No
    assert existing.read_bytes() == b"keep these bytes"
    assert not (tmp_path / "base_0.5x.mp4").exists()
    assert not (tmp_path / "base_2x.mp4").exists()
    assert export_spy.calls == []
    _assert_export_idle(window)


def test_appended_extension_overwrite_decline_keeps_file_and_starts_nothing(
    app, window, synthetic_video, tmp_path, monkeypatch, export_spy
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    existing = tmp_path / "clip.mp4"
    existing.write_bytes(b"keep these bytes")
    # The dialog confirmed "clip" only; the app appends ".mp4" and must ask again.
    save_dialog = Recorder((str(tmp_path / "clip"), ""))
    question = Recorder(QMessageBox.StandardButton.No)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "question", question)

    QTest.mouseClick(window.animated_tab.btn_export, LEFT)

    assert len(save_dialog.calls) == 1
    assert len(question.calls) == 1
    assert question.calls[0][1] == "Replace existing export?"
    assert "clip.mp4 already exists" in question.calls[0][2]
    assert question.calls[0][4] == QMessageBox.StandardButton.No
    assert existing.read_bytes() == b"keep these bytes"
    assert export_spy.calls == []
    assert not window._settings.contains("folders/export")
    _assert_export_idle(window)


def test_appended_extension_overwrite_accept_exports_to_appended_path(
    app, window, synthetic_video, tmp_path, monkeypatch
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    existing = tmp_path / "clip.mp4"
    existing.write_bytes(b"previous export")
    save_dialog = Recorder((str(tmp_path / "clip"), ""))
    question = Recorder(QMessageBox.StandardButton.Yes)
    notices = Recorder()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_dialog)
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "information", notices)
    monkeypatch.setattr(QMessageBox, "critical", notices)
    output_paths: list[str] = []

    def fake_export(**job):
        output_paths.append(job["output_path"])
        job["progress_callback"](1, 1)

    monkeypatch.setattr(exporter, "export_video", fake_export)

    QTest.mouseClick(window.animated_tab.btn_export, LEFT)

    assert _pump_until(app, lambda: window._export_worker is None and bool(output_paths))
    assert len(question.calls) == 1
    assert question.calls[0][1] == "Replace existing export?"
    assert output_paths == [str(tmp_path / "clip.mp4")]
    expected_folder = os.path.dirname(os.path.abspath(str(tmp_path / "clip.mp4")))
    assert window._settings.value("folders/export") == expected_folder
    _assert_export_idle(window)


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ("clip.MP4", "clip.MP4"),
        ("clip.Mp4", "clip.Mp4"),
        ("clip.mp4", "clip.mp4"),
        ("clip", "clip.mp4"),
        ("clip.mov", "clip.mov.mp4"),
    ],
)
def test_mp4_extension_check_is_case_insensitive(chosen, expected):
    assert _with_mp4_extension(chosen) == expected


# ----------------------------------------------------------------------
# Progress dialog: real Cancel button on a real export thread
# ----------------------------------------------------------------------


def test_progress_cancel_button_stops_real_export_and_preserves_destination(
    app, window, synthetic_video, tmp_path, monkeypatch
):
    _make_export_ready(app, window, synthetic_video, tmp_path)
    destination = tmp_path / "out.mp4"
    destination.write_bytes(b"previous export")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", Recorder((str(destination), "")))
    notices = Recorder()
    monkeypatch.setattr(QMessageBox, "information", notices)
    monkeypatch.setattr(QMessageBox, "critical", notices)

    real_export = exporter.export_video
    first_frame = threading.Event()
    cancel_seen = threading.Event()
    terminal = threading.Event()
    release = threading.Event()

    def gated_export(**job):
        report = job.pop("progress_callback")
        cancelled = job["cancel_callback"]

        def progress(current, total):
            report(current, total)
            if not first_frame.is_set():
                first_frame.set()
                # Bounded gate: hold the real encoder on its first frame until
                # the GUI Cancel button has requested cancellation.
                deadline = time.monotonic() + 20
                while not cancelled() and time.monotonic() < deadline:
                    time.sleep(0.005)
                if cancelled():
                    cancel_seen.set()

        return real_export(**job, progress_callback=progress)

    original_run = _ExportWorker.run

    def gated_run(self):
        original_run(self)
        # Terminal signal emitted; keep run() open so the busy state can be checked.
        terminal.set()
        release.wait(20)

    monkeypatch.setattr(exporter, "export_video", gated_export)
    monkeypatch.setattr(_ExportWorker, "run", gated_run)

    QTest.mouseClick(window.animated_tab.btn_export, LEFT)
    worker = window._export_worker
    assert isinstance(worker, _ExportWorker), "Export Video click did not start a worker"
    try:
        assert _pump_until(app, first_frame.is_set, 30), "encoder never reached a frame"
        assert _pump_until(
            app, lambda: _visible_dialog(window, QProgressDialog, "Export") is not None
        ), "progress dialog never became visible"
        progress = _visible_dialog(window, QProgressDialog, "Export")
        assert isinstance(progress, QProgressDialog)
        buttons = [b for b in progress.findChildren(QPushButton) if b.text() == "Cancel"]
        assert len(buttons) == 1
        assert not window.animated_tab.btn_export.isEnabled()

        QTest.mouseClick(buttons[0], LEFT)

        # QProgressDialog.cancel() hides itself; the label proves our slot ran.
        assert progress.labelText() == "Cancelling safely..."
        assert worker._cancel_requested.is_set()
        assert _pump_until(app, terminal.is_set, 30), "worker never finished cancelling"
        assert cancel_seen.is_set()
        assert _pump_until(
            app, lambda: "Export cancelled safely." in window.statusBar().currentMessage()
        )
        # Terminal notification handled but run() has not returned yet.
        assert worker.isRunning()
        assert window._export_worker is worker
        assert not window.animated_tab.btn_export.isEnabled()
        assert not window.animated_tab.btn_export_multiple.isEnabled()
    finally:
        release.set()
        assert worker.wait(30000)

    assert _pump_until(app, lambda: window._export_worker is None)
    _assert_export_idle(window)
    assert notices.calls == []
    assert destination.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".out.mp4.*.mp4"))
    assert load_records()[0]["status"] == "cancelled"


# ----------------------------------------------------------------------
# Export History: search, selection, refresh, folders, restore decline
# ----------------------------------------------------------------------


def _write_history_record(
    *,
    output_dir: Path,
    name: str,
    project_name: str,
    started_at: str,
    video: Path,
    with_project: bool = True,
) -> dict:
    """Write one schema-valid synthetic record through the real atomic writer."""
    directory = export_history.history_directory()
    run_id = uuid4().hex
    stat = video.stat()
    context: dict = {"project_name": project_name}
    if with_project:
        context["project"] = {"schema_version": 1, "video": {}, "data": {}, "composition": {}}
    record = {
        "schema_version": 1,
        "kind": "video-data-sync-export-run",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": started_at,
        "status": "succeeded",
        "output_path": str(output_dir / name),
        "record_path": str(directory / f"{run_id}.run.json"),
        "settings": {"speed_factor": 1.0, "start_s": 0, "end_s": 1},
        "context": context,
        "sources": {
            "video": {
                "path": str(video),
                "sha256": "0" * 64,
                "size_bytes": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            }
        },
        "resolved": {},
    }
    export_history.write_record(directory / f"{run_id}.run.json", record)
    return record


def _history_finder(window):
    return lambda: _visible_dialog(window, ExportHistoryDialog)


def _button(dialog, text: str) -> QPushButton:
    matches = [b for b in dialog.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, text
    return matches[0]


def _same_path(url_or_path, expected: Path) -> bool:
    value = url_or_path.toLocalFile() if hasattr(url_or_path, "toLocalFile") else url_or_path
    return os.path.normcase(os.path.abspath(value)) == os.path.normcase(
        os.path.abspath(str(expected))
    )


def test_history_search_selection_refresh_and_folder_urls(
    app, window, synthetic_video, tmp_path, monkeypatch
):
    out_alpha = tmp_path / "out-alpha"
    out_alpha.mkdir()
    out_beta = tmp_path / "out-beta"  # deliberately missing on disk
    _write_history_record(
        output_dir=out_alpha,
        name="alpha_sync.mp4",
        project_name="Experiment Alpha",
        started_at="2026-01-02T00:00:00+00:00",
        video=synthetic_video,
    )
    _write_history_record(
        output_dir=out_beta,
        name="beta_sync.mp4",
        project_name="Experiment Beta",
        started_at="2026-01-01T00:00:00+00:00",
        video=synthetic_video,
        with_project=False,
    )
    # Stubbed: never launch Explorer; record the URL that would be opened.
    open_url = Recorder(True)
    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    window._set_dirty(False)
    seen: dict = {}

    def interact(dialog):
        runs, details = dialog.runs, dialog.details
        assert runs.count() == 2
        assert runs.currentRow() == 0
        assert "Experiment Alpha" in details.toPlainText()
        assert dialog.output_button.isEnabled() and dialog.restore_button.isEnabled()

        # Keyboard selection: the record without a project cannot be restored.
        QTest.keyClick(runs, Qt.Key.Key_Down)
        assert runs.currentRow() == 1
        assert "Experiment Beta" in details.toPlainText()
        assert dialog.output_button.isEnabled()
        assert not dialog.restore_button.isEnabled()
        # Current behaviour: a missing output folder is passed to the OS as-is;
        # the dialog shows no warning of its own.
        QTest.mouseClick(dialog.output_button, LEFT)
        assert dialog.isVisible()

        QTest.keyClicks(dialog.search, "alpha")
        assert runs.count() == 1
        assert "Experiment Alpha" in details.toPlainText()

        _write_history_record(
            output_dir=out_alpha,
            name="alpha_two_sync.mp4",
            project_name="Experiment Alpha Two",
            started_at="2026-01-03T00:00:00+00:00",
            video=synthetic_video,
        )
        assert runs.count() == 1  # not visible until Refresh
        QTest.mouseClick(_button(dialog, "Refresh"), LEFT)
        assert dialog.search.text() == "alpha"  # filter survives refresh
        assert runs.count() == 2
        assert runs.currentRow() == 0
        assert "Experiment Alpha Two" in details.toPlainText()
        QTest.mouseClick(dialog.output_button, LEFT)
        QTest.mouseClick(_button(dialog, "Open history folder"), LEFT)

        dialog.search.clear()
        QTest.keyClicks(dialog.search, "no-such-run")
        assert runs.count() == 0
        assert details.toPlainText() == "No exports found."
        assert not dialog.output_button.isEnabled()
        assert not dialog.restore_button.isEnabled()
        seen["closed_by_escape"] = True
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    with ModalDriver(window, _history_finder(window), [interact]) as driver:
        _action(window, "Export History…").trigger()
    assert driver.errors == [], "\n".join(driver.errors)
    assert seen.get("closed_by_escape")
    assert _visible_dialog(window, ExportHistoryDialog) is None

    urls = [call[0] for call in open_url.calls]
    assert len(urls) == 3
    assert all(url.isLocalFile() and url.scheme() == "file" for url in urls)
    assert _same_path(urls[0], out_beta)  # folder, not the video file
    assert _same_path(urls[1], out_alpha)
    assert _same_path(urls[2], tmp_path / "export-history")
    assert not window._dirty


@pytest.mark.parametrize("decline_at", ["unsaved-changes", "changed-sources"])
def test_history_restore_decline_keeps_dialog_and_current_project(
    app, window, synthetic_video, tmp_path, monkeypatch, decline_at
):
    window.video_tab._load_video(str(synthetic_video))
    video_tab, cap = window.video_tab, window.video_tab._cap
    current = str(tmp_path / "current.vdsp.json")
    window._project_path = current
    window._set_dirty(True)
    source = tmp_path / "recorded_source.mp4"
    shutil.copyfile(synthetic_video, source)
    _write_history_record(
        output_dir=tmp_path,
        name="recorded_sync.mp4",
        project_name="Recorded",
        started_at="2026-01-02T00:00:00+00:00",
        video=source,
    )
    if decline_at == "unsaved-changes":
        question = Recorder(QMessageBox.StandardButton.Cancel)
    else:
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        question = Recorder(QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.No)
    load = Recorder(True)
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(MainWindow, "_load_project_path", load)
    seen: dict = {}

    def restore_then_escape(dialog):
        assert dialog.runs.count() == 1
        assert dialog.restore_button.isEnabled()
        QTest.mouseClick(dialog.restore_button, LEFT)
        # Declined restore must not accept (close) the history dialog.
        seen["still_open"] = dialog.isVisible()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    with ModalDriver(window, _history_finder(window), [restore_then_escape]) as driver:
        _action(window, "Export History…").trigger()
    assert driver.errors == [], "\n".join(driver.errors)

    assert seen["still_open"] is True
    titles = [call[1] for call in question.calls]
    if decline_at == "unsaved-changes":
        assert titles == ["Unsaved changes"]
    else:
        assert titles == ["Unsaved changes", "Source files have changed"]
        assert "recorded_source.mp4" in question.calls[1][2]
    assert load.calls == []
    assert window._project_path == current
    assert window._dirty
    assert window.video_tab is video_tab and window.video_tab._cap is cap
    assert cap._cap.isOpened()
