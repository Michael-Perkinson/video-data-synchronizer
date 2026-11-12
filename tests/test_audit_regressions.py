"""Regressions for import, project, and export issues found in the repo audit."""

import numpy as np
import pandas as pd
import pytest
from matplotlib.figure import Figure
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from qt_teardown import dispose_widget

from video_data_sync.data.processor import calculate_z_score, prepare_dataframe
from video_data_sync.gui.data_tab import DataTab
from video_data_sync.plotting.composition import axes_composition, draw_scale_bars
from video_data_sync.settings.presets import PresetValidationError, default_preset, validate_preset
from video_data_sync.video_processing.exporter import export_video


@pytest.fixture
def data_tab(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("video_data_sync.gui.data_tab.QSettings", lambda: settings)
    monkeypatch.setattr("video_data_sync.gui.data_tab.read_folder_path", lambda: str(tmp_path))
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", lambda path: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    tab = DataTab()
    yield tab
    dispose_widget(tab)
    assert app is not None


def load_good(tab, tmp_path):
    path = tmp_path / "good.csv"
    path.write_text("Time (s),signal\n0,1\n1,2\n2,4\n", encoding="utf-8")
    assert tab.load_file(str(path))
    return path


@pytest.mark.parametrize("rows", ["", "0,1\n0,2\n2,4\n"])
def test_bad_import_preserves_current_recording(data_tab, tmp_path, rows):
    good = load_good(data_tab, tmp_path)
    before = data_tab.dataframe.copy()
    bad = tmp_path / "bad.csv"
    bad.write_text("Time (s),signal\n" + rows, encoding="utf-8")
    assert not data_tab.load_file(str(bad))
    assert data_tab.data_path == str(good)
    pd.testing.assert_frame_equal(data_tab.dataframe, before)


def test_preset_preserves_recording_settings(data_tab, tmp_path):
    load_good(data_tab, tmp_path)
    data_tab.time_unit_combo.setCurrentText("Minutes")
    data_tab.radio_fixed.setChecked(True)
    data_tab.y_min_input.setText("-2")
    data_tab.y_max_input.setText("2")
    before = data_tab.dataframe.copy()
    data_tab.apply_appearance_settings(default_preset()["traces"])
    assert data_tab.time_unit_combo.currentText() == "Minutes"
    assert data_tab.y_limits() == (-2, 2)
    pd.testing.assert_frame_equal(data_tab.dataframe, before)


def test_chained_zscore_can_be_recreated(data_tab, tmp_path):
    load_good(data_tab, tmp_path)
    data_tab.z_start_input.setText("0")
    data_tab.z_end_input.setText("2")
    data_tab._on_zscore()
    data_tab._on_zscore()
    recipe = data_tab.zscore_recipe
    expected = data_tab.dataframe.copy()
    load_good(data_tab, tmp_path)
    data_tab.recreate_zscore(recipe)
    pd.testing.assert_frame_equal(data_tab.dataframe, expected)


@pytest.mark.parametrize("values", [[1.0], [np.nan, np.nan], [1.0, np.inf]])
def test_invalid_zscore_baselines_are_rejected(values):
    df = pd.DataFrame({"t": range(len(values)), "v": values})
    with pytest.raises(ValueError):
        calculate_z_score(df, "t", "v", 0, len(values))


def test_time_name_takes_precedence_over_other_unit_columns():
    df = pd.DataFrame({"Duration (s)": [3, 3, 3], "Time (s)": [0, 1, 2], "v": [1, 2, 3]})
    prepared, time = prepare_dataframe(df)
    assert prepared[time].tolist() == [0, 1, 2]


@pytest.mark.parametrize("section,key", [("traces", "transform"), ("composition", "layout")])
def test_malformed_preset_enum_raises_validation_error(section, key):
    preset = default_preset()
    preset[section][key] = []
    with pytest.raises(PresetValidationError):
        validate_preset(preset)


def test_fractional_time_scale_label_is_exact():
    ax = Figure().subplots()
    draw_scale_bars(
        ax, axes_composition(0, 10, 0, 1, time_scale=1.5), "", font_size=16, line_width=2
    )
    assert ax.texts[0].get_text() == "1.5 s"


def test_export_cannot_replace_its_source(synthetic_video):
    before = synthetic_video.read_bytes()
    with pytest.raises(ValueError, match="source"):
        export_video(
            str(synthetic_video),
            str(synthetic_video),
            10,
            0,
            0.5,
            None,
            None,
            1,
            pd.DataFrame({"t": [0, 1], "v": [1, 2]}),
            "t",
            "v",
            0,
            "blue",
            None,
            None,
        )
    assert synthetic_video.read_bytes() == before


def test_manual_unit_import_still_validates_timestamps(data_tab, tmp_path):
    good = load_good(data_tab, tmp_path)
    data_tab.time_unit_combo.setCurrentText("Minutes")
    path = tmp_path / "bad.csv"
    path.write_text("Time (s),signal\n0,1\n0,2\n", encoding="utf-8")
    assert not data_tab.load_file(str(path))
    assert data_tab.data_path == str(good)


def test_explicit_time_column_is_used_and_invalid_change_is_rolled_back(data_tab, tmp_path):
    path = tmp_path / "two-clocks.csv"
    path.write_text("Time (s),clock,signal\n0,10,3\n1,11,3\n2,12,3\n", encoding="utf-8")
    assert data_tab.load_file(str(path), time_column="clock", time_unit="Seconds")
    assert data_tab.dataframe[data_tab.time_col].tolist() == [10, 11, 12]
    data_tab.time_column_combo.setCurrentText("signal")
    assert data_tab.time_column_combo.currentText() == "clock"
    assert data_tab.dataframe[data_tab.time_col].tolist() == [10, 11, 12]
