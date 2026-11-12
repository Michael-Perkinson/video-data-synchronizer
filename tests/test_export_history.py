"""Real export records and the user-facing history workflow."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication
from qt_teardown import OwnedWidgets

from video_data_sync.export_history import load_records
from video_data_sync.gui.history_dialog import ExportHistoryDialog
from video_data_sync.gui.main_window import _ExportWorker
from video_data_sync.video_processing.exporter import ExportCancelled, export_video


@pytest.fixture
def owned_widgets():
    """Dispose only the top-level widgets this test registers."""
    owner = OwnedWidgets()
    yield owner
    owner.dispose_all()


def export(output, source, **extra):
    return export_video(
        str(output),
        str(source),
        10,
        0,
        0.4,
        None,
        None,
        2,
        pd.DataFrame({"t": [0.0, 0.1, 0.2, 0.3, 0.4], "v": [3.0, 4.0, 5.0, 6.0, 7.0]}),
        "t",
        "v",
        0,
        "blue",
        None,
        None,
        **extra,
    )


def test_real_export_records_effective_settings_and_preserves_previous_runs(
    synthetic_video, tmp_path
):
    output = tmp_path / "session12_sync.mp4"
    export(output, synthetic_video)
    first = load_records()[0]
    assert first["status"] == "succeeded"
    assert first["settings"]["speed_factor"] == 2
    assert first["settings"]["color"] == "blue"
    assert first["resolved"]["output_fps"] == 10
    assert first["resolved"]["output_frames"] == 2
    assert first["resolved"]["y_limits"] == [0, 4]
    assert first["resolved"]["trace_baselines"] == {"v": 3}
    assert first["resolved"]["codec"]
    assert (
        first["sources"]["video"]["sha256"]
        == hashlib.sha256(synthetic_video.read_bytes()).hexdigest()
    )
    assert first["output_fingerprint"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    export(output, synthetic_video, color2="red")
    assert len(load_records()) == 2
    assert len(list(tmp_path.glob("*.run.json"))) == 2
    assert json.loads(Path(first["record_path"]).read_text(encoding="utf-8")) == first


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_and_cancelled_attempts_have_records(synthetic_video, tmp_path, cancel):
    output = tmp_path / "out.mp4"
    with pytest.raises(ExportCancelled if cancel else ValueError):
        export(
            output,
            synthetic_video,
            cancel_callback=lambda: cancel,
            video_width_fraction=0.6 if cancel else 2,
        )
    record = load_records()[0]
    assert record["status"] == ("cancelled" if cancel else "failed")
    assert record["finished_at"]
    assert record["error"]
    assert not output.exists()


def test_history_write_failure_prevents_unlogged_export(synthetic_video, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("read-only history")

    monkeypatch.setattr("video_data_sync.export_history.write_record", fail)
    output = tmp_path / "out.mp4"
    with pytest.raises(OSError, match="read-only"):
        export(output, synthetic_video)
    assert not output.exists()


def test_history_search_and_restore_use_recorded_project(synthetic_video, tmp_path, owned_widgets):
    app = QApplication.instance() or QApplication([])
    project = {"schema_version": 1, "video": {}, "data": {}, "composition": {"speed": "2x"}}
    export(
        tmp_path / "session12_sync.mp4",
        synthetic_video,
        audit_context={"project_name": "Experiment Alpha", "project": project},
    )
    project["composition"]["speed"] = "8x"
    dialog = owned_widgets(ExportHistoryDialog())
    dialog.search.setText("alpha 2x")
    assert dialog.runs.count() == 1
    assert "Speed: 2x" in dialog.details.toPlainText()
    seen = []
    dialog.restore_requested.connect(seen.append)
    dialog.restore_button.click()
    assert seen[0]["context"]["project"]["composition"]["speed"] == "2x"
    dialog.search.setText("not-a-recording")
    assert dialog.runs.count() == 0
    assert not dialog.restore_button.isEnabled()
    dialog.close()
    assert app is not None


def test_batch_creates_a_separate_record_for_each_speed(synthetic_video, tmp_path):
    jobs = []
    for speed in (1, 2):
        jobs.append(
            {
                "output_path": str(tmp_path / f"batch_{speed}x.mp4"),
                "video_path": str(synthetic_video),
                "fps": 10,
                "start_s": 0,
                "end_s": 0.4,
                "crop": None,
                "colour_lut": None,
                "speed_factor": speed,
                "df": pd.DataFrame({"t": [0.0, 0.2, 0.4], "v": [1.0, 2.0, 3.0]}),
                "time_col": "t",
                "data_col": "v",
                "offset_s": 0,
                "color": "blue",
                "y_min": None,
                "y_max": None,
                "_estimated_frames": 4 // speed,
            }
        )
    worker = _ExportWorker(jobs)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert not errors
    records = load_records()
    assert {r["settings"]["speed_factor"] for r in records} == {1, 2}
    assert all(r["status"] == "succeeded" for r in records)


def test_source_changed_since_import_is_rejected_and_logged(synthetic_video, tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("new contents", encoding="utf-8")
    output = tmp_path / "out.mp4"
    with pytest.raises(ValueError, match="changed since loading"):
        export(
            output,
            synthetic_video,
            audit_context={
                "data_path": str(source),
                "loaded_data_fingerprint": {"sha256": "old contents"},
            },
        )
    assert not output.exists()
    assert load_records()[0]["status"] == "failed"
