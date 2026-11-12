"""
Video Cropping Tab.

Lets the user:
  - Load a video
  - Scrub through it with a slider
  - Draw a crop rectangle by clicking and dragging
  - Set start/end times (seconds)
  - Rotate the video clockwise in 90-degree steps
  - Adjust brightness, contrast, and gamma with live preview
  - Apply the selected clip, crop, rotation, and colour settings to preview/export
"""

from __future__ import annotations

import math

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from video_data_sync.gui.controls_scroll import (
    GROUP_GAP,
    ROW_GAP,
    ROW_SPACING,
    ControlsScrollArea,
    add_gap,
)
from video_data_sync.gui.data_tab import c_locale_validator
from video_data_sync.video_processing.cropper import CropOverlay, CropRect
from video_data_sync.video_processing.rotation import (
    rotate_crop,
    rotate_frame,
    rotated_dimensions,
    validate_rotation,
)


def _project_crop_rect(crop: object) -> CropRect:
    if not isinstance(crop, list | tuple) or len(crop) != 4:
        raise ValueError("Saved crop must contain four integer pixel coordinates.")

    coordinates = []
    for value in crop:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("Saved crop must contain four integer pixel coordinates.")
        try:
            numeric = float(value)
        except OverflowError as exc:
            raise ValueError("Saved crop must contain four integer pixel coordinates.") from exc
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError("Saved crop must contain four integer pixel coordinates.")
        coordinates.append(int(numeric))

    rect = CropRect(*coordinates)
    if not rect.is_valid():
        raise ValueError("Saved crop must have positive width and height.")
    return rect


# QSettings keys where older versions remembered colour correction across videos.
# They are no longer read or written, and are removed when the tab starts.
LEGACY_COLOUR_SETTINGS_KEYS = (
    "video/brightness",
    "video/contrast",
    "video/gamma",
    "video/rGain",
    "video/gGain",
    "video/bGain",
)


