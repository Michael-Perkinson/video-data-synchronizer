# ruff: noqa: E501
"""
Animated Plot Tab.

Shows the cropped video and the data trace side by side, playing in sync.
The data trace grows from left to right as the video plays.

Video frames are decoded on-demand via seek — no frames are held in RAM.

Requires that the user has:
  1. Applied video settings (VideoTab emits cropped_confirmed)
  2. Loaded data (DataTab emits data_loaded)

State is pushed in via update_video() and update_data().
"""

from __future__ import annotations

import json
from typing import Any, cast

import numpy as np
import pandas as pd
from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QSplitterHandle,
    QVBoxLayout,
    QWidget,
)

from video_data_sync.data.transforms import (
    normalise_window_values,
    transform_values,
    window_minimum,
)
from video_data_sync.plotting.composition import ANCHORS, named_anchor, shared_y_unit_for_columns
from video_data_sync.plotting.plot_widget import AnimatedPlotWidget, Trace
from video_data_sync.video_processing.capture import VideoCapture
from video_data_sync.video_processing.colour import apply_lut
from video_data_sync.video_processing.cropper import CropRect
from video_data_sync.video_processing.flicker import (
    compute_reference_brightness,
    correct_frame_brightness,
)
from video_data_sync.video_processing.rotation import (
    rotate_frame,
    rotated_dimensions,
    validate_rotation,
)

VIDEO_WIDTH_FRACTION = 0.60
MIN_VIDEO_WIDTH_FRACTION = 0.25
MAX_VIDEO_WIDTH_FRACTION = 0.68


class AspectRatioLabel(QLabel):
    """Video surface that always fits the source frame without cropping it."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._source_pixmap = QPixmap()

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._fit_pixmap()

    def clear(self) -> None:
        self._source_pixmap = QPixmap()
        super().clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_pixmap()

    def _fit_pixmap(self) -> None:
        if self._source_pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        super().setPixmap(
            self._source_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class SplitterHandle(QSplitterHandle):
    """Visible grip that makes the adjustable preview split discoverable."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setToolTip("Drag to resize the video and plot")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#d3d7dc"))
        painter.setBrush(QColor("#66707a"))
        painter.setPen(Qt.PenStyle.NoPen)
        centre = self.rect().center()
        for offset in (-6, 0, 6):
            if self.orientation() == Qt.Orientation.Horizontal:
                painter.drawEllipse(centre.x() - 2, centre.y() + offset - 2, 4, 4)
            else:
                painter.drawEllipse(centre.x() + offset - 2, centre.y() - 2, 4, 4)


class PreviewSplitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:
        return SplitterHandle(self.orientation(), self)


