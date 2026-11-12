"""Focused tests for the video tab's clip-duration feedback."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from qt_teardown import dispose_widget

from video_data_sync.gui.video_tab import VideoTab
from video_data_sync.video_processing.cropper import CropRect


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def tab(app):
    widget = VideoTab()
    yield widget
    widget.reset_state()
    dispose_widget(widget)


def test_loaded_video_shows_full_and_initial_selected_duration(tab, synthetic_video):
    assert tab.btn_confirm.text() == "Apply Video Settings"
    assert not tab.btn_confirm.isEnabled()
    assert "QPushButton:disabled" in tab.btn_confirm.styleSheet()
    tab._load_video(str(synthetic_video))

    assert tab.btn_confirm.isEnabled()
    assert tab.lbl_duration.text() == "Full video duration: 1.2 s"
    assert tab.lbl_selected_duration.text() == "Selected clip duration: 1.2 s"


def test_selected_duration_updates_live_and_handles_incomplete_range(tab):
    tab.start_input.setText("1.5")
    tab.end_input.setText("4.75")
    assert tab.lbl_selected_duration.text() == "Selected clip duration: 3.2 s"

    tab.end_input.clear()
    assert tab.lbl_selected_duration.text() == "Selected clip duration: —"

    tab.end_input.setText("1")
    assert tab.lbl_selected_duration.text() == "Selected clip duration: —"


def test_reset_clears_duration_labels(tab, synthetic_video):
    tab._load_video(str(synthetic_video))
    tab.reset_state()

    assert tab.lbl_duration.text() == "Full video duration: —"
    assert tab.lbl_selected_duration.text() == "Selected clip duration: —"
    assert not tab.btn_rotate.isEnabled()


def test_rotation_cycles_preview_and_transforms_crop_coordinates(tab, synthetic_video):
    tab._load_video(str(synthetic_video))
    tab.video_display._crop = CropRect(8, 6, 56, 42)
    emitted = []
    tab.cropped_confirmed.connect(lambda *args: emitted.append(args))

    tab.btn_rotate.click()
    assert tab.rotation == 90
    assert tab.lbl_rotation.text() == "90°"
    assert (tab.video_display._video_w, tab.video_display._video_h) == (48, 64)
    assert tab.video_display.crop.to_tuple() == (6, 8, 42, 56)
    assert tab._raw_frame.shape[:2] == (64, 48)
    assert "Current orientation: 90°" in tab.btn_rotate.toolTip()

    for _ in range(3):
        tab.btn_rotate.click()
    assert tab.rotation == 0
    assert tab.lbl_rotation.text() == "0°"
    assert (tab.video_display._video_w, tab.video_display._video_h) == (64, 48)
    assert tab.video_display.crop.to_tuple() == (8, 6, 56, 42)
    assert tab._on_confirm()
    assert emitted[-1][-1] == 0


def test_colour_note_explains_active_correction_for_this_video_only(tab, tmp_path):
    # Changed rule: colour correction is no longer remembered between sessions; it applies
    # to the current video only and is saved only in projects. The note says so.
    tab._settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    tab._reset_colour()
    assert tab.lbl_colour_note.isHidden()

    tab._brightness_slider.setValue(20)
    assert not tab.lbl_colour_note.isHidden()
    assert "this video only" in tab.lbl_colour_note.text()
    assert "projects" in tab.lbl_colour_note.text()
    assert "remembered" not in tab.lbl_colour_note.text()
    assert "Reset" in tab.lbl_colour_note.text()
    assert tab._settings.allKeys() == []

    tab._reset_colour()
    assert tab.lbl_colour_note.isHidden()