def _colour_slider_value(slider: QSlider, value: object, scale: int, default: int) -> int:
    """Convert a persisted colour setting to a slider position, or the default.

    Malformed, non-finite and out-of-range values fall back to the default
    instead of crashing or being silently clamped by the slider.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        number = float(value) * scale
    except (ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    position = int(number) if scale == 1 else round(number)
    return position if slider.minimum() <= position <= slider.maximum() else default


class VideoTab(QWidget):
    """
    Signals
    -------
    cropped_confirmed(list[np.ndarray], float, float, float, CropRect | None)
        Emitted when the user clicks "Apply Video Settings".
        Args: (frames, fps, start_s, end_s, crop_rect)
        frames are full-resolution BGR (cropped + colour-corrected).
    """

    # Args: (path, fps, start_s, end_s, crop_rect, colour_lut, rotation)
    # colour_lut is a np.ndarray LUT or None if identity
    cropped_confirmed = Signal(str, float, float, float, object, object, int)
    project_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cap = None
        self._current_frame_index: int = 0
        self._rotation: int = 0
        # Cached raw frame for live colour preview (avoid re-seeking on slider drag)
        self._raw_frame = None
        self._pending_pick: str | None = None
        self._settings = QSettings()

        self._build_ui()
        # Colour correction belongs to the current video (and saved projects) only.
        self._remove_legacy_colour_preferences()
        self._update_colour_note()
        self.start_input.textChanged.connect(self.project_changed)
        self.end_input.textChanged.connect(self.project_changed)
        self.start_input.textChanged.connect(self._update_selected_duration)
        self.end_input.textChanged.connect(self._update_selected_duration)
        for slider in (
            self._brightness_slider,
            self._contrast_slider,
            self._gamma_slider,
            self._r_slider,
            self._g_slider,
            self._b_slider,
        ):
            slider.valueChanged.connect(self.project_changed)
            slider.valueChanged.connect(self._update_colour_note)
        self.video_display.crop_confirmed.connect(self.project_changed)
        self.video_display.size_changed.connect(self._refresh_preview)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- Left panel ------------------------------------------------
        # Spare height is shared by capped gaps between rows and blocks (add_gap);
        # only what is left beyond those caps goes to the bottom stretch.
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 4, 0)
        left.setSpacing(ROW_SPACING)

        # Load button
        self.btn_load = QPushButton("Load Video")
        self.btn_load.clicked.connect(self._on_load)
        left.addWidget(self.btn_load)
        add_gap(left, GROUP_GAP)

        # Video settings group (clip, orientation and the video's info lines)
        settings_group = QGroupBox("Video Settings")
        settings_outer = QVBoxLayout()
        settings_outer.setSpacing(ROW_SPACING)
        settings_layout = QFormLayout()
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(ROW_SPACING)
        # With wide fonts a field moves below its label instead of widening the column.
        settings_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.start_input = QLineEdit("0")
        self.start_input.setValidator(c_locale_validator(QDoubleValidator(0, 99999, 3)))
        self.start_input.setFixedWidth(80)
        settings_layout.addRow("Start time (s):", self.start_input)

        self.end_input = QLineEdit()
        self.end_input.setValidator(c_locale_validator(QDoubleValidator(0, 99999, 3)))
        self.end_input.setFixedWidth(80)
        settings_layout.addRow("End time (s):", self.end_input)

        self.btn_rotate = QPushButton("Rotate 90°")
        self.btn_rotate.setToolTip(
            "Rotate clockwise in 90° steps. Apply Video Settings to use the orientation "
            "in the animated preview and exports."
        )
        self.btn_rotate.setEnabled(False)
        self.btn_rotate.setMinimumWidth(80)
        self.btn_rotate.clicked.connect(self._on_rotate_video)
        orientation_row = QHBoxLayout()
        orientation_row.addWidget(self.btn_rotate)
        self.lbl_rotation = QLabel("0°")
        self.lbl_rotation.setMinimumWidth(36)
        self.lbl_rotation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orientation_row.addWidget(self.lbl_rotation)
        settings_layout.addRow("Orientation:", orientation_row)
        settings_outer.addLayout(settings_layout)
        add_gap(settings_outer, ROW_GAP)

        # Info labels, as one closely spaced block inside the same group
        self.lbl_duration = QLabel("Full video duration: —")
        self.lbl_selected_duration = QLabel("Selected clip duration: —")
        self.lbl_fps = QLabel("FPS: —")
        self.lbl_crop = QLabel("Crop: none")
        # Wrapping labels never force the column wider than its fixed width.
        for lbl in (self.lbl_duration, self.lbl_selected_duration, self.lbl_fps, self.lbl_crop):
            lbl.setWordWrap(True)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        for lbl in (
            self.lbl_duration,
            self.lbl_selected_duration,
            self.lbl_fps,
            self.lbl_crop,
        ):
            info_layout.addWidget(lbl)
        settings_outer.addLayout(info_layout)

        settings_group.setLayout(settings_outer)
        left.addWidget(settings_group)
        add_gap(left, GROUP_GAP)

        # ---- Colour correction group -----------------------------------
        cc_group = QGroupBox("Colour Correction")
        cc_layout = QVBoxLayout()
        cc_layout.setSpacing(ROW_SPACING)

        self._brightness_slider, brightness_row = self._make_slider("Brightness", -100, 100, 0)
        self._contrast_slider, contrast_row = self._make_slider("Contrast", 50, 200, 100, scale=100)
        self._gamma_slider, gamma_row = self._make_slider("Gamma", 20, 300, 100, scale=100)
        # Per-channel gains for white balance (scale=100 → 0.0–2.0, default 1.0)
        self._r_slider, r_row = self._make_slider("R gain", 0, 200, 100, scale=100)
        self._g_slider, g_row = self._make_slider("G gain", 0, 200, 100, scale=100)
        self._b_slider, b_row = self._make_slider("B gain", 0, 200, 100, scale=100)

        for row in (brightness_row, contrast_row, gamma_row, r_row, g_row, b_row):
            cc_layout.addLayout(row)
            add_gap(cc_layout, ROW_GAP)

        # White / black balance pickers side by side
        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        self.btn_pick_white = QPushButton("Pick White")
        self.btn_pick_white.setToolTip("Click a pixel that should be white/neutral grey")
        self.btn_pick_white.setCheckable(True)
        self.btn_pick_white.clicked.connect(self._on_pick_white_toggled)
        self.btn_pick_black = QPushButton("Pick Black")
        self.btn_pick_black.setToolTip("Click a pixel that should be black")
        self.btn_pick_black.setCheckable(True)
        self.btn_pick_black.clicked.connect(self._on_pick_black_toggled)
        btn_reset_cc = QPushButton("Reset")
        btn_reset_cc.setToolTip("Restore the original colours for this video")
        btn_reset_cc.clicked.connect(self._reset_colour)
        # An explicit minimum lets the pair share the row even with wide fonts; at normal
        # sizes each button still gets its full text width.
        for button in (self.btn_pick_white, self.btn_pick_black):
            button.setMinimumWidth(60)
            pick_row.addWidget(button)
        cc_layout.addLayout(pick_row)
        add_gap(cc_layout, ROW_GAP)
        # Reset gets its own row so wide fonts never squeeze the three buttons.
        cc_layout.addWidget(btn_reset_cc, alignment=Qt.AlignmentFlag.AlignRight)

        # Colour correction applies to this video only; say so when it is active.
        self.lbl_colour_note = QLabel(
            "Colour correction is active for this video only (saved in projects). "
            "Click Reset for the original colours."
        )
        self.lbl_colour_note.setWordWrap(True)
        self.lbl_colour_note.setStyleSheet("color: #8a4b00;")
        self.lbl_colour_note.hide()
        cc_layout.addWidget(self.lbl_colour_note)

        cc_group.setLayout(cc_layout)
        left.addWidget(cc_group)
        add_gap(left, GROUP_GAP)

        # Clear crop / apply settings
        self.btn_clear_crop = QPushButton("Clear Crop")
        self.btn_clear_crop.clicked.connect(self._on_clear_crop)
        left.addWidget(self.btn_clear_crop)
        add_gap(left, ROW_GAP)

        self.btn_confirm = QPushButton("Apply Video Settings")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
            "QPushButton:disabled { background-color: #d6d6d6; color: #707070; }"
        )
        self.btn_confirm.setMinimumWidth(120)
        self.btn_confirm.clicked.connect(self._on_confirm)
        left.addWidget(self.btn_confirm)

        # Takes only the height left over once every capped gap is full.
        left.addStretch(1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        # Scroll the settings column so every control stays reachable on short
        # screens instead of forcing the window taller than the display.
        self.controls_scroll = ControlsScrollArea(left_widget, 320)
        root.addWidget(self.controls_scroll)

        # ---- Right panel -----------------------------------------------
        right = QVBoxLayout()

        # Video display (CropOverlay = QLabel with mouse crop support)
        self.video_display = CropOverlay()
        self.video_display.setText("Load a video to begin")
        self.video_display.setStyleSheet("background: black; color: #aaa;")
        self.video_display.crop_confirmed.connect(self._on_crop_confirmed)
        self.video_display.pixel_picked.connect(self._on_pixel_picked)
        right.addWidget(self.video_display, stretch=1)

        # Scrub slider
        slider_row = QHBoxLayout()
        self.lbl_time = QLabel("0.0 s")
        self.lbl_time.setFixedWidth(60)
        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setMinimum(0)
        self.scrub_slider.setMaximum(0)
        self.scrub_slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(QLabel("Frame:"))
        slider_row.addWidget(self.scrub_slider, stretch=1)
        slider_row.addWidget(self.lbl_time)
        right.addLayout(slider_row)

        root.addLayout(right, stretch=1)

    def _make_slider(
        self,
        label: str,
        min_val: int,
        max_val: int,
        default: int,
        scale: int = 1,
    ) -> tuple[QSlider, QHBoxLayout]:
        """
        Build one compact slider row: name, slider, current value side by side.

        scale: divide slider integer value by this to get the real parameter
               (e.g. scale=100 means slider 150 → 1.50).
        Returns (slider, layout).
        """
        layout = QHBoxLayout()
        layout.setSpacing(4)

        lbl = QLabel(label)
        lbl.setMinimumWidth(62)
        value_lbl = QLabel(self._fmt(default, scale))
        value_lbl.setFixedWidth(36)
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        slider = QSlider(Qt.Orientation.Horizontal)
        lbl.setBuddy(slider)
        slider.setAccessibleName(label)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(default)
        slider.setTickInterval((max_val - min_val) // 4)

        def _on_change(v: int, lbl=value_lbl, s=scale) -> None:
            lbl.setText(self._fmt(v, s))
            self._refresh_preview()

        slider.valueChanged.connect(_on_change)
        layout.addWidget(lbl)
        layout.addWidget(slider, stretch=1)
        layout.addWidget(value_lbl)

        return slider, layout

    @staticmethod
    def _fmt(value: int, scale: int) -> str:
        if scale == 1:
            return str(value)
        return f"{value / scale:.2f}"

    # ------------------------------------------------------------------
    # Colour correction helpers
    # ------------------------------------------------------------------

    def _brightness(self) -> int:
        return self._brightness_slider.value()

    def _contrast(self) -> float:
        return self._contrast_slider.value() / 100.0

    def _gamma(self) -> float:
        return self._gamma_slider.value() / 100.0

    def _r_gain(self) -> float:
        return self._r_slider.value() / 100.0

    def _g_gain(self) -> float:
        return self._g_slider.value() / 100.0

    def _b_gain(self) -> float:
        return self._b_slider.value() / 100.0

    def _reset_colour(self) -> None:
        for slider, default in (
            (self._brightness_slider, 0),
            (self._contrast_slider, 100),
            (self._gamma_slider, 100),
            (self._r_slider, 100),
            (self._g_slider, 100),
            (self._b_slider, 100),
        ):
            slider.blockSignals(True)
            slider.setValue(default)
            slider.blockSignals(False)
        self._refresh_preview()
        self._update_colour_note()

    def _colour_sliders(self) -> tuple[tuple[QSlider, int], ...]:
        """(slider, default position) for every colour-correction control."""
        return (
            (self._brightness_slider, 0),
            (self._contrast_slider, 100),
            (self._gamma_slider, 100),
            (self._r_slider, 100),
            (self._g_slider, 100),
            (self._b_slider, 100),
        )

    def _project_colour_fields(self) -> tuple[tuple[QSlider, str, int, int], ...]:
        """(slider, project key, value-to-position scale, default position)."""
        return (
            (self._brightness_slider, "brightness", 1, 0),
            (self._contrast_slider, "contrast", 100, 100),
            (self._gamma_slider, "gamma", 100, 100),
            (self._r_slider, "r_gain", 100, 100),
            (self._g_slider, "g_gain", 100, 100),
            (self._b_slider, "b_gain", 100, 100),
        )

    def _remove_legacy_colour_preferences(self) -> None:
        """Delete colour values that older versions kept in global preferences."""
        for key in LEGACY_COLOUR_SETTINGS_KEYS:
            self._settings.remove(key)

    def _set_default_colour(self) -> None:
        """Put every colour-correction control back to its default (new video, reset)."""
        for slider, default in self._colour_sliders():
            slider.setValue(default)
        self._update_colour_note()

    def _update_colour_note(self, _value: int | None = None) -> None:
        active = any(slider.value() != default for slider, default in self._colour_sliders())
        self.lbl_colour_note.setVisible(active)

    def apply_colour_settings(self, settings: dict, *, persist: bool = True) -> None:
        """Apply colour settings to the current video without changing clip or crop.

        Colour correction is never stored in global preferences; it is saved only
        with projects. ``persist`` is accepted for compatibility and ignored.
        """
        del persist
        if not isinstance(settings, dict):
            settings = {}
        for slider, key, scale, default in self._project_colour_fields():
            slider.setValue(_colour_slider_value(slider, settings.get(key), scale, default))
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Pick white / black balance
    # ------------------------------------------------------------------

    def _on_pick_white_toggled(self, checked: bool) -> None:
        if checked:
            self.btn_pick_black.setChecked(False)
            self.video_display.set_pick_mode(True)
            self._pending_pick = "white"
        else:
            self.video_display.set_pick_mode(False)
            self._pending_pick = None

    def _on_pick_black_toggled(self, checked: bool) -> None:
        if checked:
            self.btn_pick_white.setChecked(False)
            self.video_display.set_pick_mode(True)
            self._pending_pick = "black"
        else:
            self.video_display.set_pick_mode(False)
            self._pending_pick = None

    def _on_pixel_picked(self, vx: int, vy: int) -> None:
        """Sample the raw frame at (vx, vy) and apply white or black balance."""
        # Untoggle buttons — pick mode is now consumed
        self.btn_pick_white.setChecked(False)
        self.btn_pick_black.setChecked(False)

        if self._raw_frame is None:
            return

        # Clamp to frame bounds
        h, w = self._raw_frame.shape[:2]
        vx = max(0, min(vx, w - 1))
        vy = max(0, min(vy, h - 1))

        # cv2 stores BGR
        b, g, r = (int(self._raw_frame[vy, vx, c]) for c in range(3))

        if getattr(self, "_pending_pick", None) == "white":
            # Map sampled colour to neutral grey: gain = target / channel
            # Target = mean of channels (preserves overall brightness)
            target = (r + g + b) / 3.0
            r_gain = target / max(r, 1)
            g_gain = target / max(g, 1)
            b_gain = target / max(b, 1)
            # Clamp to slider range 0.0–2.0
            self._r_slider.setValue(int(min(max(r_gain, 0.0), 2.0) * 100))
            self._g_slider.setValue(int(min(max(g_gain, 0.0), 2.0) * 100))
            self._b_slider.setValue(int(min(max(b_gain, 0.0), 2.0) * 100))

        elif getattr(self, "_pending_pick", None) == "black":
            # Lift black point: set brightness to subtract the sampled dark level
            black_level = int((r + g + b) / 3.0)
            self._brightness_slider.setValue(-black_level)

        self._pending_pick = None
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Re-render the current frame with updated colour settings."""
        if self._raw_frame is None:
            return
        from video_data_sync.video_processing.colour import apply_correction

        corrected = apply_correction(
            self._raw_frame,
            brightness=self._brightness(),
            contrast=self._contrast(),
            gamma=self._gamma(),
            r_gain=self._r_gain(),
            g_gain=self._g_gain(),
            b_gain=self._b_gain(),
        )
        self._display_frame(corrected)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _update_selected_duration(self, _text: str | None = None) -> None:
        """Keep the temporal clip length visible while its boundaries are edited."""
        try:
            start_s = float(self.start_input.text())
            end_s = float(self.end_input.text())
        except ValueError:
            self.lbl_selected_duration.setText("Selected clip duration: —")
            return

        duration_s = end_s - start_s
        if not math.isfinite(duration_s) or duration_s < 0:
            self.lbl_selected_duration.setText("Selected clip duration: —")
            return
        self.lbl_selected_duration.setText(f"Selected clip duration: {duration_s:.1f} s")

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            str(self._settings.value("folders/video", "")),
            "Video files (*.mp4 *.avi *.mkv *.mov)",
        )
        if not path:
            return
        import os

        self._settings.setValue("folders/video", os.path.dirname(path))
        self._load_video(path)

    def _load_video(self, path: str) -> None:
        try:
            from video_data_sync.video_processing.capture import VideoCapture

            candidate = VideoCapture(path)
            if not math.isfinite(candidate.fps) or candidate.fps <= 0 or candidate.frame_count <= 0:
                candidate.release()
                raise OSError("Video has invalid frame rate or duration metadata.")
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        if self._cap is not None:
            self._cap.release()
        self._cap = candidate
        self._current_frame_index = 0
        # Colour correction never carries over to another (or a reopened) video.
        # Project loading applies its own values after this reset.
        self._raw_frame = None
        self._set_default_colour()
        self._rotation = 0
        self.lbl_rotation.setText("0°")
        self.btn_rotate.setToolTip(self._rotation_tooltip())

        self.scrub_slider.setMaximum(self._cap.frame_count - 1)
        self.scrub_slider.setValue(0)
        self.start_input.setText("0")
        self.end_input.setText(f"{self._cap.duration_s:.6f}")
        self.lbl_duration.setText(f"Full video duration: {self._cap.duration_s:.1f} s")
        self._update_selected_duration()
        self.lbl_fps.setText(f"FPS: {self._cap.fps:.2f}")
        self.video_display.set_video_dimensions(self._cap.width, self._cap.height)
        self.video_display.clear_crop()
        self.lbl_crop.setText("Crop: none (drag to select)")
        self.btn_rotate.setEnabled(True)
        self.btn_confirm.setEnabled(True)
        self._show_frame(0)

    def _on_slider_changed(self, value: int) -> None:
        self._current_frame_index = value
        self._show_frame(value)

    def _show_frame(self, frame_index: int) -> None:
        if self._cap is None:
            return
        frame = self._cap.read_frame(frame_index)
        if frame is None:
            return
        frame = rotate_frame(frame, self._rotation)
        self._raw_frame = frame
        self._refresh_preview()
        time_s = self._cap.time_at_frame(frame_index)
        self.lbl_time.setText(f"{time_s:.1f} s")

    def _display_frame(self, frame) -> None:
        """Convert a BGR frame to a pixmap and show it in the overlay."""
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimg).scaled(
            self.video_display.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_display.setPixmap(pixmap)

    def _on_crop_confirmed(self, crop: CropRect) -> None:
        x1, y1, x2, y2 = crop.to_tuple()
        self.lbl_crop.setText(f"Crop: ({x1},{y1}) → ({x2},{y2})")

    def _on_clear_crop(self) -> None:
        self.video_display.clear_crop()
        self.lbl_crop.setText("Crop: none")
        self.project_changed.emit()

    def _on_confirm(self) -> bool:
        if self._cap is None:
            return False

        try:
            start_s = float(self.start_input.text() or 0)
            end_s = float(self.end_input.text() or self._cap.duration_s)
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Start and end times must be numbers.")
            return False

        if start_s >= end_s:
            QMessageBox.warning(self, "Invalid input", "End time must be greater than start time.")
            return False
        if not math.isfinite(start_s) or not math.isfinite(end_s) or start_s < 0:
            QMessageBox.warning(
                self, "Invalid input", "Clip times must be finite and non-negative."
            )
            return False
        if end_s > self._cap.duration_s + 0.5 / self._cap.fps:
            QMessageBox.warning(self, "Invalid input", "Clip end exceeds the video duration.")
            return False

        crop = self.video_display.crop
        if crop is not None:
            width, height = rotated_dimensions(self._cap.width, self._cap.height, self._rotation)
            x1, y1, x2, y2 = crop.to_tuple()
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                QMessageBox.warning(self, "Invalid crop", "Crop must lie inside the video frame.")
                return False

        # Build per-channel LUT; build_lut returns None if all params are identity
        from video_data_sync.video_processing.colour import build_lut

        colour_lut = build_lut(
            self._brightness(),
            self._contrast(),
            self._gamma(),
            self._r_gain(),
            self._g_gain(),
            self._b_gain(),
        )

        self.cropped_confirmed.emit(
            self._cap.path, self._cap.fps, start_s, end_s, crop, colour_lut, self._rotation
        )
        return True

    def _rotation_tooltip(self) -> str:
        return (
            f"Current orientation: {self._rotation}° clockwise. "
            "Click to rotate clockwise by another 90°."
        )

    def _on_rotate_video(self) -> None:
        if self._cap is None:
            return
        self._set_rotation((self._rotation + 90) % 360, mark_dirty=True)

    def _set_rotation(self, rotation: int, *, mark_dirty: bool) -> None:
        """Update the displayed orientation and keep any crop aligned with it."""
        rotation = validate_rotation(rotation)
        cap = self._cap
        if cap is None or rotation == self._rotation:
            return

        width, height = rotated_dimensions(cap.width, cap.height, self._rotation)
        crop = self.video_display.crop
        turns = ((rotation - self._rotation) % 360) // 90
        if crop is not None and crop.is_valid():
            for _ in range(turns):
                crop = CropRect(*rotate_crop(crop.to_tuple(), width, height, 90))
                width, height = height, width

        self._rotation = rotation
        self.lbl_rotation.setText(f"{rotation}°")
        width, height = rotated_dimensions(cap.width, cap.height, rotation)
        self.video_display.set_video_dimensions(width, height)
        self.btn_rotate.setToolTip(self._rotation_tooltip())
        if crop is not None and crop.is_valid():
            self._on_crop_confirmed(crop)
        else:
            crop = None
            self.lbl_crop.setText("Crop: none (drag to select)")
        self._show_frame(self._current_frame_index)
        self.video_display.set_crop(crop)
        if mark_dirty:
            self.project_changed.emit()

    def load_project_video(
        self, path: str, start_s: float, end_s: float, crop=None, colour=None, rotation=0
    ) -> bool:
        self._load_video(path)
        if self._cap is None or self._cap.path != path:
            return False
        self._set_rotation(rotation, mark_dirty=False)
        self.start_input.setText(str(start_s))
        self.end_input.setText(str(end_s))
        # _load_video reset colour to defaults; the project's values are applied after it.
        colour = colour if isinstance(colour, dict) else {}
        for slider, key, scale, default in self._project_colour_fields():
            slider.setValue(_colour_slider_value(slider, colour.get(key), scale, default))
        if crop is not None:
            rect = _project_crop_rect(crop)
            self.video_display.set_crop(rect)
            self._on_crop_confirmed(rect)
        return self._on_confirm()

    @property
    def rotation(self) -> int:
        """Current clockwise video orientation in degrees."""
        return self._rotation

    def release_media(self) -> None:
        """Release the open video handle without touching any other UI state.

        Used on an accepted application close: child tabs never receive a
        ``closeEvent`` of their own, so the main window releases native decoder
        handles explicitly instead of leaving them to interpreter teardown.
        """
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        self._raw_frame = None

    def reset_state(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        self._raw_frame = None
        self._rotation = 0
        self.lbl_rotation.setText("0°")
        self.btn_rotate.setEnabled(False)
        self.btn_rotate.setToolTip(self._rotation_tooltip())
        self.video_display.set_video_dimensions(1, 1)
        self.video_display.clear_crop()
        self.video_display.clear()
        self.start_input.setText("0")
        self.end_input.clear()
        self.lbl_duration.setText("Full video duration: —")
        self.lbl_selected_duration.setText("Selected clip duration: —")
        self.btn_confirm.setEnabled(False)
        self._set_default_colour()
