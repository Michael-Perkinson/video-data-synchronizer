import json
import os

import pandas as pd
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog
from qt_teardown import dispose_widget

from video_data_sync.gui.animated_tab import AnimatedTab

MALFORMED_ADVANCED = {
    "scale_font_size": "large",
    "scale_line_width": float("nan"),
    "time_scale_s": "soon",
    "signal_scale": -5,
    "max_change_percent": float("inf"),
    "anchor": "Middle",
}


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return QApplication([])


def _load_clip(tab: AnimatedTab, video) -> None:
    tab.update_video(str(video), 10, 0, 0.6, None, None)
    tab.update_data(
        pd.DataFrame({"Time (s)": [0.0, 0.3, 0.6], "signal": [10.0, 11.0, 14.0]}),
        "Time (s)",
        "signal",
        "#000000",
        None,
        None,
    )


def _open_and_cancel_dialog(tab: AnimatedTab, monkeypatch) -> None:
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    tab._show_composition_dialog()


def _assert_malformed_values_replaced(composition: dict) -> None:
    defaults = AnimatedTab.COMPOSITION_DEFAULTS
    for key in MALFORMED_ADVANCED:
        assert composition[key] == defaults[key]


def test_malformed_project_composition_falls_back_to_defaults(synthetic_video, monkeypatch):
    _app()
    tab = AnimatedTab()
    try:
        tab.apply_project_export_settings(
            {"splitter_sizes": ["inf", 1], "advanced": dict(MALFORMED_ADVANCED)}
        )
        _load_clip(tab, synthetic_video)

        assert tab._plot_ready
        _assert_malformed_values_replaced(tab.composition_settings)
        _open_and_cancel_dialog(tab, monkeypatch)
    finally:
        tab._release_cap()
        dispose_widget(tab)


def test_malformed_saved_composition_falls_back_to_defaults(synthetic_video, monkeypatch):
    _app()
    QSettings().setValue("composition/advanced", json.dumps(MALFORMED_ADVANCED))
    QSettings().setValue("composition/panelSizes", ["1e999", 1])
    tab = AnimatedTab()
    try:
        _load_clip(tab, synthetic_video)

        assert tab._plot_ready
        _assert_malformed_values_replaced(tab.composition_settings)
        _open_and_cancel_dialog(tab, monkeypatch)
    finally:
        tab._release_cap()
        dispose_widget(tab)


def test_valid_composition_values_are_kept_unchanged(monkeypatch):
    _app()
    tab = AnimatedTab()
    valid = {
        "scale_font_size": 16.5,
        "scale_line_width": "3.5",
        "time_scale_s": 2,
        "signal_scale": 0.0,
        "max_change_percent": 100.0,
        "anchor": "Top right",
    }
    try:
        tab.set_composition_settings(dict(valid))

        for key, value in valid.items():
            assert tab.composition_settings[key] == value
        _open_and_cancel_dialog(tab, monkeypatch)
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize("value", [0.0, -1.0, 100.01, 150.0])
def test_out_of_range_max_change_percentage_falls_back_to_default(value):
    _app()
    tab = AnimatedTab()
    try:
        tab.set_composition_settings({"max_change_enabled": True, "max_change_percent": value})

        assert tab.composition_settings["max_change_percent"] == 70.0
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize("value", [0.01, 100.0])
def test_max_change_percentage_accepts_dialog_boundaries(value):
    _app()
    tab = AnimatedTab()
    try:
        tab.set_composition_settings({"max_change_percent": value})

        assert tab.composition_settings["max_change_percent"] == value
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize("sizes", [["1e30", "1e31"], [3e9, 5e9], [0, 100]])
def test_out_of_range_splitter_sizes_keep_default_split(sizes):
    _app()
    tab = AnimatedTab()
    try:
        tab.apply_project_export_settings({"splitter_sizes": sizes})
        assert tab._default_split_pending
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize(
    "value", [True, None, [16], {"size": 16}, 1e10, 0, pytest.param(10**10000, id="huge-int")]
)
def test_invalid_font_size_is_rejected(value):
    _app()
    tab = AnimatedTab()
    try:
        tab.set_composition_settings({"scale_font_size": value})
        assert tab.composition_settings["scale_font_size"] == 16
    finally:
        dispose_widget(tab)
