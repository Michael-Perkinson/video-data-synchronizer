"""System-level media, GUI, and fault-injection checks using only synthetic inputs."""

import errno
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync import export_history
from video_data_sync.export_history import load_records
from video_data_sync.gui.history_dialog import ExportHistoryDialog
from video_data_sync.gui.main_window import MainWindow
from video_data_sync.plotting.composition import composition_geometry
from video_data_sync.video_processing import exporter
from video_data_sync.video_processing.colour import apply_lut, build_lut


class ExportArgs(TypedDict):
    output_path: str
    video_path: str
    fps: float
    start_s: float
    end_s: float
    crop: tuple[int, int, int, int] | None
    colour_lut: np.ndarray | None
    speed_factor: float
    df: pd.DataFrame
    time_col: str
    data_col: str
    offset_s: float
    color: str
    y_min: float | None
    y_max: float | None
    orientation: str
    progress_callback: Callable[[int, int], None] | None
    cancel_callback: Callable[[], bool] | None


def args_for(
    source: Path,
    output: Path,
    *,
    fps: float = 10.0,
    start_s: float = 0.0,
    end_s: float = 1.0,
    crop: tuple[int, int, int, int] | None = None,
    colour_lut: np.ndarray | None = None,
    speed_factor: float = 1.0,
    df: pd.DataFrame | None = None,
    offset_s: float = 0.0,
    orientation: str = "side_by_side",
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ExportArgs:
    return ExportArgs(
        output_path=str(output),
        video_path=str(source),
        fps=fps,
        start_s=start_s,
        end_s=end_s,
        crop=crop,
        colour_lut=colour_lut,
        speed_factor=speed_factor,
        df=df if df is not None else pd.DataFrame({"t": np.arange(21) / 10, "v": np.arange(21)}),
        time_col="t",
        data_col="v",
        offset_s=offset_s,
        color="blue",
        y_min=None,
        y_max=None,
        orientation=orientation,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )


def decode(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        cap.release()
    return frames


@pytest.mark.parametrize("orientation", ["side_by_side", "stacked"])
@pytest.mark.parametrize("speed,indices", [(0.5, [2, 2, 3, 3, 4, 4]), (1, [2, 3, 4]), (2, [2, 4])])
def test_decoded_frames_crop_colour_speed_and_trace_timing(
    synthetic_video, tmp_path, monkeypatch, orientation, speed, indices
):
    source_frames = decode(synthetic_video)
    lut = build_lut(brightness=20, r_gain=0.7, b_gain=1.2)
    assert lut is not None
    output = tmp_path / "timing.mp4"
    trace_times = []
    render = exporter._render_plot_frame

    def capture_trace(canvas, lines, line_data, h, w):
        trace_times.append(float(line_data[0][0][-1]))
        return render(canvas, lines, line_data, h, w)

    monkeypatch.setattr(exporter, "_render_plot_frame", capture_trace)
    exporter.export_video(
        **args_for(
            synthetic_video,
            output,
            start_s=0.2,
            end_s=0.5,
            speed_factor=speed,
            crop=(8, 6, 56, 42),
            colour_lut=lut,
            offset_s=0.3,
            orientation=orientation,
        )
    )
    frames = decode(output)
    assert len(frames) == len(indices)
    geometry = composition_geometry(48, 36, 0.6, orientation)
    for frame, index in zip(frames, indices, strict=True):
        expected = cv2.resize(
            apply_lut(source_frames[index][6:42, 8:56], lut),
            (geometry.video_width, geometry.video_height),
        )
        actual = frame[: geometry.video_height, : geometry.video_width]
        assert np.mean(np.abs(actual.astype(float) - expected.astype(float))) < 5
    # Independently specified data timestamps for source start .2 s and offset +.3 s.
    expected_times = {0.5: [0.5, 0.5, 0.6, 0.6, 0.7, 0.7], 1: [0.5, 0.6, 0.7], 2: [0.5, 0.7]}
    # The rendered trace has a constant x-placement offset beside the scale
    # bar. Playback timing is preserved by the differences between samples.
    actual_intervals = np.diff(trace_times)
    expected_intervals = np.diff(expected_times[speed])
    assert actual_intervals == pytest.approx(expected_intervals)
    # Actual encoded plot contains the chosen blue trace, not just a blank panel.
    plot = (
        frames[-1][geometry.video_height :, :]
        if orientation == "stacked"
        else frames[-1][:, geometry.video_width :]
    )
    assert np.count_nonzero((plot[:, :, 0] > 150) & (plot[:, :, 2] < 100)) > 10


def test_cancel_during_encoding_preserves_destination_and_cleans_temp(synthetic_video, tmp_path):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous export")
    progress = []
    with pytest.raises(exporter.ExportCancelled):
        exporter.export_video(
            **args_for(
                synthetic_video,
                output,
                progress_callback=lambda n, total: progress.append(n),
                cancel_callback=lambda: len(progress) >= 3,
            )
        )
    assert progress == [1, 2, 3]
    assert output.read_bytes() == b"previous export"
    assert load_records()[0]["status"] == "cancelled"
    assert not list(tmp_path.glob(".out.mp4.*.mp4"))


@pytest.mark.parametrize("phase", ["encode", "publish", "sidecar", "final_history"])
def test_storage_failure_at_each_export_phase(synthetic_video, tmp_path, monkeypatch, phase):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous export")
    real_write = export_history.write_record
    real_replace = os.replace

    def write(path, document):
        if phase == "sidecar" and path.parent == tmp_path:
            raise OSError(errno.ENOSPC, "Simulated full output volume")
        if (
            phase == "final_history"
            and document["status"] == "succeeded"
            and path.parent != tmp_path
        ):
            raise OSError(errno.ENOSPC, "Simulated full history volume")
        return real_write(path, document)

    def replace(src, dst):
        if phase == "publish" and Path(dst) == output:
            raise PermissionError("Simulated destination lock")
        return real_replace(src, dst)

    def progress(n, total):
        if phase == "encode" and n == 2:
            raise OSError(errno.ENOSPC, "Simulated encoder write failure")

    monkeypatch.setattr(export_history, "write_record", write)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError) as error:
        exporter.export_video(**args_for(synthetic_video, output, progress_callback=progress))
    if phase in {"encode", "publish"}:
        assert output.read_bytes() == b"previous export"
        assert load_records()[0]["status"] == "failed"
    else:
        assert len(decode(output)) == 10
        assert "saved" in str(error.value).lower()
        if phase == "final_history":
            copies = list(tmp_path.glob("*.run.json"))
            assert len(copies) == 1
            assert json.loads(copies[0].read_text(encoding="utf-8"))["status"] == "succeeded"
    assert not list(tmp_path.glob(".out.mp4.*.mp4"))


@pytest.mark.parametrize("damage", ["garbage", "truncated"])
def test_corrupt_media_never_replaces_existing_output(synthetic_video, tmp_path, damage):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not video" if damage == "garbage" else synthetic_video.read_bytes()[:100])
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous export")
    with pytest.raises(OSError):
        exporter.export_video(**args_for(source, output))
    assert output.read_bytes() == b"previous export"
    assert load_records()[0]["status"] == "failed"


def test_process_exit_keeps_original_and_durable_started_record(synthetic_video, tmp_path):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous export")
    script = """
import os, sys
from pathlib import Path
import pandas as pd
from video_data_sync import export_history
from video_data_sync.video_processing.exporter import export_video
export_history.history_directory = lambda: Path(sys.argv[3])
export_video(sys.argv[2], sys.argv[1], 10, 0, 1, None, None, 1,
             pd.DataFrame({'t':[0.,1.], 'v':[1.,2.]}), 't', 'v', 0, 'blue', None, None,
             progress_callback=lambda n,total: os._exit(23) if n == 2 else None)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(synthetic_video),
            str(output),
            str(tmp_path / "crash-history"),
        ],
        timeout=40,
        capture_output=True,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    assert output.read_bytes() == b"previous export"
    record = load_records(tmp_path / "crash-history")[0]
    assert record["status"] == "running" and record["finished_at"] is None


def test_gui_load_crop_play_export_search_restore(tmp_path, monkeypatch, synthetic_video):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    for module in ("main_window", "video_tab", "data_tab", "animated_tab"):
        monkeypatch.setattr(f"video_data_sync.gui.{module}.QSettings", lambda: settings)
    monkeypatch.setattr("video_data_sync.gui.data_tab.read_folder_path", lambda: str(tmp_path))
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", lambda p: None)
    monkeypatch.setattr(
        MainWindow, "_recovery_path", property(lambda self: tmp_path / "recovery.json")
    )
    monkeypatch.setattr(MainWindow, "_offer_recovery_if_available", lambda self: None)
    notices = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *args: notices.append(args[1:]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    source = tmp_path / "signals.csv"
    source.write_text("Time (s),signal\n0,1\n0.3,2\n0.6,3\n0.9,2\n1.2,1\n", encoding="utf-8")
    selections = iter([str(synthetic_video), str(source)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (next(selections), ""))
    output = tmp_path / "gui.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window = MainWindow()
    window.show()

    def wait_for(condition, seconds=60):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(20)
        assert condition(), notices

    try:
        wait_for(lambda: window.data_tab is not None and window.animated_tab is not None)
        assert window.data_tab is not None
        animated = window.animated_tab
        assert animated is not None
        QTest.mouseClick(window.video_tab.btn_load, Qt.MouseButton.LeftButton)
        display = window.video_tab.video_display
        QTest.mousePress(display, Qt.MouseButton.LeftButton, pos=QPoint(160, 120))
        QTest.mouseMove(display, QPoint(300, 220))
        QTest.mouseRelease(display, Qt.MouseButton.LeftButton, pos=QPoint(300, 220))
        assert display.crop is not None and display.crop.is_valid()
        QTest.mouseClick(window.video_tab.btn_confirm, Qt.MouseButton.LeftButton)
        window.tabs.setCurrentIndex(1)
        QTest.mouseClick(window.data_tab.btn_load, Qt.MouseButton.LeftButton)
        window.tabs.setCurrentIndex(2)
        artifact_dir = os.environ.get("VDSP_QA_ARTIFACTS")
        if artifact_dir:
            Path(artifact_dir).mkdir(parents=True, exist_ok=True)
            QTest.qWait(100)
            window.grab().save(str(Path(artifact_dir) / "animated-preview.png"))
        pixmap = animated.video_label.pixmap()
        assert pixmap is not None
        assert pixmap.width() >= animated.video_label.width() - 2
        animated._on_play()
        wait_for(lambda: animated._frame_index >= 2)
        trace_line = animated.anim_plot._lines[0]
        assert len(np.asarray(trace_line.get_xdata())) == 1
        trace_point = animated.anim_plot.ax.transData.transform(
            (np.asarray(trace_line.get_xdata())[0], np.asarray(trace_line.get_ydata())[0])
        )
        scale_bar = animated.anim_plot.ax.lines[0]
        scale_bar_right_endpoint = animated.anim_plot.ax.transData.transform(
            (np.asarray(scale_bar.get_xdata())[1], np.asarray(scale_bar.get_ydata())[0])
        )
        vertical_bar = animated.anim_plot.ax.lines[1]
        vertical_bar_top = animated.anim_plot.ax.transData.transform(
            (np.asarray(vertical_bar.get_xdata())[0], np.asarray(vertical_bar.get_ydata())[1])
        )
        expected_origin = animated.anim_plot.ax.transData.transform(
            (
                float(np.mean(np.asarray(scale_bar.get_xdata()))),
                float(np.mean(np.asarray(vertical_bar.get_ydata()))),
            )
        )
        assert np.linalg.norm(trace_point - expected_origin) <= 6
        assert scale_bar_right_endpoint[0] > expected_origin[0]
        assert vertical_bar_top[1] > expected_origin[1]
        if artifact_dir:
            window.grab().save(str(Path(artifact_dir) / "animated-preview-play.png"))
        animated._on_pause()
        QTest.mouseClick(animated.btn_export, Qt.MouseButton.LeftButton)
        wait_for(lambda: window._export_worker is None)
        assert output.exists(), notices
        assert len(decode(output)) == 12
        history = ExportHistoryDialog(window)
        history.search.setText("gui.mp4")
        assert history.runs.count() == 1
        record = history._record()
        assert record is not None
        assert record["status"] == "succeeded"
        assert window._restore_export_settings(record)
        assert window.video_tab.video_display.crop is not None
        assert window.video_tab.video_display.crop.to_tuple() == tuple(record["settings"]["crop"])
        project_path = tmp_path / "workflow.vdsp.json"
        assert window._write_project(str(project_path))
        window._reset_project_state()
        assert window._load_project_path(str(project_path))
        assert window.data_tab is not None
        restored_animated = window.animated_tab
        assert restored_animated is not None
        assert window.data_tab.selected_column == "signal"
        assert restored_animated.video_path == str(synthetic_video)
        restored_animated._on_play()
        wait_for(lambda: restored_animated._frame_index >= 6)
        restored_animated._on_pause()
        artifact_dir = os.environ.get("VDSP_QA_ARTIFACTS")
        if artifact_dir:
            Path(artifact_dir).mkdir(parents=True, exist_ok=True)
            window.grab().save(str(Path(artifact_dir) / "gui-workflow.png"))
            history.show()
            QTest.qWait(50)
            history.grab().save(str(Path(artifact_dir) / "export-history.png"))
            frames = decode(output)
            for name, index in (("start", 0), ("middle", 6), ("end", 11)):
                cv2.imwrite(str(Path(artifact_dir) / f"export-{name}.png"), frames[index])
        history.close()
    finally:
        if window._export_worker is not None:
            window._export_worker.request_cancel()
            window._export_worker.wait(30000)
        window._dirty = False
        app.processEvents()
        # Destroy this test's window (and its history dialog child) while the
        # QApplication is alive instead of leaving it for interpreter teardown.
        dispose_widget(window)


@pytest.mark.parametrize("fourcc,extension", [("MJPG", "avi"), ("mp4v", "mp4"), ("avc1", "mp4")])
def test_available_codec_round_trip(tmp_path, fourcc, extension):
    source = tmp_path / f"codec.{extension}"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter.fourcc(*fourcc), 10, (64, 48))
    if not writer.isOpened():
        pytest.skip(f"{fourcc} encoder unavailable in this OpenCV build")
    for n in range(10):
        writer.write(np.full((48, 64, 3), n * 20, np.uint8))
    writer.release()
    output = tmp_path / "converted.mp4"
    exporter.export_video(**args_for(source, output))
    assert len(decode(output)) == 10


def test_large_table_and_long_recording_short_clip(tmp_path):
    source = tmp_path / "long.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter.fourcc(*"mp4v"), 30, (320, 240))
    assert writer.isOpened()
    for n in range(1800):
        writer.write(np.full((240, 320, 3), n % 200, np.uint8))
    writer.release()
    df = pd.DataFrame(
        {"t": np.linspace(0, 60, 1_000_000), "v": np.sin(np.linspace(0, 100, 1_000_000))}
    )
    output = tmp_path / "large.mp4"
    exporter.export_video(**args_for(source, output, fps=30, start_s=58, end_s=60, df=df))
    assert len(decode(output)) == 60
    assert load_records()[0]["data_snapshot"]["rows"] == 1_000_000


def test_fractional_clip_end_keeps_last_eligible_source_frame(synthetic_video, tmp_path):
    output = tmp_path / "fractional.mp4"
    exporter.export_video(**args_for(synthetic_video, output, end_s=0.25))
    frames = decode(output)
    expected = decode(synthetic_video)
    assert len(frames) == 3
    for i in range(3):
        assert abs(float(frames[i][:500, :500].mean()) - float(expected[i].mean())) < 5


def test_unicode_paths_and_4k_input(tmp_path):
    source = tmp_path / "synthetic µ 中文.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter.fourcc(*"mp4v"), 10, (3840, 2160))
    assert writer.isOpened()
    for n in range(5):
        writer.write(np.full((2160, 3840, 3), 30 + n * 20, np.uint8))
    writer.release()
    output = tmp_path / "result µ 中文.mp4"
    exporter.export_video(**args_for(source, output, end_s=0.5))
    assert len(decode(output)) == 5


def test_malformed_history_entry_does_not_break_history(tmp_path):
    directory = tmp_path / "history"
    directory.mkdir()
    (directory / "bad.run.json").write_text(
        json.dumps(
            {
                "kind": "video-data-sync-export-run",
                "settings": [],
                "started_at": 123,
            }
        ),
        encoding="utf-8",
    )
    assert load_records(directory) == []


@pytest.mark.parametrize("fps,expected_frames", [(120, 6), (29.97, 3)])
def test_high_and_fractional_frame_rates(tmp_path, fps, expected_frames):
    source = tmp_path / "rate.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter.fourcc(*"mp4v"), fps, (64, 48))
    assert writer.isOpened()
    for n in range(30):
        writer.write(np.full((48, 64, 3), n * 6, np.uint8))
    writer.release()
    output = tmp_path / "rate-out.mp4"
    exporter.export_video(**args_for(source, output, fps=fps, end_s=0.1))
    assert len(decode(output)) == expected_frames
    assert load_records()[0]["resolved"]["output_fps"] <= 60


def test_silent_encoder_frame_loss_is_detected(synthetic_video, tmp_path, monkeypatch):
    real_open = exporter._open_video_writer

    class DroppingWriter:
        def __init__(self, writer):
            self.writer = writer
            self.count = 0

        def write(self, frame):
            self.count += 1
            if self.count < 3:
                self.writer.write(frame)

        def release(self):
            self.writer.release()

    monkeypatch.setattr(
        exporter, "_open_video_writer", lambda *args: DroppingWriter(real_open(*args))
    )
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous export")
    with pytest.raises(OSError, match="verified"):
        exporter.export_video(**args_for(synthetic_video, output))
    assert output.read_bytes() == b"previous export"
    assert load_records()[0]["status"] == "failed"


def test_custom_speed_label_retains_precision(synthetic_video, tmp_path, monkeypatch):
    labels = []
    real_put = cv2.putText

    def put(frame, text, *args):
        labels.append(text)
        return real_put(frame, text, *args)

    monkeypatch.setattr(cv2, "putText", put)
    exporter.export_video(**args_for(synthetic_video, tmp_path / "out.mp4", speed_factor=1.25))
    assert set(labels) == {"1.25x"}