class ControlsBar(QWidget):
    """Playback and export controls in one row, or two rows on narrow windows.

    When the window is wide enough the bar is the same single row as before, so
    the preview area (and the default video/plot split) does not change. Only
    when the single row cannot fit does the export group move below the
    playback group. Every control stays visible, and the bar never forces the
    window wider than a small screen.
    """

    SPACING = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lead = QWidget(self)
        # The playback group keeps its natural width in both arrangements.
        self.lead.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.lead_layout = QHBoxLayout(self.lead)
        self.lead_layout.setContentsMargins(0, 0, 0, 0)
        self.tail = QWidget(self)
        self.tail_layout = QHBoxLayout(self.tail)
        self.tail_layout.setContentsMargins(0, 0, 0, 0)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(self.SPACING)
        self._box.addWidget(self.lead)
        self._box.addWidget(self.tail, stretch=1)

    @property
    def wrapped(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def one_row_width(self) -> int:
        """Smallest width at which both groups fit side by side."""
        return self.lead.sizeHint().width() + self.SPACING + self.tail.minimumSizeHint().width()

    def minimumSizeHint(self) -> QSize:
        # Report the wrapped minimum width so the window can shrink; the
        # resize handler then moves the export group to a second row.
        current = super().minimumSizeHint()
        width = max(self.lead.sizeHint().width(), self.tail.minimumSizeHint().width())
        return QSize(width, current.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < self.one_row_width()
            else QBoxLayout.Direction.LeftToRight
        )
        if self._box.direction() != direction:
            self._box.setDirection(direction)


def _sorted_trace_arrays(
    df: pd.DataFrame, time_col: str, columns: str | list[str]
) -> tuple[np.ndarray, Any]:
    times = df[time_col].to_numpy(dtype=float, copy=True)
    finite = np.isfinite(times)
    order = np.argsort(times[finite], kind="stable")
    names = [columns] if isinstance(columns, str) else columns
    result = {col: df[col].to_numpy(dtype=float, copy=True)[finite][order] for col in names}
    return times[finite][order], result[columns] if isinstance(columns, str) else result


def _finite_number(value: object) -> float | None:
    """Parse a persisted number, rejecting bools, non-numeric types and NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if np.isfinite(number) else None


def _number_in(low: float, high: float) -> Any:
    def check(value: object) -> bool:
        number = _finite_number(value)
        return number is not None and low <= number <= high

    return check


# Persisted composition values that would crash the preview or the Advanced
# dialog, keyed to the check a valid value must pass. Ranges match the preset
# validator so every valid preset/project value is kept unchanged.
_COMPOSITION_CHECKS: dict[str, Any] = {
    "scale_font_size": _number_in(6, 72),
    "scale_line_width": _number_in(0.1, 20),
    "time_scale_s": _number_in(0, 1e12),
    "signal_scale": _number_in(0, 1e12),
    "max_change_percent": _number_in(0.01, 100.0),
    "anchor": lambda v: isinstance(v, str) and v in ANCHORS,
}


def _sanitize_composition(settings: dict, defaults: dict[str, object]) -> dict:
    """Replace malformed project/QSettings composition values with defaults."""
    for key, is_valid in _COMPOSITION_CHECKS.items():
        if key in settings and not is_valid(settings[key]):
            settings[key] = defaults[key]
    return settings


class AnimatedTab(QWidget):
    """
    Signals
    -------
    export_requested()
        Emitted when the user clicks Export — main_window handles it.
    """

    export_requested = Signal()
    batch_export_requested = Signal()
    composition_changed = Signal()
    COMPOSITION_DEFAULTS: dict[str, object] = {
        "scale_font_size": 16,
        "scale_line_width": 2.8,
        "show_time_scale": True,
        "show_signal_scale": True,
        "time_scale_s": 0.0,
        "signal_scale": 0.0,
        "max_change_enabled": False,
        "max_change_percent": 70.0,
        "time_scale_label": "",
        "signal_scale_label": "",
        "anchor": "Bottom left",
        "background": "#ffffff",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings()
        self._suspend_preferences = True

        # Video state — set by update_video()
        self._video_path: str | None = None
        self._fps: float = 30.0
        self._start_s: float = 0.0
        self._end_s: float = 0.0
        self._crop: CropRect | None = None
        self._colour_lut: np.ndarray | None = None
        self._rotation: int = 0
        self._cap: VideoCapture | None = None
        self._total_frames: int = 0
        self._ref_brightness: float | None = None  # target luminance for de-flicker

        # Data state — set by update_data()
        self._df: pd.DataFrame = pd.DataFrame()
        self._time_col: str | None = None
        self._data_col: str = ""
        self._data_col2: str = ""  # optional 2nd overlaid signal
        self._legend_labels: dict[str, str] = {}
        self._trace_color: str = "#000000"
        self._trace_color2: str = "#d62728"
        self._linewidth: float = 1.5
        self._y_min: float | None = None
        self._y_max: float | None = None
        self._offset_s: float = 0.0
        self._trace_baselines: dict[str, float] = {}
        self._trace_transform = "subtract_first"
        self._trace_times = np.array([], dtype=float)
        self._trace_values: dict[str, np.ndarray] = {}
        self._window_start_idx = 0
        self._plot_baseline: float = 0.0
        self._window_y_min: float = 0.0
        self._window_display_range: tuple[float, float] | None = None

        # Displayed frame dimensions (after crop) — drive the side-by-side layout
        self._frame_w: int = 0
        self._frame_h: int = 0
        self._preview_w: int = 0
        self._preview_plot_w: int = 0
        self._preview_h: int = 0
        self._preview_plot_h: int = 0

        # Playback state
        self._plot_ready: bool = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._frame_index: int = 0
        self._next_abs: int = 0  # next absolute frame a sequential read() returns
        self._default_split_pending = True
        self._setting_default_split = False

        self._build_ui()
        self._legacy_max_change: float | None = None
        self._composition = dict(self.COMPOSITION_DEFAULTS)
        saved = self._settings.value("composition/advanced")
        if saved:
            try:
                saved_composition = json.loads(saved)
                if isinstance(saved_composition, dict):
                    legacy_change = saved_composition.pop("max_change", None)
                    if legacy_change is not None and "max_change_percent" not in saved_composition:
                        try:
                            self._legacy_max_change = float(str(legacy_change))
                        except (TypeError, ValueError):
                            self._legacy_max_change = None
                    self._composition.update(
                        _sanitize_composition(saved_composition, self.COMPOSITION_DEFAULTS)
                    )
                    self._composition.pop("trace_font_size", None)
            except (TypeError, ValueError):
                pass
        # Plot background is not user-configurable; ignore any custom value
        # left over from settings saved before this was locked to white.
        self._composition["background"] = self.COMPOSITION_DEFAULTS["background"]
        self._apply_export_preferences()
        self._suspend_preferences = False
        self._position_play_hint()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- Controls row -----------------------------------------------
        # One row on normal windows; wraps to two rows on narrow screens.
        self.controls_bar = ControlsBar(self)

        self.btn_play = QPushButton("Play")
        self.btn_play.setFixedWidth(70)
        self.btn_play.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold;")
        self.btn_play.clicked.connect(self._on_play)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setFixedWidth(70)
        self.btn_pause.setStyleSheet("background-color: #616161; color: white; font-weight: bold;")
        self.btn_pause.clicked.connect(self._on_pause)

        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setFixedWidth(70)
        self.btn_reset.clicked.connect(self._on_reset)

        speed_label = QLabel("Preview speed:")
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25x", "0.5x", "1x", "2x", "4x", "8x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.setEditable(True)  # allow typing a custom speed
        self.speed_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.speed_combo.setToolTip(
            "Controls preview playback and the speed used by Export Video. "
            "Export Speeds uses its own selection."
        )
        self.speed_combo.setFixedWidth(70)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
        speed_editor = self.speed_combo.lineEdit()
        if speed_editor is not None:
            speed_editor.editingFinished.connect(self._commit_speed_text)

        self.chk_deflicker = QCheckBox("De-flicker")
        self.chk_deflicker.setToolTip(
            "Smooth out brightness flicker by normalising each frame's luminance."
        )
        self.chk_deflicker.toggled.connect(self._on_deflicker_toggled)

        self.btn_export = QPushButton("Export Video")
        self.btn_export.setToolTip("Export one video at the selected Preview speed.")
        self.btn_export.setStyleSheet("background-color: #9a4e00; color: white; font-weight: bold;")
        self.btn_export.clicked.connect(self.export_requested.emit)
        self._update_export_button_label()
        self.btn_export_multiple = QPushButton("Export Speeds…")
        self.btn_export_multiple.setToolTip(
            "Choose export speeds independently of Preview speed. "
            "The selected speeds run sequentially."
        )
        self.btn_export_multiple.clicked.connect(self.batch_export_requested.emit)
        self.btn_advanced = QPushButton("Advanced…")
        self.btn_advanced.clicked.connect(self._show_composition_dialog)
        self.layout_combo = QComboBox()
        # Keep the legacy object for project/settings compatibility, but do
        # not expose the stacked layout as a user option.
        self.layout_combo.addItem("Side by side")

        self.lbl_status = QLabel("Load video and data to begin.")
        # Long guidance (e.g. no data overlap) wraps rather than widening the window.
        self.lbl_status.setWordWrap(True)

        playback_row = self.controls_bar.lead_layout
        playback_row.addWidget(self.btn_play)
        playback_row.addWidget(self.btn_pause)
        playback_row.addWidget(self.btn_reset)
        playback_row.addSpacing(16)
        playback_row.addWidget(speed_label)
        playback_row.addWidget(self.speed_combo)
        playback_row.addSpacing(16)
        playback_row.addWidget(self.chk_deflicker)
        playback_row.addSpacing(16)
        playback_row.addWidget(self.btn_advanced)
        export_row = self.controls_bar.tail_layout
        # The status label owns the free space so it only wraps when it must.
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        export_row.addWidget(self.lbl_status, stretch=1)
        self.btn_export_multiple.setStyleSheet(
            "background-color: #9a4e00; color: white; font-weight: bold;"
        )
        export_row.addWidget(self.btn_export)
        export_row.addWidget(self.btn_export_multiple)
        root.addWidget(self.controls_bar)

        # ---- Main display area ------------------------------------------
        # Video and plot sit side by side at the SAME height, both sized to fit
        # the window (see _relayout_display). The container lets us measure the
        # available area; stretches keep the pair centred.
        self.display_container = PreviewSplitter(Qt.Orientation.Horizontal)
        self.display_container.setHandleWidth(10)

        self.video_label = AspectRatioLabel("Video will appear here")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.video_label.setStyleSheet("color: #888; background: white;")
        self.video_label.setMinimumSize(1, 1)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.anim_plot = AnimatedPlotWidget()
        self.anim_plot.setMinimumSize(1, 1)
        self.anim_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Preview-only hint: the trace grows during playback, so the pane is
        # intentionally empty at frame 0 (matching the exported first frame).
        self.lbl_plot_hint = QLabel("Press Play to draw the trace", self.anim_plot)
        self.lbl_plot_hint.setStyleSheet(
            "color: #444; background: rgba(255, 255, 255, 220);"
            " border: 1px solid #c8c8c8; padding: 4px 8px;"
        )
        self.lbl_plot_hint.adjustSize()
        self.lbl_plot_hint.move(8, 8)
        self.lbl_plot_hint.hide()

        self.display_container.addWidget(self.video_label)
        self.display_container.addWidget(self.anim_plot)
        self.display_container.setChildrenCollapsible(False)
        self.display_container.splitterMoved.connect(self._on_splitter_moved)
        self._set_default_split()

        root.addWidget(self.display_container, stretch=1)

    # ------------------------------------------------------------------
    def _on_orientation_changed(self, index: int) -> None:
        self.display_container.setOrientation(
            Qt.Orientation.Vertical if index else Qt.Orientation.Horizontal
        )
        self.video_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
            if index
            else Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._default_split_pending = True
        self._set_default_split()
        if not self._suspend_preferences:
            self._settings.setValue("composition/layout", index)
        self.composition_changed.emit()

    def _set_default_split(self) -> None:
        """Fit the video without letting its aspect ratio starve the plot pane."""
        raw_extent = (
            self.display_container.height()
            if self.display_container.orientation() == Qt.Orientation.Vertical
            else self.display_container.width()
        )
        raw_extent -= self.display_container.handleWidth()
        extent = max(100, raw_extent)
        fraction = VIDEO_WIDTH_FRACTION
        has_displayed_video = (
            self._frame_w > 0
            and self._frame_h > 0
            and self.display_container.width() > 0
            and self.display_container.height() > 0
        )
        if has_displayed_video:
            if self.display_container.orientation() == Qt.Orientation.Horizontal:
                natural_video_extent = (
                    self.display_container.height() * self._frame_w / self._frame_h
                )
            else:
                natural_video_extent = (
                    self.display_container.width() * self._frame_h / self._frame_w
                )
            fraction = natural_video_extent / max(1, raw_extent)
            fraction = min(max(fraction, MIN_VIDEO_WIDTH_FRACTION), MAX_VIDEO_WIDTH_FRACTION)
        # Keep recalculating while this is the automatic layout. A window
        # resize can change the splitter's absolute sizes; only a real user
        # drag should release it from the aspect-ratio default.
        self._default_split_pending = True
        self._setting_default_split = True
        try:
            self.display_container.setSizes(
                [round(extent * fraction), round(extent * (1 - fraction))]
            )
        finally:
            self._setting_default_split = False

    def _restore_split(self, sizes: object) -> None:
        """Keep saved pane proportions, or fall back to the aspect-ratio default.

        Splits saved under the old 0.85 cap that leave the plot pane narrower
        than the balanced limit are migrated to the default instead.
        """
        self._default_split_pending = True
        if not isinstance(sizes, (list, tuple)) or len(sizes) != 2:
            return
        try:
            values = [int(float(str(value))) for value in sizes]
        except (TypeError, ValueError, OverflowError):
            return
        if not all(1 <= value <= 1_000_000 for value in values):
            return
        if values[0] / sum(values) > MAX_VIDEO_WIDTH_FRACTION:
            return
        self.display_container.setSizes(values)
        self._default_split_pending = False

    def _position_play_hint(self) -> None:
        anchor_x, anchor_y = named_anchor(str(self._composition["anchor"]))
        hint = self.lbl_plot_hint
        hint.adjustSize()
        x = 8 if anchor_x < 0.5 else max(8, self.anim_plot.width() - hint.width() - 8)
        y = 8 if anchor_y <= 0.65 else max(8, self.anim_plot.height() - hint.height() - 8)
        hint.move(x, y)

    def _on_splitter_moved(self, *_args) -> None:
        """Remember and constrain the user-selected video/plot proportions."""
        if not self._setting_default_split:
            self._default_split_pending = False
        sizes = self.display_container.sizes()
        total = sum(sizes)
        if total and sizes[0] / total > MAX_VIDEO_WIDTH_FRACTION:
            video_size = round(total * MAX_VIDEO_WIDTH_FRACTION)
            self._setting_default_split = True
            try:
                self.display_container.setSizes([video_size, total - video_size])
            finally:
                self._setting_default_split = False
        self._position_play_hint()
        self.composition_changed.emit()

    @property
    def composition_settings(self) -> dict:
        settings = dict(self._composition)
        if self._legacy_max_change is not None:
            settings.pop("max_change_percent", None)
            settings["max_change"] = self._legacy_max_change
        return settings

    def set_composition_settings(self, settings: dict) -> None:
        settings = dict(settings)
        settings.pop("trace_font_size", None)
        legacy_change = settings.pop("max_change", None)
        if legacy_change is not None and "max_change_percent" not in settings:
            try:
                self._legacy_max_change = float(str(legacy_change))
            except (TypeError, ValueError):
                self._legacy_max_change = None
        elif "max_change_percent" in settings:
            self._legacy_max_change = None
        self._composition.update(_sanitize_composition(settings, self.COMPOSITION_DEFAULTS))
        self._position_play_hint()
        # Plot background is not user-configurable; ignore any custom value
        # from a project or preset saved before this was locked to white.
        self._composition["background"] = self.COMPOSITION_DEFAULTS["background"]
        self._setup_plot_if_ready()
        self.composition_changed.emit()

    def _reset_composition_defaults(self) -> None:
        self._legacy_max_change = None
        self._composition = dict(self.COMPOSITION_DEFAULTS)
        QSettings().setValue("composition/advanced", json.dumps(self._composition))
        self._setup_plot_if_ready()
        self.composition_changed.emit()

    def _show_composition_dialog(self) -> None:
        dialog = QDialog(self)
        form = QFormLayout(dialog)
        controls: dict[str, QWidget] = {}
        font = QSpinBox()
        font.setRange(6, 72)
        font.setValue(round(float(str(self._composition["scale_font_size"]))))
        controls["scale_font_size"] = font
        form.addRow("Scale-bar font size", font)
        line = QDoubleSpinBox()
        line.setRange(0.2, 10)
        line.setValue(float(str(self._composition["scale_line_width"])))
        controls["scale_line_width"] = line
        form.addRow("Scale-bar line width", line)
        for key, label in (
            ("show_time_scale", "Show time scale"),
            ("show_signal_scale", "Show signal scale"),
        ):
            check = QCheckBox()
            check.setChecked(bool(self._composition[key]))
            controls[key] = check
            form.addRow(label, check)
        max_change_enabled = QCheckBox("Limit the plotted signal range")
        max_change_enabled.setChecked(bool(self._composition["max_change_enabled"]))
        max_change_enabled.setToolTip(
            "Start at the lowest signal value in the current video window. The percentage "
            "below sets how much of the full low-to-high range is shown above it; higher "
            "values are clipped. This overrides fixed Y limits from the Data Plot tab. "
            "Automatic scaling is the default."
        )
        controls["max_change_enabled"] = max_change_enabled
        max_change_percent = QDoubleSpinBox()
        max_change_percent.setRange(0.01, 100.0)
        max_change_percent.setDecimals(2)
        max_change_percent.setSingleStep(1.0)
        max_change_percent.setValue(float(str(self._composition["max_change_percent"])))
        max_change_percent.setSuffix("%")
        max_change_percent.setFixedWidth(150)
        max_change_percent.setEnabled(max_change_enabled.isChecked())
        max_change_percent.setToolTip(
            "Choose how much of the signal's full range to show, starting from its lowest "
            "value. The limit updates automatically when the signal range changes."
        )
        max_change_enabled.toggled.connect(max_change_percent.setEnabled)
        controls["max_change_percent"] = max_change_percent
        range_summary = QLabel(self._max_change_range_summary(max_change_percent.value()))
        range_summary.setWordWrap(True)
        range_percent_edited = False

        def update_range_summary(value: float) -> None:
            nonlocal range_percent_edited
            range_percent_edited = True
            range_summary.setText(self._max_change_range_summary(value))

        max_change_percent.valueChanged.connect(update_range_summary)
        for key, label in (
            ("time_scale_s", "Time scale (s; 0 = Auto)"),
            ("signal_scale", "Signal scale (0 = Auto)"),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(0, 999999)
            spin.setValue(float(str(self._composition[key])))
            controls[key] = spin
            form.addRow(label, spin)
        for key, label, placeholder in (
            ("time_scale_label", "Time scale unit/label", "Auto (s or min)"),
            ("signal_scale_label", "Signal scale unit/label", "Auto from column name"),
        ):
            edit = QLineEdit(str(self._composition.get(key, "")))
            edit.setMaxLength(40)
            edit.setPlaceholderText(placeholder)
            edit.setToolTip(
                "Optional text shown after the automatically calculated numeric scale length."
            )
            controls[key] = edit
            form.addRow(label, edit)
        anchor = QComboBox()
        anchor.addItems(["Bottom right", "Bottom left", "Top right", "Top left"])
        anchor.setCurrentText(str(self._composition["anchor"]))
        controls["anchor"] = anchor
        form.addRow("Scale-bar anchor", anchor)
        range_group = QGroupBox("Graph range")
        range_form = QFormLayout(range_group)
        range_form.addRow(max_change_enabled)
        range_form.addRow("Show this much of the full range", max_change_percent)
        range_form.addRow(range_summary)
        form.addRow(range_group)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.RestoreDefaults
            | QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )

        def restore_defaults() -> None:
            nonlocal range_percent_edited
            range_percent_edited = True
            defaults = self.COMPOSITION_DEFAULTS
            font.setValue(int(str(defaults["scale_font_size"])))
            line.setValue(float(str(defaults["scale_line_width"])))
            for key in ("show_time_scale", "show_signal_scale"):
                check = controls[key]
                assert isinstance(check, QCheckBox)
                check.setChecked(bool(defaults[key]))
            for key in ("time_scale_s", "signal_scale", "max_change_percent"):
                spin = controls[key]
                assert isinstance(spin, QDoubleSpinBox)
                spin.setValue(float(str(defaults[key])))
            max_change_check = controls["max_change_enabled"]
            max_change_spin = controls["max_change_percent"]
            assert isinstance(max_change_check, QCheckBox)
            assert isinstance(max_change_spin, QDoubleSpinBox)
            max_change_check.setChecked(bool(defaults["max_change_enabled"]))
            max_change_spin.setEnabled(bool(defaults["max_change_enabled"]))
            for key in ("time_scale_label", "signal_scale_label"):
                edit = controls[key]
                assert isinstance(edit, QLineEdit)
                edit.setText(str(defaults[key]))
            anchor.setCurrentText(str(defaults["anchor"]))

        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            restore_defaults
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if range_percent_edited:
            self._legacy_max_change = None
        for key, control in controls.items():
            if isinstance(control, QCheckBox):
                self._composition[key] = control.isChecked()
            elif isinstance(control, QComboBox):
                self._composition[key] = control.currentText()
            elif isinstance(control, QSpinBox | QDoubleSpinBox):
                self._composition[key] = control.value()
            elif isinstance(control, QLineEdit):
                self._composition[key] = control.text().strip()
        saved_composition = dict(self._composition)
        if self._legacy_max_change is not None:
            saved_composition.pop("max_change_percent", None)
            saved_composition["max_change"] = self._legacy_max_change
        QSettings().setValue("composition/advanced", json.dumps(saved_composition))
        self._position_play_hint()
        self._setup_plot_if_ready()
        self.composition_changed.emit()

    # Public API — called by main_window when other tabs emit signals
    # ------------------------------------------------------------------

    def update_video(
        self,
        path: str,
        fps: float,
        start_s: float,
        end_s: float,
        crop: CropRect | None,
        colour_lut: np.ndarray | None,
        rotation: int = 0,
    ) -> None:
        """Receive video parameters from VideoTab. Opens a VideoCapture for seeking."""
        self._stop_playback()
        self._release_cap()

        self._video_path = path
        self._fps = fps
        self._start_s = start_s
        self._end_s = end_s
        self._crop = crop
        self._colour_lut = colour_lut
        self._rotation = validate_rotation(rotation)

        try:
            cap = VideoCapture(path)
        except OSError:
            self.lbl_status.setText("Error: could not open video.")
            return
        self._cap = cap

        duration = end_s - start_s
        self._total_frames = max(1, int(np.ceil(duration * fps)))
        self._frame_index = 0

        # Record displayed frame dimensions (after crop) for the layout.
        if crop is not None and crop.is_valid():
            x1, y1, x2, y2 = crop.to_tuple()
            self._frame_w, self._frame_h = x2 - x1, y2 - y1
        else:
            self._frame_w, self._frame_h = rotated_dimensions(cap.width, cap.height, self._rotation)
        # Keep a dragged or restored split; otherwise fit the new aspect ratio.
        self._relayout_display()
        if self._default_split_pending:
            self._set_default_split()

        # Sample a reference luminance for the optional de-flicker pass.
        crop_tuple = crop.to_tuple() if (crop is not None and crop.is_valid()) else None

        def read_rotated_frame(frame_index: int):
            frame = cap.read_frame(frame_index)
            return rotate_frame(frame, self._rotation) if frame is not None else None

        self._ref_brightness = compute_reference_brightness(
            read_rotated_frame,
            cap.frame_index_at_time(start_s),
            cap.frame_index_at_time(end_s),
            crop=crop_tuple,
        )

        self._show_frame(0)
        self._setup_plot_if_ready()
        self.lbl_status.setText(f"Video ready — {duration:.1f} s ({self._total_frames} frames).")

    def update_data(
        self,
        df: pd.DataFrame,
        time_col: str,
        data_col: str,
        color: str,
        y_min: float | None,
        y_max: float | None,
        linewidth: float = 1.5,
        offset_s: float = 0.0,
        data_col2: str = "",
        color2: str = "#d62728",
        trace_transform: str = "subtract_first",
        legend_labels: dict[str, str] | None = None,
    ) -> None:
        """Receive updated data state from DataTab. data_col2 is the optional overlay."""
        self._stop_playback()
        self._df = df
        self._time_col = time_col
        self._data_col = data_col
        self._data_col2 = data_col2
        self._legend_labels = {
            str(column): str(label).strip()[:128]
            for column, label in (legend_labels or {}).items()
            if str(label).strip()
        }
        self._trace_color = color
        self._trace_color2 = color2
        self._y_min = y_min
        self._y_max = y_max
        self._linewidth = linewidth
        self._offset_s = offset_s
        self._trace_transform = trace_transform
        self._frame_index = 0
        self._trace_baselines = {}
        self._rebuild_trace_cache()
        self._setup_plot_if_ready()

    def _rebuild_trace_cache(self) -> None:
        self._trace_times = np.array([], dtype=float)
        self._trace_values = {}
        self._window_start_idx = 0
        if self._df.empty or not self._time_col or self._time_col not in self._df:
            return
        cols = self._overlay_cols()
        if not cols or any(col not in self._df for col in cols):
            return
        self._trace_times, self._trace_values = _sorted_trace_arrays(self._df, self._time_col, cols)

    def _overlay_cols(self) -> list[str]:
        """Columns to animate: the primary, plus the 2nd if set and present."""
        cols = [self._data_col]
        if self._data_col2 and self._data_col2 in self._df.columns:
            cols.append(self._data_col2)
        return cols

    @staticmethod
    def _first_finite(values) -> float:
        arr = np.asarray(values, dtype=float)
        finite = arr[np.isfinite(arr)]
        return float(finite[0]) if finite.size else 0.0

    def _delta_from_start(self, col: str, values) -> np.ndarray:
        return transform_values(values, self._trace_transform) - self._plot_baseline

    def _setup_plot_if_ready(self) -> None:
        """Initialise the animated plot axes if we have both video and data."""
        self._window_display_range = None
        self.lbl_plot_hint.hide()
        if self._df.empty or self._time_col is None or not self._data_col:
            return
        if self._cap is None:
            return
        if self._data_col not in self._df.columns:
            return

        self._plot_ready = False
        data_t_min = float(self._df[self._time_col].min())
        data_t_max = float(self._df[self._time_col].max())

        video_win_start = self._start_s + self._offset_s
        video_win_end = self._end_s + self._offset_s

        mask = (self._df[self._time_col] >= video_win_start) & (
            self._df[self._time_col] <= video_win_end
        )
        window = self._df[mask]
        self._window_start_idx = int(
            np.searchsorted(self._trace_times, video_win_start, side="left")
        )
        if window.empty:
            self.lbl_status.setText(
                f"No data overlap — adjust Sync Offset on Full Data Plot. "
                f"Data: {data_t_min:.1f}–{data_t_max:.1f} s  |  "
                f"Video window (+ offset): {video_win_start:.1f}–{video_win_end:.1f} s"
            )
            return

        win_t_min = float(window[self._time_col].min())
        win_t_max = float(window[self._time_col].max())
        self.lbl_status.setText(
            f"Data window: {win_t_min:.1f}–{win_t_max:.1f} s  "
            f"(data spans {data_t_min:.1f}–{data_t_max:.1f} s)"
        )

        cols = self._overlay_cols()
        colors = [self._trace_color, self._trace_color2]
        alpha = 0.5 if len(cols) > 1 else 1.0
        t = cast(pd.Series, window[self._time_col]).to_numpy(dtype=float)
        values = {col: cast(pd.Series, window[col]).to_numpy(dtype=float) for col in cols}
        self._trace_baselines = {col: self._first_finite(values[col]) for col in cols}
        value_arrays = [values[col] for col in cols]
        transformed = [transform_values(values, self._trace_transform) for values in value_arrays]
        finite_parts = [array[np.isfinite(array)] for array in transformed]
        finite_display = np.concatenate(finite_parts) if finite_parts else np.array([], dtype=float)
        if finite_display.size:
            self._window_display_range = (
                float(np.min(finite_display)),
                float(np.max(finite_display)),
            )
        self._migrate_legacy_max_change()
        if self.max_change is None:
            normalised, self._plot_baseline = normalise_window_values(
                value_arrays, self._trace_transform
            )
        else:
            normalised = transformed
            self._plot_baseline = 0.0
        self._window_y_min = window_minimum(value_arrays)
        traces = [
            Trace(
                t,
                normalised[i],
                label=self._legend_labels.get(col, col),
                color=colors[i],
                linewidth=self._linewidth,
                alpha=alpha,
                source_column=col,
            )
            for i, col in enumerate(cols)
        ]
        c = self._composition
        plot_y_min = self._y_min - self._window_y_min if self._y_min is not None else None
        plot_y_max = self._y_max - self._window_y_min if self._y_max is not None else None
        self.anim_plot.setup(
            traces,
            y_min=plot_y_min,
            y_max=plot_y_max,
            max_change=self.max_change,
            pin_scale_bars_to_axes=True,
            show_time_scale=bool(c["show_time_scale"]),
            show_signal_scale=bool(c["show_signal_scale"]),
            scale_font_size=float(str(c["scale_font_size"])),
            scale_line_width=float(str(c["scale_line_width"])),
            time_scale_s=float(str(c["time_scale_s"])) or None,
            signal_scale=float(str(c["signal_scale"])) or None,
            anchor=named_anchor(str(c["anchor"])),
            background=str(c["background"]),
            time_scale_label=str(c.get("time_scale_label", "")),
            signal_scale_label=str(c.get("signal_scale_label", "")),
        )
        self._plot_ready = True
        self.lbl_plot_hint.setVisible(self._frame_index == 0)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _speed_factor(self) -> float:
        value = self._parse_speed()
        return value if value is not None else 1.0

    def _parse_speed(self) -> float | None:
        text = self.speed_combo.currentText().strip().lower().rstrip("x").strip()
        try:
            value = float(text)
        except ValueError:
            return None
        return value if 0.05 <= value <= 16 else None

    def validate_speed(self) -> bool:
        """Validate the editable preview speed before a single export."""
        if self._parse_speed() is not None:
            return True
        self._commit_speed_text()
        return False

    def _on_play(self) -> None:
        if self._cap is None:
            QMessageBox.warning(
                self,
                "Not ready",
                "Apply video settings on the Video Cropping tab first.",
            )
            return
        if self._df.empty:
            QMessageBox.warning(self, "Not ready", "Load data first.")
            return
        if not self._plot_ready:
            data_t_min = float(self._df[self._time_col].min()) if self._time_col else 0
            data_t_max = float(self._df[self._time_col].max()) if self._time_col else 0
            QMessageBox.warning(
                self,
                "Data not aligned",
                f"No data overlaps with the video window.\n\n"
                f"Video clip: {self._start_s:.1f} – {self._end_s:.1f} s\n"
                f"Data spans: {data_t_min:.1f} – {data_t_max:.1f} s\n\n"
                f"Adjust Sync Offset on the Full Data Plot tab, then return here.",
            )
            return
        # Position the sequential cursor at the current frame, then play.
        self._next_abs = self._cap.frame_index_at_time(self._start_s) + self._frame_index
        self._cap.set_pos(self._next_abs)
        self.lbl_plot_hint.hide()
        self._timer.start(self._interval_ms())

    def _interval_ms(self) -> int:
        """Timer interval for the requested speed (decode speed caps the real rate)."""
        return max(1, int(1000 / (self._fps * self._speed_factor())))

    def _on_pause(self) -> None:
        self._timer.stop()

    def _on_speed_changed(self, _: str) -> None:
        """If playing, restart the timer at the new speed. If paused, do nothing."""
        self._update_export_button_label()
        text = self.speed_combo.currentText().strip().lower().rstrip("x").strip()
        try:
            value = float(text)
        except ValueError:
            value = 0
        if not self._suspend_preferences and 0.05 <= value <= 16:
            self._settings.setValue("composition/speed", f"{value:g}x")
        if self._timer.isActive():
            self._timer.start(self._interval_ms())

    def _commit_speed_text(self) -> None:
        """Replace invalid editable input with the last valid reusable speed."""
        text = self.speed_combo.currentText().strip().lower().rstrip("x").strip()
        try:
            value = float(text)
        except ValueError:
            value = 0
        if 0.05 <= value <= 16:
            normalised = f"{value:g}x"
            if self.speed_combo.currentText() != normalised:
                self.speed_combo.setCurrentText(normalised)
            self._update_export_button_label()
            return
        fallback = str(self._settings.value("composition/speed", "1x"))
        self.speed_combo.setCurrentText(fallback)
        self._update_export_button_label()
        self.lbl_status.setText("Speed must be between 0.05x and 16x.")

    def _update_export_button_label(self) -> None:
        """Show the speed used by the single-export button."""
        if not hasattr(self, "btn_export"):
            return
        value = self._parse_speed()
        label = f"{value:g}x" if value is not None else "invalid speed"
        self.btn_export.setText(f"Export Video ({label})")

    def _on_deflicker_toggled(self, _: bool) -> None:
        """Re-render the current frame so the effect is visible immediately."""
        if not self._suspend_preferences:
            self._settings.setValue("composition/deflicker", self.chk_deflicker.isChecked())
        if self._cap is not None:
            self._show_frame(self._frame_index)

    def _apply_export_preferences(self) -> None:
        """Restore reusable export controls for a new session or project."""
        self._suspend_preferences = True
        try:
            speed = str(self._settings.value("composition/speed", "1x"))
            self.speed_combo.setCurrentText(speed)
            self.layout_combo.setCurrentIndex(0)
            panel_sizes = self._settings.value("composition/panelSizes", [])
            self._restore_split(panel_sizes)
            checked = str(self._settings.value("composition/deflicker", "false")).lower()
            self.chk_deflicker.setChecked(checked == "true")
        finally:
            self._suspend_preferences = False

    def apply_project_export_settings(self, settings: dict) -> None:
        """Apply saved project controls without changing reusable user defaults."""
        self._suspend_preferences = True
        try:
            # Legacy projects may contain stacked orientation. The preview is
            # now always side-by-side.
            self.layout_combo.setCurrentIndex(0)
            self._restore_split(settings.get("splitter_sizes"))
            self.speed_combo.setCurrentText(str(settings.get("speed", "1x")))
            self.chk_deflicker.setChecked(bool(settings.get("deflicker", False)))
            raw_advanced = settings.get("advanced", {})
            advanced = dict(raw_advanced) if isinstance(raw_advanced, dict) else {}
            advanced.setdefault("max_change_enabled", False)
            if "max_change" not in advanced:
                advanced.setdefault("max_change_percent", 70.0)
            self.set_composition_settings(advanced)
        finally:
            self._suspend_preferences = False

    def set_export_busy(self, busy: bool) -> None:
        self.btn_export.setEnabled(not busy)
        self.btn_export_multiple.setEnabled(not busy)

    def _on_reset(self) -> None:
        self._stop_playback()
        self._frame_index = 0
        if self._cap is not None:
            self._show_frame(0)
        self._setup_plot_if_ready()

    def _stop_playback(self) -> None:
        self._timer.stop()
        self._frame_index = 0

    def _tick(self) -> None:
        if self._frame_index >= self._total_frames:
            self._timer.stop()
            self.lbl_status.setText("Playback complete.")
            return

        # Sequential decode (no per-frame seek) so the video keeps pace and no
        # frames are skipped. Decode speed — not seeking — sets the real rate.
        if self._cap is None:
            self._timer.stop()
            return
        expected_abs = self._cap.frame_index_at_time(self._start_s) + self._frame_index
        if self._next_abs != expected_abs:
            self._cap.set_pos(expected_abs)
            self._next_abs = expected_abs
        frame = self._cap.read_next()
        if frame is None:
            self._timer.stop()
            self.lbl_status.setText("Playback complete.")
            return
        self._next_abs += 1

        self._process_and_display(frame)
        self._update_data_line(self._frame_index)
        self._frame_index += 1

    def _relayout_display(self) -> None:
        """Let the splitter fill the display area; its handle owns the ratio."""
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.anim_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _show_frame(self, frame_index: int) -> None:
        """Display a specific frame via random access (seek). Used when paused."""
        if self._cap is None:
            return
        abs_frame = self._cap.frame_index_at_time(self._start_s) + frame_index
        frame = self._cap.read_frame(abs_frame)
        # Keep the sequential cursor consistent with this seek.
        self._next_abs = abs_frame + 1
        if frame is not None:
            self._process_and_display(frame)

    def _process_and_display(self, frame) -> None:
        """Crop, de-flicker, colour-correct and paint a BGR frame to the panel."""
        # Rotate before applying the crop so crop coordinates match the Video tab.
        frame = rotate_frame(frame, self._rotation)
        if self._crop is not None and self._crop.is_valid():
            x1, y1, x2, y2 = self._crop.to_tuple()
            frame = frame[y1:y2, x1:x2]

        # Brightness-flicker correction (optional) — before the colour LUT so it
        # normalises the raw luminance the reference was measured on.
        if self.chk_deflicker.isChecked():
            frame = correct_frame_brightness(frame, self._ref_brightness)

        # Apply colour LUT (None = identity, skip)
        if self._colour_lut is not None:
            frame = apply_lut(frame, self._colour_lut)

        # Convert to Qt pixmap, scaled into the current preview panel.
        import cv2

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        self.video_label.set_source_pixmap(QPixmap.fromImage(qimg))

    def _update_data_line(self, frame_index: int) -> None:
        if self._trace_times.size == 0 or not self._data_col:
            return
        cols = self._overlay_cols()
        if any(col not in self._trace_values for col in cols):
            return
        current_time_s = self._start_s + (frame_index / self._fps) + self._offset_s
        end_idx = int(np.searchsorted(self._trace_times, current_time_s, side="right"))
        start = self._window_start_idx
        if end_idx <= start:
            return
        t = self._trace_times[start:end_idx]
        self.anim_plot.update_lines(
            [
                (t, self._delta_from_start(col, self._trace_values[col][start:end_idx]))
                for col in cols
            ]
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _release_cap(self) -> None:
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def resizeEvent(self, event) -> None:
        """Re-fit the video/plot panels and re-render the current frame."""
        super().resizeEvent(event)
        self._relayout_display()
        if self._default_split_pending:
            self._set_default_split()
        if self._cap is not None and not self._timer.isActive():
            self._show_frame(self._frame_index)
        self._position_play_hint()

    def showEvent(self, event) -> None:
        """Fit the preview when the tab first becomes visible."""
        super().showEvent(event)
        self._relayout_display()
        if self._default_split_pending:
            self._set_default_split()
        if self._cap is not None and not self._timer.isActive():
            self._show_frame(self._frame_index)

    def closeEvent(self, event) -> None:
        self._release_cap()
        super().closeEvent(event)

    def reset_state(self) -> None:
        self._stop_playback()
        self._release_cap()
        self._video_path = None
        self._rotation = 0
        self._df = pd.DataFrame()
        self._time_col = None
        self._data_col = self._data_col2 = ""
        self._plot_ready = False
        self._trace_times = np.array([], dtype=float)
        self._trace_values = {}
        self._plot_baseline = 0.0
        self._window_y_min = 0.0
        self._window_display_range = None
        self.video_label.clear()
        self.video_label.setText("Video will appear here")
        self.anim_plot.ax.cla()
        self.anim_plot.canvas.draw_idle()
        self.lbl_plot_hint.hide()
        self._apply_export_preferences()

    # ------------------------------------------------------------------
    # Accessors for exporter (main_window passes these through)
    # ------------------------------------------------------------------

    @property
    def video_path(self) -> str | None:
        return self._video_path

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def start_s(self) -> float:
        return self._start_s

    @property
    def end_s(self) -> float:
        return self._end_s

    @property
    def crop(self) -> CropRect | None:
        return self._crop

    @property
    def colour_lut(self) -> np.ndarray | None:
        return self._colour_lut

    @property
    def rotation(self) -> int:
        return self._rotation

    def _max_change_range_summary(self, selected_percent: float) -> str:
        if self._window_display_range is None:
            return "Load video and data to see the signal range for this clip."
        range_min, range_max = self._window_display_range
        span = range_max - range_min
        unit = shared_y_unit_for_columns(self._overlay_cols())
        suffix = f" {unit}" if unit else ""
        if span <= 0:
            return f"The signal is flat at {range_min:.4g}{suffix}; this limit will have no effect."
        upper_limit = range_min + span * selected_percent / 100.0
        return (
            f"Signal range: {range_min:.4g} to {range_max:.4g}{suffix} "
            f"({span:.4g}{suffix} total). At {selected_percent:.4g}%, the graph shows "
            f"{range_min:.4g} to {upper_limit:.4g}{suffix}."
        )

    def _migrate_legacy_max_change(self) -> None:
        if self._legacy_max_change is None or self._window_display_range is None:
            return
        range_min, range_max = self._window_display_range
        span = range_max - range_min
        if span <= 0 or not np.isfinite(span):
            return
        percent = self._legacy_max_change / span * 100.0
        if np.isfinite(percent) and percent > 0:
            self._composition["max_change_percent"] = min(percent, 100.0)
        self._legacy_max_change = None

    @property
    def max_change(self) -> float | None:
        if not bool(self._composition.get("max_change_enabled", False)):
            return None
        if self._window_display_range is None:
            return None
        try:
            percent = float(str(self._composition.get("max_change_percent", 70.0)))
        except ValueError:
            return None
        span = self._window_display_range[1] - self._window_display_range[0]
        if not np.isfinite(percent) or not 0 < percent <= 100 or not np.isfinite(span) or span <= 0:
            return None
        return span * percent / 100.0

    @property
    def speed_factor(self) -> float:
        return self._speed_factor()

    @property
    def linewidth(self) -> float:
        return self._linewidth

    @property
    def deflicker_enabled(self) -> bool:
        return self.chk_deflicker.isChecked()

    @property
    def video_width_fraction(self) -> float:
        sizes = self.display_container.sizes()
        return sizes[0] / max(1, sum(sizes)) if len(sizes) == 2 else VIDEO_WIDTH_FRACTION
