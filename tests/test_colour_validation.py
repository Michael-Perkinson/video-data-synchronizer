"""Persisted colour-correction values must never crash or silently change."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from qt_teardown import dispose_widget

from video_data_sync.gui.video_tab import VideoTab

DEFAULT_POSITIONS = {"brightness": 0, "contrast": 100, "gamma": 100, "r": 100, "g": 100, "b": 100}
MALFORMED = ["bright", float("nan"), float("inf"), -float("inf"), 1e308, True, None, [1], {"x": 1}]
# float() of this int raises OverflowError; an explicit id avoids str() of it.
HUGE_INT = pytest.param(10**10000, id="huge-int")
PROJECT_KEYS = ("brightness", "contrast", "gamma", "r_gain", "g_gain", "b_gain")
SETTINGS_KEYS = (
    "video/brightness",
    "video/contrast",
    "video/gamma",
    "video/rGain",
    "video/gGain",
    "video/bGain",
)


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


def _positions(tab: VideoTab) -> dict[str, int]:
    return {
        "brightness": tab._brightness_slider.value(),
        "contrast": tab._contrast_slider.value(),
        "gamma": tab._gamma_slider.value(),
        "r": tab._r_slider.value(),
        "g": tab._g_slider.value(),
        "b": tab._b_slider.value(),
    }


@pytest.mark.parametrize("value", [*MALFORMED, "1.5e10", 500, -500])
def test_malformed_saved_colour_preferences_fall_back_to_defaults(app, value):
    settings = QSettings()
    for key in SETTINGS_KEYS:
        settings.setValue(key, value)
    settings.sync()
    tab = VideoTab()
    try:
        assert _positions(tab) == DEFAULT_POSITIONS
    finally:
        dispose_widget(tab)


def test_valid_saved_colour_preferences_are_ignored_and_removed(app):
    # Changed rule: colour correction is no longer a global preference. Values that an
    # older version saved (even valid ones) are ignored, and the stale keys are deleted.
    settings = QSettings()
    for key, value in zip(SETTINGS_KEYS, (-40, 150, "250", 0, 200, "75"), strict=True):
        settings.setValue(key, value)
    settings.setValue("folders/video", "keep-me")
    settings.sync()
    tab = VideoTab()
    try:
        assert _positions(tab) == DEFAULT_POSITIONS
        current = QSettings()
        assert [key for key in SETTINGS_KEYS if current.contains(key)] == []
        assert current.value("folders/video") == "keep-me"
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize("value", [*MALFORMED, 500.0, -300.0, HUGE_INT])
def test_malformed_project_colour_falls_back_to_defaults(app, synthetic_video, value):
    tab = VideoTab()
    try:
        colour = dict.fromkeys(PROJECT_KEYS, value)
        assert tab.load_project_video(str(synthetic_video), 0.0, 1.0, colour=colour)
        assert _positions(tab) == DEFAULT_POSITIONS
    finally:
        tab.reset_state()
        dispose_widget(tab)


def test_valid_project_colour_is_kept_and_reaches_export(app, synthetic_video):
    tab = VideoTab()
    emitted = []
    tab.cropped_confirmed.connect(lambda *args: emitted.append(args))
    colour = {
        "brightness": 12.9,
        "contrast": 1.5,
        "gamma": "0.8",
        "r_gain": 0,
        "g_gain": 2.0,
        "b_gain": 1.234,
    }
    try:
        assert tab.load_project_video(str(synthetic_video), 0.0, 1.0, colour=colour)
        assert _positions(tab) == {
            "brightness": 12,
            "contrast": 150,
            "gamma": 80,
            "r": 0,
            "g": 200,
            "b": 123,
        }
        assert emitted and emitted[-1][5] is not None
    finally:
        tab.reset_state()
        dispose_widget(tab)


@pytest.mark.parametrize("colour", ["red", [1, 2], 3])
def test_non_dict_project_colour_falls_back_to_defaults(app, synthetic_video, colour):
    tab = VideoTab()
    try:
        assert tab.load_project_video(str(synthetic_video), 0.0, 1.0, colour=colour)
        assert _positions(tab) == DEFAULT_POSITIONS
    finally:
        tab.reset_state()
        dispose_widget(tab)


@pytest.mark.parametrize("value", ["bright", float("nan"), 1e308, None, HUGE_INT])
def test_malformed_applied_colour_settings_fall_back_to_defaults(app, value):
    tab = VideoTab()
    try:
        tab.apply_colour_settings(dict.fromkeys(PROJECT_KEYS, value), persist=False)
        assert _positions(tab) == DEFAULT_POSITIONS
    finally:
        dispose_widget(tab)


@pytest.mark.parametrize("settings", ["red", [1, 2], None])
def test_non_dict_applied_colour_settings_fall_back_to_defaults(app, settings):
    tab = VideoTab()
    try:
        tab.apply_colour_settings(settings, persist=False)
        assert _positions(tab) == DEFAULT_POSITIONS
    finally:
        dispose_widget(tab)
