"""Video colour correction applies to the current video and is saved only in projects."""

from __future__ import annotations

import os

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from qt_teardown import OwnedWidgets, dispose_widget

from video_data_sync.gui.main_window import MainWindow
from video_data_sync.gui.video_tab import LEGACY_COLOUR_SETTINGS_KEYS, VideoTab

DEFAULTS = (0, 100, 100, 100, 100, 100)
CHANGED = (25, 150, 80, 120, 90, 110)
PROJECT_COLOUR = {
    "brightness": 25,
    "contrast": 1.5,
    "gamma": 0.8,
    "r_gain": 1.2,
    "g_gain": 0.9,
    "b_gain": 1.1,
}


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _reset_video_tab(widget) -> None:
    if isinstance(widget, VideoTab):
        widget.reset_state()


@pytest.fixture
def owned_widgets(app):
    owned = OwnedWidgets(before_dispose=_reset_video_tab)
    yield owned
    owned.dispose_all()


@pytest.fixture
def other_video(tmp_path):
    path = tmp_path / "other.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10, (64, 48))
    for index in range(12):
        writer.write(np.full((48, 64, 3), 200 - index * 10, dtype=np.uint8))
    writer.release()
    return path


def _sliders(tab: VideoTab):
    return (
        tab._brightness_slider,
        tab._contrast_slider,
        tab._gamma_slider,
        tab._r_slider,
        tab._g_slider,
        tab._b_slider,
    )


def _positions(tab: VideoTab) -> tuple[int, ...]:
    return tuple(slider.value() for slider in _sliders(tab))


def _change_colour(tab: VideoTab) -> None:
    for slider, value in zip(_sliders(tab), CHANGED, strict=True):
        slider.setValue(value)
    assert _positions(tab) == CHANGED


def _stored_colour_keys() -> list[str]:
    settings = QSettings()
    return [key for key in LEGACY_COLOUR_SETTINGS_KEYS if settings.contains(key)]


def test_opening_another_video_resets_colour(owned_widgets, synthetic_video, other_video):
    tab = owned_widgets(VideoTab())
    tab._load_video(str(synthetic_video))
    _change_colour(tab)
    assert not tab.lbl_colour_note.isHidden()

    tab._load_video(str(other_video))

    assert _positions(tab) == DEFAULTS
    assert tab.lbl_colour_note.isHidden()


def test_reopening_the_same_video_resets_colour(owned_widgets, synthetic_video):
    tab = owned_widgets(VideoTab())
    tab._load_video(str(synthetic_video))
    _change_colour(tab)
    tab._load_video(str(synthetic_video))
    assert _positions(tab) == DEFAULTS


def test_colour_is_not_written_to_preferences_and_new_tab_starts_at_defaults(
    owned_widgets, synthetic_video
):
    first = owned_widgets(VideoTab())
    first._load_video(str(synthetic_video))
    _change_colour(first)
    assert _stored_colour_keys() == []

    second = owned_widgets(VideoTab())
    assert _positions(second) == DEFAULTS
    assert _stored_colour_keys() == []


def test_stale_colour_keys_are_ignored_and_only_they_are_removed(owned_widgets):
    settings = QSettings()
    for key, value in zip(LEGACY_COLOUR_SETTINGS_KEYS, CHANGED, strict=True):
        settings.setValue(key, value)
    settings.setValue("folders/video", "remembered-folder")
    settings.setValue("data/traceColor", "#123456")
    settings.sync()

    tab = owned_widgets(VideoTab())

    assert _positions(tab) == DEFAULTS
    assert _stored_colour_keys() == []
    current = QSettings()
    assert current.value("folders/video") == "remembered-folder"
    assert current.value("data/traceColor") == "#123456"


def test_project_colour_survives_load_then_manual_open_resets(
    owned_widgets, synthetic_video, other_video
):
    tab = owned_widgets(VideoTab())
    assert tab.load_project_video(str(synthetic_video), 0.0, 1.0, colour=PROJECT_COLOUR)
    assert _positions(tab) == CHANGED
    assert _stored_colour_keys() == []

    tab._load_video(str(other_video))

    assert _positions(tab) == DEFAULTS


def test_applied_colour_settings_affect_current_video_only(
    owned_widgets, synthetic_video, other_video
):
    tab = owned_widgets(VideoTab())
    tab._load_video(str(synthetic_video))
    tab.apply_colour_settings(PROJECT_COLOUR, persist=True)
    assert _positions(tab) == CHANGED
    assert _stored_colour_keys() == []
    tab._load_video(str(other_video))
    assert _positions(tab) == DEFAULTS


def test_pick_black_point_changes_current_video_only(owned_widgets, synthetic_video, other_video):
    tab = owned_widgets(VideoTab())
    tab._load_video(str(other_video))  # first frame is mid grey (200)
    tab.btn_pick_black.click()
    tab._on_pixel_picked(10, 10)
    assert not tab.btn_pick_black.isChecked()
    assert tab._brightness_slider.value() < 0
    assert _stored_colour_keys() == []

    tab._load_video(str(synthetic_video))
    assert _positions(tab) == DEFAULTS


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_recovery_if_available", lambda self: None)
    win = MainWindow()
    win._preload_done = True
    win._ensure_data_tab()
    win._ensure_animated_tab()
    yield win
    win._set_dirty(False)
    dispose_widget(win)


def test_project_round_trip_keeps_colour_and_next_video_resets(
    window, synthetic_video, other_video, tmp_path, monkeypatch
):
    source = tmp_path / "data.csv"
    source.write_text("Time (s),signal\n0,1\n0.5,2\n1,4\n", encoding="utf-8")
    assert window.data_tab is not None
    assert window.data_tab.load_file(str(source), time_unit="Seconds", remember_folder=False)
    window.video_tab._load_video(str(synthetic_video))
    _change_colour(window.video_tab)
    project = tmp_path / "colour.vdsp.json"
    assert window._write_project(str(project))
    assert _stored_colour_keys() == []

    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_recovery_if_available", lambda self: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    restored = MainWindow()
    restored._preload_done = True
    restored._ensure_data_tab()
    restored._ensure_animated_tab()
    try:
        assert restored._load_project_path(str(project))
        assert _positions(restored.video_tab) == CHANGED
        restored.video_tab._load_video(str(other_video))
        assert _positions(restored.video_tab) == DEFAULTS
        assert _stored_colour_keys() == []
    finally:
        restored._set_dirty(False)
        dispose_widget(restored)
