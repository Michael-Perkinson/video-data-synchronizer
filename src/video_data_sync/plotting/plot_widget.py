"""
Reusable matplotlib-in-Qt widget.

PlotWidget wraps a Figure + NavigationToolbar and exposes a simple API for
plotting time-series data with optional fixed y-axis limits and scale bars.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from video_data_sync.plotting.composition import (
    align_trace_origin_to_pinned_bars,
    axes_composition,
    draw_scale_bars,
    place_trace_origin,
    scale_bar_band_limits,
    scale_label_figure_margins,
)


@dataclass
class Trace:
    """A single time-series trace to draw on a shared axis."""

    time_s: pd.Series | np.ndarray
    values: pd.Series | np.ndarray
    label: str = ""
    color: str = "#000000"
    linewidth: float = 1.5
    alpha: float = 1.0
    source_column: str = ""


class PlotWidget(QWidget):
    """
    A self-contained widget containing a matplotlib Figure and a toolbar.

    Usage:
        widget = PlotWidget(parent)
        widget.plot(time_s, values, color="#000000")
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.figure = Figure(tight_layout=True)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self._style_axes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _style_axes(self) -> None:
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)

    def _apply_y_limits(self, y_min: float | None, y_max: float | None) -> None:
        if y_min is not None and y_max is not None:
            self.ax.set_ylim(y_min, y_max)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clear(self) -> None:
        self.ax.cla()
        self._style_axes()
        self.canvas.draw_idle()

    def plot(
        self,
        time_s: pd.Series | np.ndarray,
        values: pd.Series | np.ndarray,
        label: str = "",
        color: str = "#000000",
        linewidth: float = 1.5,
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> None:
        """
        Plot a single time-series trace.

        y_min / y_max: if both provided, fixes the y-axis to that range.
        Otherwise autoscales.
        """
        self.plot_traces(
            [Trace(time_s, values, label=label, color=color, linewidth=linewidth)],
            y_min=y_min,
            y_max=y_max,
        )

    def plot_traces(
        self,
        traces: list[Trace],
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> None:
        """
        Plot one or more time-series traces on a single shared axis.

        All traces share the same x- and y-axis, so overlaid signals are drawn
        on the same scale (e.g. two ΔF/F channels). A legend is shown only when
        more than one trace is present. y_min / y_max fix the y-axis if both given.
        """
        self.ax.cla()
        self._style_axes()

        if not traces:
            self.canvas.draw_idle()
            return

        for tr in traces:
            self.ax.plot(
                tr.time_s,
                tr.values,
                color=tr.color,
                linewidth=tr.linewidth,
                alpha=tr.alpha,
                label=tr.label,
            )

        self.ax.set_xlabel("Time (s)")

        # Shared y-label: use the common unit if every trace shares one;
        # for a single trace fall back to its column name.
        units = {y_unit_for_column(tr.source_column or tr.label) for tr in traces}
        units.discard("")
        if len(units) == 1:
            self.ax.set_ylabel(next(iter(units)))
        elif len(traces) == 1:
            self.ax.set_ylabel(traces[0].label)
        else:
            self.ax.set_ylabel("")

        t_min = min(float(np.nanmin(tr.time_s)) for tr in traces)
        t_max = max(float(np.nanmax(tr.time_s)) for tr in traces)
        margin = (t_max - t_min) * 0.02
        self.ax.set_xlim(t_min - margin, t_max + margin)

        self._apply_y_limits(y_min, y_max)

        if len(traces) > 1:
            self.ax.legend(loc="upper right", fontsize=8, framealpha=0.6)

        self.canvas.draw_idle()

    def add_vline(self, x: float, color: str = "red", linestyle: str = "--") -> None:
        """Add a vertical line at time x (seconds)."""
        self.ax.axvline(x=x, color=color, linestyle=linestyle, linewidth=1)
        self.canvas.draw_idle()

    def update_trace(
        self,
        time_s: pd.Series | np.ndarray,
        values: pd.Series | np.ndarray,
    ) -> None:
        """Update the first line on the axes without clearing — faster for animation."""
        lines = self.ax.get_lines()
        if lines:
            lines[0].set_data(time_s, values)
            self.ax.relim()
            self.ax.autoscale_view(scaley=False)  # don't rescale y if fixed
        self.canvas.draw_idle()


# ---------------------------------------------------------------------------
# Animated plot widget (video + data side by side, no toolbar)
# ---------------------------------------------------------------------------


class AnimatedPlotWidget(QWidget):
    """
    Stripped-down matplotlib widget for the animated plot tab.

    Has no navigation toolbar (it's live playback, not interactive exploration).
    Supports scale bars instead of axis ticks.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preferred_size = QSize(320, 180)
        self._scale_font_size = 16.0
        self._scale_line_width = 2.8
        self._scale_anchor = (0.8, 0.5)
        self._show_time_scale = True
        self._show_signal_scale = True

        self.figure = Figure()
        self.figure.subplots_adjust(left=0.035, right=0.965, bottom=0.035, top=0.965)
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumSize(1, 1)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self._update_scale_label_margins()

        self._lines: list = []
        self._y_unit: str = ""
        self._trace_time_offset = 0.0
        self._trace_signal_offset = 0.0
        self._max_change: float | None = None
        self._data_y_min = 0.0

    def _update_scale_label_margins(self) -> None:
        if not hasattr(self, "figure"):
            return
        # Widget sizes are logical pixels; figure.dpi tracks physical pixels on HiDPI screens.
        pixel_ratio = self.devicePixelRatioF()
        width_inches = max(self.width(), 1) * pixel_ratio / self.figure.dpi
        height_inches = max(self.height(), 1) * pixel_ratio / self.figure.dpi
        left, right, bottom, top = scale_label_figure_margins(
            width_inches,
            height_inches,
            self._scale_font_size,
            self._scale_line_width,
            self._scale_anchor,
            show_time=self._show_time_scale,
            show_signal=self._show_signal_scale,
        )
        self.figure.subplots_adjust(
            left=left,
            right=1.0 - right,
            bottom=bottom,
            top=1.0 - top,
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_scale_label_margins()

    def sizeHint(self) -> QSize:
        return QSize(self._preferred_size)

    def minimumSizeHint(self) -> QSize:
        return QSize(1, 1)

    def set_preview_size(self, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))
        self._preferred_size = QSize(width, height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(width, height)
        self.canvas.setFixedSize(width, height)
        self.updateGeometry()

    def setup(
        self,
        traces: list[Trace],
        y_min: float | None = None,
        y_max: float | None = None,
        *,
        show_time_scale: bool = True,
        show_signal_scale: bool = True,
        scale_font_size: float = 16,
        scale_line_width: float = 2.8,
        time_scale_s: float | None = None,
        signal_scale: float | None = None,
        anchor: tuple[float, float] = (0.8, 0.5),
        max_change: float | None = None,
        pin_scale_bars_to_axes: bool | None = None,
        background: str = "white",
        time_scale_label: str = "",
        signal_scale_label: str = "",
    ) -> None:
        """
        Initialise the axes for animation. Call once before starting playback.

        traces : one or more Trace specs holding the *full-window* data; their
                 time/value arrays are used only to fix the axis limits and
                 scale bars. The lines start empty and grow via update_lines().
                 All traces share one x/y axis, so overlaid signals are on the
                 same scale; a legend is shown when more than one is given.
        """
        self.ax.cla()
        self._scale_font_size = scale_font_size
        self._scale_line_width = scale_line_width
        self._scale_anchor = anchor
        self._show_time_scale = show_time_scale
        self._show_signal_scale = show_signal_scale
        self._update_scale_label_margins()

        original_times = [np.asarray(tr.time_s, dtype=float) for tr in traces]
        original_values = [np.asarray(tr.values, dtype=float) for tr in traces]
        finite_times = np.concatenate([values[np.isfinite(values)] for values in original_times])
        finite_values = np.concatenate([values[np.isfinite(values)] for values in original_values])
        if finite_times.size:
            x_min = float(np.min(finite_times))
            x_max = float(np.max(finite_times))
        else:
            x_min, x_max = 0.0, 1.0
        if finite_values.size:
            data_y_min = float(np.min(finite_values))
            data_y_max = float(np.max(finite_values))
        else:
            data_y_min, data_y_max = 0.0, 1.0

        if max_change is not None and (not np.isfinite(max_change) or max_change <= 0):
            raise ValueError("Maximum change must be a positive finite number.")
        if pin_scale_bars_to_axes is None:
            pin_scale_bars_to_axes = max_change is not None
        if max_change is not None:
            axis_y_min, axis_y_max = data_y_min, data_y_min + max_change
        else:
            axis_y_min = y_min if y_min is not None else data_y_min
            axis_y_max = y_max if y_max is not None else data_y_max
        self._max_change = max_change
        self._data_y_min = data_y_min
        placement_values = (
            [np.minimum(values, data_y_min + max_change) for values in original_values]
            if max_change is not None
            else original_values
        )
        comp = axes_composition(
            x_min,
            x_max,
            axis_y_min,
            axis_y_max,
            time_scale=time_scale_s,
            signal_scale=signal_scale,
            anchor=anchor,
        )
        if max_change is not None and pin_scale_bars_to_axes:
            axis_y_min, axis_y_max = scale_bar_band_limits(
                axis_y_min,
                axis_y_max,
                comp.signal_scale,
                anchor[1],
                show_time_scale=show_time_scale,
                show_signal_scale=show_signal_scale,
            )
            comp = replace(comp, ylim=(axis_y_min, axis_y_max))
        if pin_scale_bars_to_axes:
            trace_times, trace_values, comp, placement = align_trace_origin_to_pinned_bars(
                comp,
                original_times,
                placement_values,
                anchor,
                preserve_time=anchor[0] < 0.5,
                preserve_signal_baseline=max_change is not None or anchor[0] < 0.5,
                clear_time_bar=show_time_scale,
                allow_signal_shift=max_change is None,
                fixed_ylim=(axis_y_min, axis_y_max) if max_change is not None else None,
            )
        else:
            trace_times, trace_values, comp, placement = place_trace_origin(
                comp,
                original_times,
                placement_values,
                preserve_signal_baseline=max_change is not None,
                clear_time_bar=show_time_scale,
                allow_signal_shift=max_change is None,
            )
            if max_change is not None:
                comp = replace(comp, ylim=(axis_y_min, axis_y_max))
        self._trace_time_offset = placement.time_offset
        self._trace_signal_offset = placement.signal_offset
        self.ax.set_xlim(*comp.xlim)
        self.ax.set_ylim(*comp.ylim)
        # The plot background is intentionally fixed. Keep the legacy
        # parameter in the API so older integrations continue to call setup,
        # but never allow saved projects or presets to produce a coloured pane.
        self.ax.set_facecolor("#ffffff")
        self.figure.patch.set_facecolor("#ffffff")

        # Hide ticks and spines
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        for spine in self.ax.spines.values():
            spine.set_visible(False)

        self._y_unit = shared_y_unit_for_columns([tr.source_column or tr.label for tr in traces])
        draw_scale_bars(
            self.ax,
            comp,
            self._y_unit,
            font_size=scale_font_size,
            line_width=scale_line_width,
            show_time=show_time_scale,
            show_signal=show_signal_scale,
            time_label=time_scale_label,
            signal_label=signal_scale_label,
        )
        # Initialise one empty animated line per trace
        self._lines = []
        for tr, time_s, values in zip(traces, trace_times, trace_values, strict=True):
            (line,) = self.ax.plot(
                time_s[:0],
                values[:0],
                color=tr.color,
                linewidth=tr.linewidth,
                alpha=tr.alpha,
                label=tr.label,
            )
            self._lines.append(line)
        if len(traces) > 1:
            self.ax.legend(loc="upper right", fontsize=8, framealpha=0.6)

        self.canvas.draw()

    def update_lines(
        self,
        data: list[tuple[pd.Series | np.ndarray, pd.Series | np.ndarray]],
    ) -> None:
        """
        Update each animated trace. Call on every frame tick.

        data : one (time_s, values) pair per line, in the same order as the
               traces passed to setup().
        """
        for line, (time_s, values) in zip(self._lines, data, strict=False):
            display_values = np.asarray(values, dtype=float) + self._trace_signal_offset
            if self._max_change is not None:
                display_values = np.minimum(display_values, self._data_y_min + self._max_change)
            line.set_data(
                np.asarray(time_s, dtype=float) + self._trace_time_offset,
                display_values,
            )
        self.canvas.draw_idle()


def y_unit_for_column(col_name: str) -> str:
    """
    Infer a y-axis unit label from the column name.

    Returns:
        "ΔF/F"   for dF/F or dfof-style columns
        "z-score" for z-score columns
        ""        otherwise (no unit shown)
    """
    import re

    lower = col_name.lower()
    # Match dff / df_f / dfof / df/f / delta_f / deltaf etc.
    if re.search(r"df[\s_/]?f|dfof|delta[\s_]?f", lower):
        return "\u0394F/F"
    # Match z_score / zscore / z-score, and photometry-style Z_465 / Z470 columns
    # (a leading "z" followed by an optional separator and a digit).
    if re.search(r"z[\s_-]?score|zscore", lower) or re.search(r"(?<![a-z])z[\s_-]?\d", lower):
        return "z-score"
    return ""


def shared_y_unit_for_columns(col_names) -> str:
    """Return the common unit for a set of trace labels, or empty if mixed."""
    units = {y_unit_for_column(col_name) for col_name in col_names}
    units.discard("")
    return next(iter(units)) if len(units) == 1 else ""


# Sensible scale-bar increments
_TIME_INCREMENTS = [
    0.1,
    0.2,
    0.5,
    1,
    2,
    5,
    10,
    15,
    30,
    60,
    90,
    120,
    180,
    300,
    600,
    900,
    1200,
    1800,
    3600,
]
_SIGNAL_INCREMENTS = [
    0.001,
    0.002,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1,
    2,
    5,
    10,
    20,
    50,
    100,
    200,
    500,
    1000,
]

ANIMATED_SCALE_BAR_FONT_SIZE = 28
ANIMATED_SCALE_BAR_LINEWIDTH = 2.8
