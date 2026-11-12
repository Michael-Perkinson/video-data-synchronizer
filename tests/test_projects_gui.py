"""Offscreen regression tests for project lifecycle guarantees."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow, _ExportWorker
from video_data_sync.settings.presets import validate_preset
from video_data_sync.video_processing.cropper import CropRect
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


def test_main_window_offscreen_smoke(window):
    assert window.video_tab and window.windowTitle().startswith("Video Data Synchronizer")


def test_save_includes_video_before_crop_confirmation(window, synthetic_video):
    window.video_tab._load_video(str(synthetic_video))
    assert window._video_state is None
    document = window._project_document(portable_paths=False)
    assert document["video"]["path"] == str(synthetic_video)
    assert document["video"]["end_s"] == pytest.approx(1.2)
    assert document["video"]["rotation"] == 0


def test_export_snapshot_matches_latest_visible_controls(
    window, synthetic_video, tmp_path, monkeypatch
):
    source = tmp_path / "data.csv"
    source.write_text("Time (s),signal\n0,1\n0.5,2\n1,4\n", encoding="utf-8")
    assert window.data_tab.load_file(str(source), time_unit="Seconds")
    window.data_tab.legend_label_input.setText("Fiber signal")
    window.data_tab.legend_label_input.editingFinished.emit()
    window.video_tab._load_video(str(synthetic_video))
    window.video_tab.btn_rotate.click()
    assert window.animated_tab is not None
    window.animated_tab.set_composition_settings(
        {"max_change_enabled": True, "max_change_percent": 50.0}
    )
    window.video_tab._brightness_slider.setValue(12)
    window.video_tab.end_input.setText("1")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "out.mp4"), "")
    )
    monkeypatch.setattr(_ExportWorker, "start", lambda self: None)
    window._on_export_requested([1, 2])
    expected_max_change = window.animated_tab.max_change
    assert expected_max_change is not None
    jobs = window._export_worker._jobs
    window.video_tab._brightness_slider.setValue(30)
    assert len(jobs) == 2
    for job in jobs:
        project = job["audit_context"]["project"]
        assert project["video"]["colour"]["brightness"] == 12
        assert project["video"]["end_s"] == job["end_s"] == 1
        assert project["video"]["rotation"] == job["rotation"] == 90
        assert job["max_change"] == pytest.approx(expected_max_change)
        assert job["pin_scale_bars_to_axes"] is True
        assert job["legend_labels"]["signal"] == "Fiber signal"
        assert project["data"]["legend_labels"]["signal"] == "Fiber signal"
        assert project["composition"]["advanced"]["max_change_percent"] == 50.0
        assert project["composition"]["advanced"]["max_change_enabled"] is True
        assert project["composition"]["speed"] == f"{job['speed_factor']:g}x"
    assert [job["output_path"] for job in jobs] == [
        str(tmp_path / "out_1x.mp4"),
        str(tmp_path / "out_2x.mp4"),
    ]
    window._clear_export_worker(window._export_worker)
    window._on_export_requested([1])
    assert window._export_worker._jobs[0]["output_path"] == str(tmp_path / "out_1x.mp4")
    window._clear_export_worker(window._export_worker)


def test_restore_history_loads_recorded_settings_as_unsaved_project(
    window, synthetic_video, tmp_path, monkeypatch
):
    source = tmp_path / "data.csv"
    source.write_text("Time (s),clock,signal\n0,0,1\n1,0.01,2\n2,0.02,4\n", encoding="utf-8")
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._restore_export_settings(
        {
            "record_path": str(tmp_path / "run.run.json"),
            "sources": {},
            "context": {
                "project": {
                    "schema_version": 1,
                    "video": {"path": str(synthetic_video), "start_s": 0, "end_s": 1},
                    "data": {
                        "path": str(source),
                        "time_column": "clock",
                        "time_unit": "Minutes",
                        "column": "signal",
                        "transform": "raw",
                    },
                    "composition": {"speed": "2x"},
                }
            },
        }
    )
    assert window._project_path is None and window._dirty
    assert window.data_tab.time_column_combo.currentText() == "clock"
    assert window.data_tab.dataframe["Time (s)"].tolist() == [0, 0.6, 1.2]
    assert window.animated_tab.speed_factor == 2
    window._set_dirty(False)
    window.data_tab.offset_input.setText("0.1")
    assert window._dirty


@pytest.mark.parametrize("accept", [True, False])
def test_manual_time_column_choice_preserves_or_replaces_active_data(
    window, tmp_path, monkeypatch, accept
):
    current = tmp_path / "current.csv"
    current.write_text("Time (s),signal\n0,1\n1,2\n", encoding="utf-8")
    unknown_time = tmp_path / "unknown-time.csv"
    unknown_time.write_text("measurement,signal\n0,4\n0.5,6\n1.1,11\n", encoding="utf-8")
    data = window.data_tab
    assert data.load_file(str(current))
    original = data.dataframe.copy()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(unknown_time), ""))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("measurement", accept))

    data._on_load()

    if accept:
        assert data.data_path == str(unknown_time)
        assert data.time_col == "Time (s)"
        assert data.dataframe["Time (s)"].tolist() == [0.0, 0.5, 1.1]
    else:
        assert data.data_path == str(current)
        assert data.dataframe.equals(original)


@pytest.mark.parametrize("fail_video", [True, False])
def test_project_data_folder_preference_commits_only_after_import(
    window, synthetic_video, tmp_path, monkeypatch, fail_video
):
    from video_data_sync.gui.video_tab import VideoTab

    current_dir = tmp_path / "current-data"
    imported_dir = tmp_path / "project-data"
    current_dir.mkdir()
    imported_dir.mkdir()
    current = current_dir / "current.csv"
    current.write_text("Time (s),signal\n0,1\n1,2\n", encoding="utf-8")
    imported = imported_dir / "imported.csv"
    imported.write_text("Time (s),signal\n0,3\n1,4\n", encoding="utf-8")
    folder_writes: list[str] = []
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", folder_writes.append)
    data = window.data_tab
    assert data.load_file(str(current))
    folder_writes.clear()
    previous_folder = data._settings.value("folders/data")
    if fail_video:
        monkeypatch.setattr(VideoTab, "load_project_video", lambda *args, **kwargs: False)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    document = {
        "schema_version": 1,
        "video": {"path": str(synthetic_video), "start_s": 0, "end_s": 1},
        "data": {
            "path": str(imported),
            "time_column": "Time (s)",
            "time_unit": "Auto-detect",
            "column": "signal",
        },
    }

    loaded = window._load_project_path(
        str(tmp_path / "folder-preference.vdsp.json"),
        remember=False,
        clear_recovery=False,
        document=document,
    )

    assert loaded is not fail_video
    if fail_video:
        assert data._settings.value("folders/data") == previous_folder
        assert folder_writes == []
    else:
        assert window.data_tab._settings.value("folders/data") == str(imported_dir)
        assert folder_writes == [str(imported_dir)]


def test_project_rejects_fractional_crop_coordinates_without_replacing_video(
    window, synthetic_video, tmp_path, monkeypatch
):
    window.video_tab._load_video(str(synthetic_video))
    current_path = window.video_tab._cap.path
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    document = {
        "schema_version": 1,
        "video": {
            "path": str(synthetic_video),
            "start_s": 0,
            "end_s": 1,
            "crop": [0.9, 1, 32, 24],
        },
    }

    assert not window._load_project_path(
        str(tmp_path / "fractional-crop.vdsp.json"),
        remember=False,
        clear_recovery=False,
        document=document,
    )
    assert window.video_tab._cap.path == current_path


def test_project_rejects_unsupported_time_unit_without_replacing_current_data(
    window, tmp_path, monkeypatch
):
    current = tmp_path / "current.csv"
    current.write_text("Time (s),signal\n0,1\n1,2\n", encoding="utf-8")
    unsupported = tmp_path / "milliseconds.csv"
    unsupported.write_text("elapsed_ms,signal\n0,4\n1000,5\n2000,6\n", encoding="utf-8")
    data = window.data_tab
    assert data.load_file(str(current))
    original = data.dataframe.copy()
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    document = {
        "schema_version": 1,
        "data": {
            "path": str(unsupported),
            "time_column": "elapsed_ms",
            "time_unit": "Fortnights",
            "column": "signal",
        },
    }

    assert not window._load_project_path(
        str(tmp_path / "invalid-time-unit.vdsp.json"),
        remember=False,
        clear_recovery=False,
        document=document,
    )
    assert data.data_path == str(current)
    assert data.dataframe.equals(original)


def test_current_appearance_can_be_serialized_as_generic_preset(window):
    document = validate_preset(window._appearance_preset_document())
    encoded = json.dumps(document)
    for recording_specific in ("path", "start_s", "end_s", "crop", "offset_s", "zscore"):
        assert f'"{recording_specific}"' not in encoded


def test_recovery_snapshot_is_atomic_project_json(window, tmp_path, monkeypatch):
    recovery = tmp_path / "recovery.vdsp.json"
    monkeypatch.setattr(MainWindow, "_recovery_path", property(lambda self: recovery))
    window._project_path = str(tmp_path / "original.vdsp.json")
    window._set_dirty(True)
    window._write_recovery_snapshot()
    document = json.loads(recovery.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["recovery"]["project_path"] == window._project_path
    assert document["recovery"]["saved_at"]
    assert not list(tmp_path.glob(".recovery-*.tmp"))
    window._clear_recovery()


@pytest.mark.parametrize("accept", [True, False])
def test_recovery_prompt_restores_project_identity_and_clears_snapshot(
    window, tmp_path, monkeypatch, accept
):
    recovery = tmp_path / "recovery.vdsp.json"
    original_project = str(tmp_path / "study.vdsp.json")
    monkeypatch.setattr(MainWindow, "_recovery_path", property(lambda self: recovery))
    window._project_path = original_project
    window._set_dirty(True)
    window._write_recovery_snapshot()
    window._set_dirty(False)
    answer = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *args: answer)

    window._offer_recovery_if_available()

    assert not recovery.exists()
    assert window._project_path == original_project
    assert window._dirty is accept


def test_recent_projects_are_deduplicated_and_bounded(window, tmp_path):
    previous = window._settings.value("projects/recent")
    try:
        paths = [str(tmp_path / f"project-{index}.vdsp.json") for index in range(10)]
        for path in paths:
            window._remember_recent_project(path)
        window._remember_recent_project(paths[-1])
        recent = window._recent_projects()
        assert len(recent) == 8 and recent[0] == os.path.abspath(paths[-1])
        assert len(recent) == len(set(recent))
    finally:
        if previous is None:
            window._settings.remove("projects/recent")
        else:
            window._settings.setValue("projects/recent", previous)


def test_project_paths_are_relative_to_project(window, tmp_path):
    assert window._portable_path(
        str(tmp_path / "session/inputs/trace.csv"), str(tmp_path / "session/project.vdsp.json")
    ) == os.path.join("inputs", "trace.csv")


def test_save_as_does_not_change_path_when_atomic_write_fails(window, tmp_path, monkeypatch):
    original = str(tmp_path / "old.vdsp.json")
    window._project_path = original
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "new.vdsp.json"), "")
    )
    monkeypatch.setattr(window, "_write_project", lambda path: False)
    assert not window._save_project_as() and window._project_path == original


def test_atomic_write_cleans_temporary_file_on_replace_failure(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    with patch("video_data_sync.gui.main_window.os.replace", side_effect=OSError("nope")):
        assert not window._write_project(str(tmp_path / "project.vdsp.json"))
    assert not list(tmp_path.glob(".vdsp-*.tmp"))


@pytest.mark.parametrize("document", ["{broken", json.dumps({"schema_version": 999})])
def test_invalid_project_leaves_current_identity_untouched(window, tmp_path, monkeypatch, document):
    current = str(tmp_path / "current.vdsp.json")
    other = tmp_path / "other.json"
    other.write_text(document, encoding="utf-8")
    window._project_path = current
    window._set_dirty(True)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    assert not window._load_project_path(str(other))
    assert window._project_path == current and window._dirty


def test_runtime_loader_failure_keeps_current_project_state(window, tmp_path, monkeypatch):
    source = tmp_path / "valid.csv"
    source.write_text("Time (s),signal\n0,1\n1,2\n", encoding="utf-8")
    project = tmp_path / "p.json"
    project.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video": {},
                "data": {"path": source.name, "transform": "subtract_first"},
                "composition": {},
            }
        ),
        encoding="utf-8",
    )
    window._project_path = "old"
    window.data_tab.transform_combo.setCurrentText("Raw values")
    monkeypatch.setattr(type(window.data_tab), "load_file", lambda self, path: False)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    assert not window._load_project_path(str(project))
    assert window._project_path == "old" and window.data_tab.trace_transform == "raw"


def test_new_project_clears_sources_and_reapplies_preferences(window, tmp_path, monkeypatch):
    data = window.data_tab
    data._settings.setValue("data/traceColor", "#123456")
    data._settings.setValue("data/transform", "raw")
    data._settings.sync()
    data._data_path = str(tmp_path / "trace.csv")
    window._video_state = ("video.avi", 10.0, 0.0, 1.0, CropRect(0, 0, 10, 10), None)
    window._video_ready = window._data_ready = True
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._new_project()
    assert (
        window._video_state is None
        and data.data_path is None
        and window.animated_tab.video_path is None
    )
    assert data.trace_color == "#123456" and data.trace_transform == "raw" and not window._dirty


def test_preferred_channel_is_reused_only_when_available(window, tmp_path):
    data = window.data_tab
    key = "data/preferredColumn1"
    previous = data._settings.value(key)
    csv = tmp_path / "channels.csv"
    csv.write_text("Time (s),signal,second\n0,1,2\n1,3,4\n", encoding="utf-8")
    try:
        data._settings.setValue(key, "second")
        data.reset_to_preferences()
        assert data.load_file(str(csv)) and data.selected_column == "second"

        data._settings.setValue(key, "missing")
        data.reset_to_preferences()
        assert data.load_file(str(csv)) and data.selected_column == "signal"
    finally:
        if previous is None:
            data._settings.remove(key)
        else:
            data._settings.setValue(key, previous)


def test_column_colour_mapping_follows_selected_channel(window, tmp_path):
    data = window.data_tab
    key = "data/colorsByColumn"
    previous = data._settings.value(key)
    csv = tmp_path / "mapped-colours.csv"
    csv.write_text("Time (s),signal,second\n0,1,2\n1,3,4\n", encoding="utf-8")
    try:
        data._settings.setValue(key, json.dumps({"signal": "#112233", "second": "#abcdef"}))
        data.reset_to_preferences()
        assert data.load_file(str(csv))
        data.column_combo.setCurrentText("second")
        assert data.trace_color == "#abcdef"
    finally:
        if previous is None:
            data._settings.remove(key)
        else:
            data._settings.setValue(key, previous)


def test_dirty_close_cancel_ignores_event(window, monkeypatch):
    window._set_dirty(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()


def test_project_auto_y_mode_clears_fixed_limits(window):
    data = window.data_tab
    data.radio_fixed.setChecked(True)
    data.y_min_input.setText("-1")
    data.y_max_input.setText("2")
    data.apply_project_style({"y_mode": "auto", "y_limits": [None, None]})
    assert data.radio_auto.isChecked() and data.y_limits() == (None, None)


def test_late_video_failure_is_transactional(window, synthetic_video, tmp_path, monkeypatch):
    source = tmp_path / "valid.csv"
    source.write_text("Time (s),signal\n0,1\n1,2\n", encoding="utf-8")
    project = tmp_path / "late-failure.json"
    project.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "video": {"path": str(synthetic_video), "start_s": 0, "end_s": 1},
                "data": {"path": source.name, "transform": "raw"},
                "composition": {},
            }
        ),
        encoding="utf-8",
    )
    old_tabs = (window.video_tab, window.data_tab, window.animated_tab)
    old_path, old_dirty = window._project_path, window._dirty
    from video_data_sync.gui.video_tab import VideoTab

    monkeypatch.setattr(VideoTab, "load_project_video", lambda *args: False)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    assert not window._load_project_path(str(project))
    assert (window.video_tab, window.data_tab, window.animated_tab) == old_tabs
    assert (window._project_path, window._dirty) == (old_path, old_dirty)


def test_export_worker_cancelled_is_distinct_and_clears_state(window, monkeypatch):
    seen: list[str] = []

    def cancelled_export(**kwargs):
        assert kwargs["cancel_callback"]()
        raise ExportCancelled

    monkeypatch.setattr("video_data_sync.video_processing.exporter.export_video", cancelled_export)
    worker = _ExportWorker({})
    window._export_worker = worker
    worker.cancelled.connect(lambda: seen.append("cancelled"))
    worker.cancelled.connect(lambda: window._clear_export_worker(worker))
    worker.error.connect(lambda _: seen.append("failed"))
    worker.succeeded.connect(lambda: seen.append("succeeded"))
    worker.request_cancel()
    worker.run()
    assert seen == ["cancelled"] and window._export_worker is None


def test_export_worker_runs_batch_sequentially(monkeypatch):
    calls = []

    def export(**kwargs):
        calls.append(kwargs["output_path"])
        kwargs["progress_callback"](1, 1)

    monkeypatch.setattr("video_data_sync.video_processing.exporter.export_video", export)
    worker = _ExportWorker(
        [
            {"output_path": "first.mp4", "_estimated_frames": 1},
            {"output_path": "second.mp4", "_estimated_frames": 1},
        ]
    )
    progress = []
    worker.progress.connect(lambda current, total: progress.append((current, total)))
    worker.run()
    assert calls == ["first.mp4", "second.mp4"]
    assert progress == [(1, 2), (2, 2)]


def test_repeated_export_request_is_ignored(window, monkeypatch):
    window._export_worker = object()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: pytest.fail("dialog opened"))
    window._on_export_requested()
    assert "already running" in window.statusBar().currentMessage()
    window._export_worker = None


def test_close_waits_for_export_worker_without_force_kill(window, monkeypatch):
    class Worker:
        cancel_requested = False

        def isRunning(self):
            return True

        def request_cancel(self):
            self.cancel_requested = True

        def wait(self, timeout):
            return False

    worker = Worker()
    window._export_worker = worker
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    event = QCloseEvent()
    window.closeEvent(event)
    assert worker.cancel_requested and not event.isAccepted()


@pytest.mark.parametrize(
    ("saved_split_fraction", "restores_saved_split"), [(0.6, True), (0.85, False)]
)
def test_complete_project_round_trip_preserves_preferences(
    window, synthetic_video, tmp_path, monkeypatch, saved_split_fraction, restores_saved_split
):
    csv = tmp_path / "trace.csv"
    csv.write_text(
        "Time (s),signal,second\n0,1,4\n.25,2,3\n.5,4,2\n.75,8,1\n1,16,0\n", encoding="utf-8"
    )
    data = window.data_tab
    data._settings.setValue("data/traceColor", "#aabbcc")
    data._settings.setValue("data/transform", "raw")
    data._settings.setValue("data/timeUnit", "Auto-detect")
    data._settings.sync()
    data.time_unit_combo.setCurrentText("Auto-detect")
    assert data.load_file(str(csv))
    data.column_combo.setCurrentText("signal")
    data.z_start_input.setText("0")
    data.z_end_input.setText(".75")
    data._on_zscore()
    zcol = data.selected_column
    data.chk_overlay.setChecked(True)
    data.legend_label_input.setText("Normalized signal")
    data.legend_label_input.editingFinished.emit()
    data.legend_label_input2.setText("Control signal")
    data.legend_label_input2.editingFinished.emit()
    data.radio_fixed.setChecked(True)
    data.y_min_input.setText("-2.5")
    data.y_max_input.setText("3.5")
    data.transform_combo.setCurrentText("Subtract clip-start value")
    assert window.video_tab.load_project_video(
        str(synthetic_video),
        0,
        1,
        [1, 2, 46, 60],
        {
            "brightness": 7,
            "contrast": 1.2,
            "gamma": 0.8,
            "r_gain": 1.1,
            "g_gain": 0.9,
            "b_gain": 1.3,
        },
        rotation=90,
    )
    anim = window.animated_tab
    anim.layout_combo.setCurrentIndex(1)
    window.resize(1280, 900)
    window.show()
    window.tabs.setCurrentWidget(anim)
    QApplication.processEvents()
    split_total = sum(anim.display_container.sizes())
    anim.display_container.setSizes(
        [
            round(split_total * saved_split_fraction),
            round(split_total * (1 - saved_split_fraction)),
        ]
    )
    anim.speed_combo.setCurrentText("2x")
    anim.chk_deflicker.setChecked(True)
    advanced = anim.composition_settings
    advanced.update(
        {
            "anchor": "Top left",
            "background": "#102030",
            "scale_font_size": 19,
            "max_change_enabled": True,
            "max_change_percent": 50.0,
        }
    )
    anim.set_composition_settings(advanced)
    expected_max_change = anim.max_change
    assert expected_max_change is not None
    project = tmp_path / "roundtrip.vdsp.json"
    assert window._write_project(str(project))
    # Opening a project must not replace the user's reusable transform default.
    data._settings.setValue("data/transform", "raw")
    data._settings.sync()
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    restored = MainWindow()
    restored._preload_done = True
    restored._ensure_data_tab()
    restored._ensure_animated_tab()
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    try:
        assert restored._load_project_path(str(project))
        restored.resize(1280, 900)
        restored.show()
        restored.tabs.setCurrentWidget(restored.video_tab)
        QApplication.processEvents()
        video_surface = restored.video_tab.video_display
        pixmap = video_surface.pixmap()
        assert pixmap is not None and not pixmap.isNull()
        assert (
            max(
                pixmap.width() / video_surface.width(),
                pixmap.height() / video_surface.height(),
            )
            >= 0.9
        )
        assert restored.data_tab is not None and restored.animated_tab is not None
        rd = restored.data_tab
        restored_anim = restored.animated_tab
        restored.tabs.setCurrentWidget(restored_anim)
        QApplication.processEvents()
        restored_split = restored_anim.display_container.sizes()
        restored_fraction = restored_split[0] / sum(restored_split)
        if restores_saved_split:
            assert restored_fraction == pytest.approx(saved_split_fraction, abs=0.02)
        else:
            assert 0.25 <= restored_fraction <= 0.68
        assert (
            restored.video_tab._brightness() == 7
            and restored.video_tab._r_gain() == pytest.approx(1.1)
            and restored.video_tab.rotation == 90
            and restored_anim.rotation == 90
        )
        assert (
            rd.y_limits() == (-2.5, 3.5)
            and rd.trace_transform == "subtract_first"
            and zcol in rd.dataframe.columns
        )
        assert rd.legend_labels[zcol] == "Normalized signal"
        assert rd.legend_labels["second"] == "Control signal"
        full_data_legend = rd.plot.ax.get_legend()
        preview_legend = restored_anim.anim_plot.ax.get_legend()
        assert full_data_legend is not None
        assert preview_legend is not None
        assert [text.get_text() for text in full_data_legend.get_texts()] == [
            "Normalized signal",
            "Control signal",
        ]
        assert [text.get_text() for text in preview_legend.get_texts()] == [
            "Normalized signal",
            "Control signal",
        ]
        assert (
            restored_anim.layout_combo.currentIndex() == 0
            and restored_anim.speed_combo.currentText() == "2x"
            and restored_anim.deflicker_enabled
        )
        assert restored_anim.composition_settings["anchor"] == "Top left"
        assert restored_anim.composition_settings["max_change_percent"] == 50.0
        assert restored_anim.max_change == pytest.approx(expected_max_change)
        assert (
            rd._settings.value("data/traceColor") == "#aabbcc"
            and rd._settings.value("data/transform") == "raw"
        )
    finally:
        restored._set_dirty(False)
        dispose_widget(restored)
