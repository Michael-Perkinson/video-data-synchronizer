"""The Data tab's time unit override offers milliseconds and hours."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from qt_teardown import OwnedWidgets

from video_data_sync.gui.data_tab import DataTab


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.ini"
    monkeypatch.setattr(
        "video_data_sync.gui.data_tab.QSettings",
        lambda: QSettings(str(path), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr("video_data_sync.gui.data_tab.read_folder_path", lambda: str(tmp_path))
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", lambda path: None)
    return path


@pytest.fixture
def owned_widgets(app):
    owned = OwnedWidgets()
    yield owned
    owned.dispose_all()


def _store(path, value) -> None:
    store = QSettings(str(path), QSettings.Format.IniFormat)
    store.setValue("data/timeUnit", value)
    store.sync()


def test_time_unit_choices_keep_existing_identifiers_and_add_ms_and_hours(settings, owned_widgets):
    tab = owned_widgets(DataTab())
    combo = tab.time_unit_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Auto-detect",
        "Seconds",
        "Minutes",
        "Milliseconds",
        "Hours",
    ]
    # The combo is not clipped to a width narrower than its longest choice.
    assert combo.sizeHint().width() > combo.fontMetrics().horizontalAdvance("Milliseconds")
    assert combo.maximumWidth() >= combo.sizeHint().width()


@pytest.mark.parametrize("stored", ["Seconds", "Minutes", "Milliseconds", "Hours"])
def test_stored_time_unit_is_restored(settings, owned_widgets, stored):
    _store(settings, stored)
    tab = owned_widgets(DataTab())
    assert tab.time_unit_combo.currentText() == stored


def test_unknown_stored_time_unit_falls_back_to_auto_detect(settings, owned_widgets):
    _store(settings, "Fortnights")
    tab = owned_widgets(DataTab())
    assert tab.time_unit_combo.currentText() == "Auto-detect"


def test_milliseconds_override_converts_loaded_data(settings, owned_widgets, tmp_path, monkeypatch):
    source = tmp_path / "elapsed.csv"
    source.write_text("elapsed,signal\n0,1\n500,2\n1000,3\n", encoding="utf-8")
    errors: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    tab = owned_widgets(DataTab())
    loaded = tab.load_file(
        str(source), time_column="elapsed", time_unit="Milliseconds", remember_folder=False
    )
    assert errors == []
    assert loaded
    assert tab.dataframe["Time (s)"].tolist() == pytest.approx([0.0, 0.5, 1.0])
    assert tab.time_unit_combo.currentText() == "Milliseconds"
