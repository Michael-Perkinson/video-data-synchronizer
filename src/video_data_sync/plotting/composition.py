"""Shared composition geometry and scale-bar calculations."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import numpy as np

TIME_INCREMENTS = (
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
)
SIGNAL_INCREMENTS = (
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
)
ANCHORS = {
    "Bottom right": (0.3, 0.5),
    "Bottom left": (0.8, 0.5),
    "Top right": (0.3, 0.8),
    "Top left": (0.8, 0.8),
}
_PINNED_BAR_INSET_FRACTION = 0.04


def scale_label_figure_margins(
    figure_width_inches: float,
    figure_height_inches: float,
    font_size: float,
    line_width: float,
    anchor: tuple[float, float],
    *,
    show_time: bool,
    show_signal: bool,
) -> tuple[float, float, float, float]:
    """Compute figure margins that keep scale labels visible at pane size."""
    base_margin = 0.035

    def required_margin(extent_points: float, dimension_inches: float) -> float:
        if not np.isfinite(dimension_inches) or dimension_inches <= 0:
            return base_margin
        label_fraction = extent_points / (72.0 * dimension_inches)
        opposite_margin = base_margin
        required = (label_fraction - _PINNED_BAR_INSET_FRACTION * (1.0 - opposite_margin)) / (
            1.0 - _PINNED_BAR_INSET_FRACTION
        )
        return min(max(base_margin, required + 0.005), 0.6)

    left = right = bottom = top = base_margin
    if show_signal:
        side_margin = required_margin(
            1.2 * font_size + line_width / 2 + 2.0,
            figure_width_inches,
        )
        if anchor[0] >= 0.5:
            left = side_margin
        else:
            right = side_margin
    if show_time:
        vertical_margin = required_margin(
            1.7 * font_size + line_width / 2 + 2.0,
            figure_height_inches,
        )
        if anchor[1] <= 0.65:
            bottom = vertical_margin
        else:
            top = vertical_margin
    return left, right, bottom, top


def even_pixels(value: float) -> int:
    return max(2, int(round(value / 2) * 2))


@dataclass(frozen=True)
class CompositionGeometry:
    video_width: int
    video_height: int
    plot_width: int
    plot_height: int
    output_width: int
    output_height: int


def composition_geometry(
    source_width: int,
    source_height: int,
    fraction: float = 0.6,
    orientation: str = "side_by_side",
    target_size: int = 540,
) -> CompositionGeometry:
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Source dimensions must be positive.")
    if not np.isfinite(fraction) or not 0.1 <= fraction <= 0.9:
        raise ValueError("Video fraction must be between 0.1 and 0.9.")
    if orientation not in {"side_by_side", "stacked"}:
        raise ValueError("Orientation must be 'side_by_side' or 'stacked'.")
    if orientation == "side_by_side":
        vh = even_pixels(target_size)
        vw = even_pixels(source_width * vh / source_height)
        pw = even_pixels(vw * (1 - fraction) / fraction)
        return CompositionGeometry(vw, vh, pw, vh, vw + pw, vh)
    vw = even_pixels(target_size)
    vh = even_pixels(source_height * vw / source_width)
    ph = even_pixels(vh * (1 - fraction) / fraction)
    return CompositionGeometry(vw, vh, vw, ph, vw, vh + ph)


def named_anchor(name: str) -> tuple[float, float]:
    if name not in ANCHORS:
        raise ValueError(f"Unknown scale-bar anchor: {name}")
    return ANCHORS[name]


def nice_scale(value: float, increments: tuple[float, ...]) -> float:
    return next((x for x in increments if x >= value), increments[-1])


def y_unit_for_column(name: str) -> str:
    low = name.lower()
    if re.search(r"df[\s_/]?f|dfof|delta[\s_]?f", low):
        return "ΔF/F"
    if re.search(r"z[\s_-]?score|zscore", low) or re.search(r"(?<![a-z])z[\s_-]?\d", low):
        return "z-score"
    return ""


def shared_y_unit_for_columns(names: list[str]) -> str:
    units = {y_unit_for_column(x) for x in names} - {""}
    return next(iter(units)) if len(units) == 1 else ""


@dataclass(frozen=True)
class AxesComposition:
    xlim: tuple[float, float]
    ylim: tuple[float, float]
    bar_x: float
    bar_y: float
    time_scale: float
    signal_scale: float
    time_direction: int = 1
    signal_direction: int = 1


@dataclass(frozen=True)
class TraceOriginPlacement:
    """Translation that places the first trace sample beside the scale bar."""

    time_offset: float
    signal_offset: float
    origin_x: float
    origin_y: float
    used_fallback: bool = False


def _first_finite_pair(time_s: np.ndarray, values: np.ndarray) -> tuple[int, int] | None:
    valid = np.flatnonzero(np.isfinite(time_s) & np.isfinite(values))
    if valid.size == 0:
        return None
    return int(valid[0]), int(valid[1]) if valid.size > 1 else -1


def _bar_x_interval(comp: AxesComposition) -> tuple[float, float]:
    """Return the sorted x-range occupied by the horizontal scale bar."""
    bar_end = comp.bar_x + comp.time_direction * comp.time_scale
    return min(comp.bar_x, bar_end), max(comp.bar_x, bar_end)


def _signal_extrema_in_bar_interval(
    time_s: np.ndarray,
    values: np.ndarray,
    time_offset: float,
    signal_offset: float,
    comp: AxesComposition,
) -> tuple[float, float] | None:
    """Return the translated signal range over segments crossing the time bar."""
    bar_left, bar_right = _bar_x_interval(comp)
    t = np.asarray(time_s, dtype=float) + time_offset
    v = np.asarray(values, dtype=float) + signal_offset
    finite = np.isfinite(t) & np.isfinite(v)
    t = t[finite]
    v = v[finite]
    if t.size < 2:
        return None
    on_bar = np.flatnonzero((t >= bar_left) & (t <= bar_right))
    if on_bar.size == 0:
        return None
    lo = max(0, int(on_bar[0]) - 1)
    hi = min(t.size - 1, int(on_bar[-1]) + 1)
    lowest: float | None = None
    highest: float | None = None
    for i in range(lo, hi):
        x0, x1 = t[i], t[i + 1]
        y0, y1 = v[i], v[i + 1]
        seg_lo = max(min(x0, x1), bar_left)
        seg_hi = min(max(x0, x1), bar_right)
        if seg_lo > seg_hi:
            continue
        if x1 == x0:
            y_lo, y_hi = min(y0, y1), max(y0, y1)
        else:
            at_lo = y0 + (y1 - y0) * (seg_lo - x0) / (x1 - x0)
            at_hi = y0 + (y1 - y0) * (seg_hi - x0) / (x1 - x0)
            y_lo, y_hi = min(at_lo, at_hi), max(at_lo, at_hi)
        lowest = y_lo if lowest is None else min(lowest, y_lo)
        highest = y_hi if highest is None else max(highest, y_hi)
    if lowest is None or highest is None:
        return None
    return lowest, highest


def place_trace_origin(
    comp: AxesComposition,
    time_values: list[np.ndarray],
    signal_values: list[np.ndarray],
    *,
    preserve_time: bool = False,
    preserve_signal_baseline: bool = False,
    clear_time_bar: bool = True,
    allow_signal_shift: bool = True,
) -> tuple[list[np.ndarray], list[np.ndarray], AxesComposition, TraceOriginPlacement]:
    """Translate traces to the scale-bar midpoint intersection.

    The normal origin is the intersection of perpendiculars through the
    midpoint of each scale-bar leg. If any trace segment whose x-range overlaps
    the horizontal leg drops below it, move the whole trace upward just enough
    to clear that leg. The overlap is computed from the actual rendered time
    range of the bar, so it works regardless of the user's time axis or
    sampling density.
    """
    if not time_values or not signal_values:
        placement = TraceOriginPlacement(0.0, 0.0, comp.bar_x, comp.bar_y)
        return time_values, signal_values, comp, placement

    first_pair = _first_finite_pair(
        np.asarray(time_values[0], dtype=float), np.asarray(signal_values[0], dtype=float)
    )
    if first_pair is None:
        placement = TraceOriginPlacement(0.0, 0.0, comp.bar_x, comp.bar_y)
        return time_values, signal_values, comp, placement

    first_index, _second_index = first_pair
    first_time = float(np.asarray(time_values[0], dtype=float)[first_index])
    first_signal = float(np.asarray(signal_values[0], dtype=float)[first_index])
    origin_x = comp.bar_x + comp.time_direction * comp.time_scale / 2
    origin_y = comp.bar_y + comp.signal_direction * comp.signal_scale / 2
    if preserve_time:
        origin_x = first_time
        time_offset = 0.0
    else:
        time_offset = origin_x - first_time
    signal_offset = 0.0 if preserve_signal_baseline else origin_y - first_signal
    origin_y = first_signal + signal_offset
    used_fallback = False

    extrema = None
    signal_time_pairs = (
        [(time_values[0], signal_arr) for signal_arr in signal_values]
        if len(time_values) == 1 and len(signal_values) > 1
        else list(zip(time_values, signal_values, strict=True))
    )
    for time_arr, signal_arr in signal_time_pairs:
        candidate = _signal_extrema_in_bar_interval(
            time_arr, signal_arr, time_offset, signal_offset, comp
        )
        if candidate is not None:
            if extrema is None:
                extrema = candidate
            else:
                extrema = (min(extrema[0], candidate[0]), max(extrema[1], candidate[1]))
    if clear_time_bar and allow_signal_shift and extrema is not None:
        clearance = comp.signal_scale * 0.05
        if comp.signal_direction > 0 and extrema[0] < comp.bar_y:
            shift = comp.bar_y - extrema[0] + clearance
        elif comp.signal_direction < 0 and extrema[1] > comp.bar_y:
            shift = comp.bar_y - extrema[1] - clearance
        else:
            shift = 0.0
        if shift:
            signal_offset += shift
            origin_y += shift
            used_fallback = True

    translated_times = [np.asarray(values, dtype=float) + time_offset for values in time_values]
    translated_signals = [
        np.asarray(values, dtype=float) + signal_offset for values in signal_values
    ]
    finite_times = np.concatenate(
        [values[np.isfinite(values)] for values in translated_times if np.isfinite(values).any()]
    )
    finite_signals = np.concatenate(
        [values[np.isfinite(values)] for values in translated_signals if np.isfinite(values).any()]
    )
    xlim = comp.xlim
    ylim = comp.ylim
    if finite_times.size:
        xlim = (min(xlim[0], float(finite_times.min())), max(xlim[1], float(finite_times.max())))
    if finite_signals.size:
        ylim = (
            min(ylim[0], float(finite_signals.min())),
            max(ylim[1], float(finite_signals.max())),
        )
    placement = TraceOriginPlacement(time_offset, signal_offset, origin_x, origin_y, used_fallback)
    return translated_times, translated_signals, replace(comp, xlim=xlim, ylim=ylim), placement


def axes_composition(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    *,
    time_scale: float | None = None,
    signal_scale: float | None = None,
    anchor: tuple[float, float] = (0.8, 0.5),
) -> AxesComposition:
    xr = x_max - x_min
    if not np.isfinite(xr) or xr <= 0:
        xr, x_min, x_max = 1.0, x_min - 0.5, x_max + 0.5
    yr = y_max - y_min
    if not np.isfinite(yr) or yr <= 0:
        c = y_min if np.isfinite(y_min) else 0.0
        yr = max(abs(c) * 0.2, 1.0)
        y_min, y_max = c - yr / 2, c + yr / 2
    xb = time_scale or nice_scale(xr * 0.15, TIME_INCREMENTS)
    yb = signal_scale or nice_scale(yr * 0.2, SIGNAL_INCREMENTS)
    # A left anchor uses the familiar └ shape; a right anchor mirrors it to ┘.
    # Both bars live outside the data interval, preventing trace collisions.
    right_side = anchor[0] < 0.5
    top_side = anchor[1] > 0.65
    time_direction = -1 if right_side else 1
    signal_direction = -1 if top_side else 1
    gap = xb * 0.05
    bx = x_max + xb + gap if right_side else x_min - xb - gap
    y_gap = yb * 0.05
    by = y_max + yb + y_gap if top_side else y_min - yb - y_gap
    xm = max(xr * 0.025, xb * 0.25)
    ym = max(yr * 0.08, yb * 0.14)
    return AxesComposition(
        (x_min - xm if right_side else bx - xm, bx + xm if right_side else x_max),
        (y_min - ym if top_side else by - ym, by + ym if top_side else y_max + ym),
        bx,
        by,
        xb,
        yb,
        time_direction,
        signal_direction,
    )


def pin_scale_bars(comp: AxesComposition, anchor: tuple[float, float]) -> AxesComposition:
    """Place scale bars in a fixed corner of the final plot-pane limits."""
    x_min, x_max = comp.xlim
    y_min, y_max = comp.ylim
    x_range = x_max - x_min
    y_range = y_max - y_min
    if not np.isfinite(x_range) or x_range <= 0 or not np.isfinite(y_range) or y_range <= 0:
        return comp

    left_side = anchor[0] >= 0.5
    bottom_side = anchor[1] <= 0.65
    inset_x = x_range * _PINNED_BAR_INSET_FRACTION
    inset_y = y_range * _PINNED_BAR_INSET_FRACTION
    time_scale = min(comp.time_scale, x_range - 2 * inset_x)
    signal_scale = min(comp.signal_scale, y_range - 2 * inset_y)
    return replace(
        comp,
        bar_x=x_min + inset_x if left_side else x_max - inset_x,
        bar_y=y_min + inset_y if bottom_side else y_max - inset_y,
        time_scale=max(time_scale, 0.0),
        signal_scale=max(signal_scale, 0.0),
        time_direction=1 if left_side else -1,
        signal_direction=1 if bottom_side else -1,
    )


def scale_bar_band_limits(
    data_y_min: float,
    data_y_max: float,
    signal_scale: float,
    anchor_y: float,
    *,
    show_time_scale: bool,
    show_signal_scale: bool,
) -> tuple[float, float]:
    """Reserve space outside a fixed data range for pinned scale bars.

    In fixed-change mode the trace must remain anchored to its actual minimum.
    This band keeps the bar (and its clearance gap) outside that data interval.
    """
    if not (show_time_scale or show_signal_scale):
        return data_y_min, data_y_max
    data_range = data_y_max - data_y_min
    if not np.isfinite(data_range) or data_range <= 0:
        return data_y_min, data_y_max
    vertical_bar_length = signal_scale if show_signal_scale else 0.0
    clearance = max(data_range * 0.01, vertical_bar_length * 0.05)
    band = (_PINNED_BAR_INSET_FRACTION * data_range + vertical_bar_length + clearance) / (
        1.0 - _PINNED_BAR_INSET_FRACTION
    )
    edge_padding = data_range * 0.01
    if anchor_y <= 0.65:
        return data_y_min - band, data_y_max + edge_padding
    return data_y_min - edge_padding, data_y_max + band


def align_trace_origin_to_pinned_bars(
    comp: AxesComposition,
    time_values: list[np.ndarray],
    signal_values: list[np.ndarray],
    anchor: tuple[float, float],
    *,
    preserve_time: bool = False,
    preserve_signal_baseline: bool = False,
    clear_time_bar: bool = True,
    allow_signal_shift: bool = True,
    fixed_ylim: tuple[float, float] | None = None,
) -> tuple[list[np.ndarray], list[np.ndarray], AxesComposition, TraceOriginPlacement]:
    """Re-place traces against pinned bars until pane geometry stabilises."""
    source_times = [np.asarray(values, dtype=float) for values in time_values]
    source_signals = [np.asarray(values, dtype=float) for values in signal_values]
    placed_times = source_times
    placed_signals = source_signals
    placement = TraceOriginPlacement(0.0, 0.0, comp.bar_x, comp.bar_y)
    output_comp = comp

    for _ in range(12):
        if fixed_ylim is not None:
            comp = replace(comp, ylim=fixed_ylim)
        pinned_comp = pin_scale_bars(comp, anchor)
        placed_times, placed_signals, output_comp, placement = place_trace_origin(
            pinned_comp,
            source_times,
            source_signals,
            preserve_time=preserve_time,
            preserve_signal_baseline=preserve_signal_baseline,
            clear_time_bar=clear_time_bar,
            allow_signal_shift=allow_signal_shift,
        )
        if fixed_ylim is not None:
            output_comp = replace(output_comp, ylim=fixed_ylim)
        next_comp = pin_scale_bars(output_comp, anchor)
        geometry_delta = max(
            abs(next_comp.bar_x - pinned_comp.bar_x),
            abs(next_comp.bar_y - pinned_comp.bar_y),
            abs(next_comp.time_scale - pinned_comp.time_scale),
            abs(next_comp.signal_scale - pinned_comp.signal_scale),
        )
        if geometry_delta < 1e-9:
            return placed_times, placed_signals, next_comp, placement
        comp = output_comp

    return placed_times, placed_signals, pin_scale_bars(output_comp, anchor), placement


def draw_scale_bars(
    ax,
    comp: AxesComposition,
    unit: str,
    *,
    font_size: float,
    line_width: float,
    show_time: bool = True,
    show_signal: bool = True,
    time_label: str = "",
    signal_label: str = "",
) -> None:
    if show_time:
        ax.plot(
            [comp.bar_x, comp.bar_x + comp.time_direction * comp.time_scale],
            [comp.bar_y, comp.bar_y],
            color="black",
            linewidth=line_width,
            solid_capstyle="butt",
            zorder=3,
        )
        n = comp.time_scale
        if time_label.strip():
            # The measured numeric length is always retained; custom text only
            # replaces the automatically selected unit/label.
            label = f"{n:g} {time_label.strip()}"
        else:
            label = f"{int(n // 60)} min" if n >= 60 and n % 60 == 0 else f"{n:g} s"
        ax.annotate(
            label,
            xy=(comp.bar_x, comp.bar_y),
            xytext=(0, -font_size * 0.5 if comp.signal_direction > 0 else font_size * 0.5),
            textcoords="offset points",
            ha="left" if comp.time_direction > 0 else "right",
            va="top" if comp.signal_direction > 0 else "bottom",
            fontsize=font_size,
            annotation_clip=False,
            clip_on=False,
        )
    if show_signal:
        ax.plot(
            [comp.bar_x, comp.bar_x],
            [comp.bar_y, comp.bar_y + comp.signal_direction * comp.signal_scale],
            color="black",
            linewidth=line_width,
            solid_capstyle="butt",
            zorder=3,
        )
        ax.annotate(
            f"{comp.signal_scale:.2g}"
            f"{' ' + (signal_label.strip() or unit) if signal_label.strip() or unit else ''}",
            xy=(comp.bar_x, comp.bar_y),
            # Keep the label's vertical start at the shared origin and offset it
            # only perpendicular to the bar stroke for readable clearance.
            xytext=(-comp.time_direction * (line_width / 2 + 2.0), 0),
            textcoords="offset points",
            ha="left",
            va=("bottom" if (comp.time_direction > 0) == (comp.signal_direction > 0) else "top"),
            fontsize=font_size,
            rotation=90 if comp.signal_direction > 0 else 270,
            rotation_mode="anchor",
            annotation_clip=False,
            clip_on=False,
        )
