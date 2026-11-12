"""
Export a combined video (video frame + data trace side by side) to an MP4.

Uses cv2 for writing — no ffmpeg dependency required.
Each output frame is read from the source file on-demand (no RAM buffer),
cropped and colour-corrected, then stitched to a matplotlib plot rendered
to a numpy array.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sys
import tempfile
from dataclasses import replace
from typing import cast

import cv2
import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import is_color_like
from matplotlib.figure import Figure

from video_data_sync.data.processor import validate_data_columns, validate_time_series
from video_data_sync.data.transforms import (
    TraceTransform,
    normalise_window_values,
    transform_values,
    window_minimum,
)
from video_data_sync.export_history import record_export
from video_data_sync.plotting.composition import (
    AxesComposition,
    align_trace_origin_to_pinned_bars,
    axes_composition,
    composition_geometry,
    draw_scale_bars,
    place_trace_origin,
    scale_bar_band_limits,
    scale_label_figure_margins,
)
from video_data_sync.video_processing.colour import apply_lut
from video_data_sync.video_processing.flicker import (
    compute_reference_brightness,
    correct_frame_brightness,
)
from video_data_sync.video_processing.rotation import rotate_frame, validate_rotation

EXPORT_HEIGHT_PX = 540
VIDEO_WIDTH_FRACTION = 0.60
EXPORT_SCALE_BAR_FONT_SIZE = 16
EXPORT_SCALE_BAR_LINEWIDTH = 2.8


class ExportCancelled(RuntimeError):
    """Raised after an export is cooperatively cancelled."""


# Tolerance (in source frames) for floating-point error when a time lands exactly on a frame.
_FRAME_EPS = 1e-6


def _frame_at(time_s: float, source_fps: float) -> int:
    """Return ``floor(time_s * source_fps)``, robust to floating-point error at frame edges."""
    return math.floor(time_s * source_fps + _FRAME_EPS)


def _ceil_frames(frames: float) -> int:
    """Return ``ceil(frames)``, ignoring floating-point error just above a whole frame."""
    return math.ceil(frames - _FRAME_EPS)


def _output_frame_source(
    frame_index: int,
    *,
    start_s: float,
    speed_factor: float,
    output_fps: float,
    source_fps: float,
    start_frame: int,
    end_frame: int,
) -> tuple[float, int]:
    """Return ``(source_time_s, source_frame)`` for one output frame.

    ``source_time_s`` is the clip time shown by the output frame. ``source_frame`` is
    ``floor(source_time_s * source_fps)`` (with a small tolerance), clamped to
    ``[start_frame, end_frame - 1]``.
    """
    source_time_s = start_s + frame_index * speed_factor / output_fps
    wanted = _frame_at(source_time_s, source_fps)
    return source_time_s, max(start_frame, min(end_frame - 1, wanted))


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


@contextlib.contextmanager
def _suppress_native_stderr():
    """
    Silence OS-level stderr for the duration of the block.

    Probing the avc1/H.264 codec makes the bundled ffmpeg/OpenH264 libraries
    print load failures straight to the C stderr (not Python's), so this hides
    that noise. Falls back to a no-op if stderr has no real file descriptor.
    """
    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return
    saved = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, stderr_fd)
        yield
    finally:
        os.dup2(saved, stderr_fd)
        os.close(devnull)
        os.close(saved)


def _open_video_writer(
    output_path: str,
    fps: float,
    size: tuple[int, int],
) -> cv2.VideoWriter | None:
    """
    Open a writer after probing codecs against a temporary file.

    OpenCV can report a writer as opened even when the underlying encoder is not
    usable. Probing avoids a corrupt final export without inserting a black test
    frame into the user's output.
    """
    out_w, out_h = size
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    with _suppress_native_stderr():
        for fourcc_str in ("mp4v", "avc1"):
            fourcc = cv2.VideoWriter.fourcc(*fourcc_str)
            handle = tempfile.NamedTemporaryFile(
                prefix=f".codec_probe_{fourcc_str}_",
                suffix=".mp4",
                dir=output_dir,
                delete=False,
            )
            probe_path = handle.name
            handle.close()
            os.remove(probe_path)
            probe = cv2.VideoWriter(probe_path, fourcc, fps, size)
            ok = False
            if probe.isOpened():
                probe.write(np.zeros((out_h, out_w, 3), dtype=np.uint8))
                ok = probe.isOpened()
            probe.release()
            try:
                os.remove(probe_path)
            except OSError:
                pass
            if not ok:
                continue

            writer = cv2.VideoWriter(output_path, fourcc, fps, size)
            if writer.isOpened():
                return writer
            writer.release()
    return None


def _render_plot_frame(
    canvas: FigureCanvasAgg,
    lines: list,
    line_data: list[tuple[np.ndarray, np.ndarray]],
    target_h: int,
    target_w: int,
) -> np.ndarray:
    """
    Render the current state of the figure to a (target_h, target_w, 3) BGR array.

    The axis limits are fixed once at setup (full window), so each line just
    grows left-to-right within it — matching the live playback view. The actual
    rendered size is read back from the canvas (matplotlib can be off by a pixel
    from set_size_inches due to rounding) and resized to the exact target so it
    always stitches cleanly next to the video frame.
    """
    for line, (time_data, value_data) in zip(lines, line_data, strict=False):
        line.set_data(time_data, value_data)

    canvas.draw()
    w, h = canvas.get_width_height()
    arr = np.frombuffer(canvas.buffer_rgba(), dtype=np.uint8).reshape(h, w, 4)
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if (h, w) != (target_h, target_w):
        bgr = cv2.resize(bgr, (target_w, target_h))
    return bgr


def _even_pixel_count(value: float) -> int:
    """Return a positive even pixel count for codec-friendly MP4 dimensions."""
    return max(2, int(round(value / 2.0) * 2))


def _first_finite(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite[0]) if finite.size else 0.0


def _nice_scale(value: float, increments: list[float]) -> float:
    for inc in sorted(increments):
        if inc >= value:
            return inc
    return increments[-1]


def _y_unit_for_column(col_name: str) -> str:
    lower = col_name.lower()
    if re.search(r"df[\s_/]?f|dfof|delta[\s_]?f", lower):
        return "\u0394F/F"
    if re.search(r"z[\s_-]?score|zscore", lower) or re.search(r"(?<![a-z])z[\s_-]?\d", lower):
        return "z-score"
    return ""


def _shared_y_unit_for_columns(col_names: list[str]) -> str:
    units = {_y_unit_for_column(col_name) for col_name in col_names}
    units.discard("")
    return next(iter(units)) if len(units) == 1 else ""


def _draw_scale_bars(
    ax,
    bar_x: float,
    bar_y: float,
    x_bar_len: float,
    y_bar_len: float,
    y_unit: str,
    font_size: float = EXPORT_SCALE_BAR_FONT_SIZE,
    line_width: float = EXPORT_SCALE_BAR_LINEWIDTH,
    show_time: bool = True,
    show_signal: bool = True,
    time_direction: int = 1,
    signal_direction: int = 1,
    time_label: str = "",
    signal_label: str = "",
) -> None:
    """Draw L-shaped time/signal scale bars for the exported trace panel."""
    draw_scale_bars(
        ax,
        AxesComposition(
            ax.get_xlim(),
            ax.get_ylim(),
            bar_x,
            bar_y,
            x_bar_len,
            y_bar_len,
            time_direction,
            signal_direction,
        ),
        y_unit,
        font_size=font_size,
        line_width=line_width,
        show_time=show_time,
        show_signal=show_signal,
        time_label=time_label,
        signal_label=signal_label,
    )


@record_export
def export_video(
    output_path: str,
    video_path: str,
    fps: float,
    start_s: float,
    end_s: float,
    crop: tuple[int, int, int, int] | None,
    colour_lut: np.ndarray | None,
    speed_factor: float,
    df: pd.DataFrame,
    time_col: str,
    data_col: str,
    offset_s: float,
    color: str,
    y_min: float | None,
    y_max: float | None,
    linewidth: float = 1.5,
    data_col2: str = "",
    color2: str = "#d62728",
    deflicker: bool = False,
    progress_callback=None,
    cancel_callback=None,
    trace_transform: TraceTransform | str = TraceTransform.SUBTRACT_FIRST,
    video_width_fraction: float = VIDEO_WIDTH_FRACTION,
    show_scale_bars: bool = True,
    show_time_scale: bool = True,
    show_signal_scale: bool = True,
    scale_bar_font_size: float = EXPORT_SCALE_BAR_FONT_SIZE,
    scale_bar_linewidth: float = EXPORT_SCALE_BAR_LINEWIDTH,
    time_scale_s: float | None = None,
    signal_scale: float | None = None,
    scale_bar_anchor_x: float = 0.8,
    scale_bar_anchor_y: float = 0.5,
    plot_background: str = "white",
    orientation: str = "side_by_side",
    time_scale_label: str = "",
    signal_scale_label: str = "",
    audit_context: dict | None = None,
    run_details: dict | None = None,
    rotation: int = 0,
    max_change: float | None = None,
    pin_scale_bars_to_axes: bool | None = None,
    legend_labels: dict[str, str] | None = None,
) -> None:
    """
    Write a side-by-side video to output_path.

    Left side : cropped video frames read on-demand from video_path.
    Right side : growing data trace rendered from matplotlib.
    Output height is normalised to 540 pixels; video/trace width is 60/40.

    Parameters
    ----------
    output_path  : destination .mp4 file
    video_path   : source video file
    fps          : original video fps
    start_s      : clip start time in seconds
    end_s        : clip end time in seconds
    crop         : (x1, y1, x2, y2) crop rect in video pixel coords, or None
    colour_lut   : pre-built 256-element uint8 LUT, or None
    speed_factor : playback speed multiplier for the output video
    df           : full dataframe
    time_col     : name of the time column (seconds)
    data_col     : name of the data column to plot
    offset_s     : data offset — added to video time to get data time
    color        : trace colour (hex string)
    y_min/y_max  : fixed y-axis limits, or None for auto
    max_change   : optional fixed range from the transformed minimum to minimum + max_change
    pin_scale_bars_to_axes: pin automatically for a fixed range unless explicitly overridden
    data_col2    : optional 2nd signal overlaid on the same axis ("" = none)
    legend_labels: optional mapping from source column names to displayed legend labels
    color2       : colour for the 2nd signal
    deflicker    : if True, normalise each frame's brightness to reduce flicker
    """
    rotation = validate_rotation(rotation)
    if os.path.normcase(os.path.realpath(output_path)) == os.path.normcase(
        os.path.realpath(video_path)
    ) or (os.path.exists(output_path) and os.path.samefile(output_path, video_path)):
        raise ValueError("The output must not replace the source video. Choose another filename.")
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("FPS must be a positive finite number.")
    if not np.isfinite(speed_factor) or speed_factor <= 0:
        raise ValueError("Playback speed must be positive.")
    if not np.isfinite(start_s) or not np.isfinite(end_s) or start_s < 0 or end_s <= start_s:
        raise ValueError("Export end time must be greater than its non-negative start time.")
    if not np.isfinite(offset_s):
        raise ValueError("Data offset must be finite.")
    if max_change is not None and (not np.isfinite(max_change) or max_change <= 0):
        raise ValueError("Maximum change must be a positive finite number.")
    if pin_scale_bars_to_axes is None:
        pin_scale_bars_to_axes = max_change is not None
    if not np.isfinite(video_width_fraction) or not 0.1 <= video_width_fraction <= 0.9:
        raise ValueError("Video width fraction must be between 0.1 and 0.9.")
    if orientation not in {"side_by_side", "stacked"}:
        raise ValueError("Invalid orientation.")
    if (
        not np.isfinite(scale_bar_font_size)
        or not np.isfinite(scale_bar_linewidth)
        or scale_bar_font_size <= 0
        or scale_bar_linewidth <= 0
    ):
        raise ValueError("Scale-bar styling must be positive.")
    if time_scale_s is not None and (not np.isfinite(time_scale_s) or time_scale_s <= 0):
        raise ValueError("Time scale length must be positive when specified.")
    if signal_scale is not None and (not np.isfinite(signal_scale) or signal_scale <= 0):
        raise ValueError("Signal scale length must be positive when specified.")
    if not 0 <= scale_bar_anchor_x <= 1 or not 0 <= scale_bar_anchor_y <= 1:
        raise ValueError("Scale-bar anchors must be between 0 and 1.")
    if (y_min is None) != (y_max is None):
        raise ValueError("Fixed y-axis limits require both a minimum and maximum.")
    if y_min is not None and y_max is not None:
        if not np.isfinite(y_min) or not np.isfinite(y_max) or y_min >= y_max:
            raise ValueError("Fixed y-axis minimum must be finite and less than maximum.")
    # ``plot_background`` remains accepted for compatibility with old project
    # documents and integrations, but exported plots are always white.
    colours = (("trace", color), ("second trace", color2))
    for name, value in colours:
        if not is_color_like(value):
            raise ValueError(f"Invalid {name} colour: {value}")
    if time_col not in df.columns:
        raise ValueError(f"Selected time column not found: {time_col}")
    selected_columns = [data_col, *([data_col2] if data_col2 else [])]
    validate_time_series(cast(pd.Series, df[time_col]))
    validate_data_columns(df, selected_columns)
    window_mask = (df[time_col] >= start_s + offset_s) & (df[time_col] <= end_s + offset_s)
    if not bool(window_mask.any()):
        raise ValueError("No data overlap the selected video clip and offset.")
    # Keep the encoded frame rate conventional for macOS/media-player compatibility.
    # Playback speed is produced by resampling the source timeline below, rather
    # than creating files with extreme rates such as 240 or 480 FPS.
    output_fps = min(fps, 60.0)
    if not np.isfinite(output_fps) or output_fps <= 0:
        raise ValueError("Output FPS must be positive and finite.")
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(output_dir):
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{os.path.basename(output_path)}.", suffix=".mp4", dir=output_dir, delete=False
    )
    temp_path = handle.name
    handle.close()
    os.remove(temp_path)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise OSError(f"Could not open video: {video_path}")

    writer = None
    try:
        source_fps = float(cap.get(cv2.CAP_PROP_FPS))
        source_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if source_fps <= 0 or source_frames <= 0:
            raise OSError("Video duration metadata are unavailable or invalid.")
        duration_s = source_frames / source_fps
        if end_s > duration_s + 0.5 / source_fps:
            raise ValueError(f"Export end time exceeds the video duration ({duration_s:.3f} s).")
        start_frame = _frame_at(start_s, source_fps)
        end_frame = min(source_frames, _ceil_frames(end_s * source_fps))
        total_frames = max(1, _ceil_frames((end_s - start_s) * output_fps / speed_factor))

        # Read one frame to get dimensions after crop
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        ret, sample = cap.read()
        if not ret:
            raise OSError("Could not read frames from video.")
        sample = rotate_frame(sample, rotation)
        if crop is not None:
            if len(crop) != 4 or not all(isinstance(v, int | np.integer) for v in crop):
                raise ValueError("Crop must contain four integer pixel coordinates.")
            x1, y1, x2, y2 = crop
            frame_h, frame_w = sample.shape[:2]
            if not (0 <= x1 < x2 <= frame_w and 0 <= y1 < y2 <= frame_h):
                raise ValueError("Crop must lie within the source video frame.")
            sample = sample[y1:y2, x1:x2]
        src_h, src_w = sample.shape[:2]
        if src_h <= 0 or src_w <= 0:
            raise OSError("Crop produced an empty frame.")
        g = composition_geometry(src_w, src_h, video_width_fraction, orientation, EXPORT_HEIGHT_PX)
        vid_w, vid_h, plot_w, plot_h, out_w, out_h = (
            g.video_width,
            g.video_height,
            g.plot_width,
            g.plot_height,
            g.output_width,
            g.output_height,
        )
        writer = _open_video_writer(temp_path, output_fps, (out_w, out_h))
        if writer is None:
            raise OSError(f"Could not open video writer for: {output_path}")
        if run_details is not None:
            run_details.update(
                source_fps=source_fps,
                source_frames=source_frames,
                output_fps=output_fps,
                output_frames=total_frames,
                width=out_w,
                height=out_h,
            )

        # Build the matplotlib figure once, reuse for all frames.
        dpi = 100
        fig = Figure(figsize=(plot_w / dpi, plot_h / dpi), dpi=dpi)
        left, right, bottom, top = scale_label_figure_margins(
            plot_w / dpi,
            plot_h / dpi,
            scale_bar_font_size,
            scale_bar_linewidth,
            (scale_bar_anchor_x, scale_bar_anchor_y),
            show_time=show_scale_bars and show_time_scale,
            show_signal=show_scale_bars and show_signal_scale,
        )
        fig.subplots_adjust(
            left=left,
            right=1.0 - right,
            bottom=bottom,
            top=1.0 - top,
        )
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        ax.set_facecolor("#ffffff")
        fig.patch.set_facecolor("#ffffff")

        # Columns to plot (primary + optional overlay), with a shared y-range.
        cols = [data_col]
        if data_col2 and data_col2 in df.columns:
            cols.append(data_col2)
        colors = [color, color2]
        alpha = 0.5 if len(cols) > 1 else 1.0

        # Auto mode shifts the displayed minimum to zero. A maximum-change
        # range instead keeps transformed values (which may be negative) and
        # caps the interval at minimum + max_change.
        mask = (df[time_col] >= start_s + offset_s) & (df[time_col] <= end_s + offset_s)
        window = df.loc[mask, [time_col, *cols]].copy()
        if not window.empty:
            window = window.sort_values(time_col)
            raw_t_values = window[time_col].to_numpy(dtype=float)
            display_t_values = raw_t_values
            raw_values = [window[c].to_numpy(dtype=float) for c in cols]
            raw_window_y_min = window_minimum(raw_values)
            if max_change is None:
                shifted_values, _transformed_window_baseline = normalise_window_values(
                    raw_values, trace_transform
                )
                finite_y = np.concatenate([v[np.isfinite(v)] for v in shifted_values])
                data_y_min = float(np.min(finite_y)) if finite_y.size else 0.0
                data_y_max = float(np.max(finite_y)) if finite_y.size else 1.0
            else:
                shifted_values = [
                    transform_values(values, trace_transform) for values in raw_values
                ]
                data_y_min = window_minimum(shifted_values)
                data_y_max = data_y_min + max_change
                shifted_values = [np.minimum(values, data_y_max) for values in shifted_values]
            x_min = float(np.min(display_t_values))
            x_max = float(np.max(display_t_values))
            data_time_bounds = [float(raw_t_values[0]), float(raw_t_values[-1])]
        else:
            raw_t_values = np.array([], dtype=float)
            display_t_values = raw_t_values
            shifted_values = [np.array([], dtype=float) for _ in cols]
            data_y_min, data_y_max = 0.0, 1.0
            x_min, x_max = start_s + offset_s, end_s + offset_s
            data_time_bounds = [x_min, x_max]
            raw_window_y_min = 0.0

        x_range = x_max - x_min
        if not np.isfinite(x_range) or x_range <= 0:
            x_range = 1.0
            x_min -= 0.5
            x_max += 0.5

        fixed_y_min = y_min - raw_window_y_min if y_min is not None else data_y_min
        fixed_y_max = y_max - raw_window_y_min if y_max is not None else data_y_max
        if max_change is not None:
            fixed_y_min, fixed_y_max = data_y_min, data_y_min + max_change
        data_y_limits = (fixed_y_min, fixed_y_max)
        y_range = fixed_y_max - fixed_y_min
        if not np.isfinite(y_range) or y_range <= 0:
            centre = fixed_y_min if np.isfinite(fixed_y_min) else 0.0
            y_range = max(abs(centre) * 0.2, 1.0)
            fixed_y_min = centre - y_range / 2
            fixed_y_max = centre + y_range / 2

        x_bar_len = time_scale_s or _nice_scale(x_range * 0.15, _TIME_INCREMENTS)
        y_bar_len = signal_scale or _nice_scale(y_range * 0.2, _SIGNAL_INCREMENTS)
        base_comp = axes_composition(
            x_min,
            x_max,
            fixed_y_min,
            fixed_y_max,
            time_scale=x_bar_len,
            signal_scale=y_bar_len,
            anchor=(scale_bar_anchor_x, scale_bar_anchor_y),
        )
        if max_change is not None and pin_scale_bars_to_axes:
            fixed_y_min, fixed_y_max = scale_bar_band_limits(
                data_y_limits[0],
                data_y_limits[1],
                base_comp.signal_scale,
                scale_bar_anchor_y,
                show_time_scale=show_scale_bars and show_time_scale,
                show_signal_scale=show_scale_bars and show_signal_scale,
            )
            base_comp = replace(base_comp, ylim=(fixed_y_min, fixed_y_max))
        if run_details is not None:
            run_details.update(
                data_time_bounds=data_time_bounds,
                y_limits=[fixed_y_min, fixed_y_max],
                data_y_range=list(data_y_limits),
                max_change=max_change,
                pin_scale_bars_to_axes=pin_scale_bars_to_axes,
                time_scale_s=x_bar_len,
                signal_scale=y_bar_len,
                trace_baselines={c: _first_finite(window[c].to_numpy()) for c in cols},
            )
        anchor = (scale_bar_anchor_x, scale_bar_anchor_y)
        if pin_scale_bars_to_axes:
            placed_times, shifted_values, comp, placement = align_trace_origin_to_pinned_bars(
                base_comp,
                [display_t_values],
                shifted_values,
                anchor,
                preserve_time=scale_bar_anchor_x < 0.5,
                preserve_signal_baseline=max_change is not None or scale_bar_anchor_x < 0.5,
                clear_time_bar=show_scale_bars and show_time_scale,
                allow_signal_shift=max_change is None,
                fixed_ylim=(fixed_y_min, fixed_y_max) if max_change is not None else None,
            )
        else:
            placed_times, shifted_values, comp, placement = place_trace_origin(
                base_comp,
                [display_t_values],
                shifted_values,
                preserve_signal_baseline=max_change is not None,
                clear_time_bar=show_scale_bars and show_time_scale,
                allow_signal_shift=max_change is None,
            )
            if max_change is not None:
                comp = replace(comp, ylim=(fixed_y_min, fixed_y_max))
        placed_t_values = placed_times[0]
        ax.set_xlim(*comp.xlim)
        ax.set_ylim(*comp.ylim)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        if show_scale_bars and (show_time_scale or show_signal_scale):
            _draw_scale_bars(
                ax,
                comp.bar_x,
                comp.bar_y,
                comp.time_scale,
                comp.signal_scale,
                _shared_y_unit_for_columns(cols),
                scale_bar_font_size,
                scale_bar_linewidth,
                show_time_scale,
                show_signal_scale,
                comp.time_direction,
                comp.signal_direction,
                time_scale_label,
                signal_scale_label,
            )

        lines = []
        for i, c in enumerate(cols):
            (line,) = ax.plot(
                [],
                [],
                color=colors[i],
                linewidth=linewidth,
                alpha=alpha,
                label=str((legend_labels or {}).get(c, c)),
            )
            lines.append(line)
        if len(lines) > 1:
            ax.legend(loc="upper right", fontsize=8, framealpha=0.6)
        # Pre-pass: sample a reference brightness for the optional de-flicker.
        reference_brightness = None
        if deflicker:

            def _read(idx: int):
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, f = cap.read()
                return rotate_frame(f, rotation) if ok else None

            reference_brightness = compute_reference_brightness(
                _read, start_frame, end_frame, crop=crop
            )

        speed_text = f"{speed_factor:g}x"
        if run_details is not None:
            run_details["deflicker_reference_brightness"] = reference_brightness

        # Seek back to start and iterate frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        next_source_frame = start_frame
        cached_source_frame = -1
        cached_frame = None

        try:
            for frame_index in range(total_frames):
                if cancel_callback is not None and cancel_callback():
                    raise ExportCancelled("Export cancelled.")
                source_time_s, wanted_frame = _output_frame_source(
                    frame_index,
                    start_s=start_s,
                    speed_factor=speed_factor,
                    output_fps=output_fps,
                    source_fps=source_fps,
                    start_frame=start_frame,
                    end_frame=end_frame,
                )
                if wanted_frame == cached_source_frame and cached_frame is not None:
                    frame = cached_frame.copy()
                else:
                    if wanted_frame != next_source_frame:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, wanted_frame)
                    ret, frame = cap.read()
                    if not ret:
                        raise OSError(f"Could not read source frame {wanted_frame}.")
                    next_source_frame = wanted_frame + 1
                    cached_source_frame = wanted_frame
                    cached_frame = frame.copy()

                # Keep preview/export geometry identical: rotate, crop, normalize brightness.
                frame = rotate_frame(frame, rotation)
                if crop is not None:
                    x1, y1, x2, y2 = crop
                    frame = frame[y1:y2, x1:x2]
                if deflicker:
                    frame = correct_frame_brightness(frame, reference_brightness)
                if colour_lut is not None:
                    frame = apply_lut(frame, colour_lut)

                # Resize if needed (shouldn't happen but be safe)
                if frame.shape[:2] != (vid_h, vid_w):
                    frame = cv2.resize(frame, (vid_w, vid_h))

                current_time_s = source_time_s + offset_s
                end_idx = int(np.searchsorted(raw_t_values, current_time_s, side="right"))
                t_data = placed_t_values[:end_idx]
                line_data = [(t_data, values[:end_idx]) for values in shifted_values]

                plot_frame = _render_plot_frame(canvas, lines, line_data, plot_h, plot_w)
                combined = np.concatenate(
                    [frame, plot_frame], axis=1 if orientation == "side_by_side" else 0
                )

                cv2.putText(
                    combined,
                    speed_text,
                    (out_w - 80, out_h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 0, 0),
                    2,
                )

                writer.write(combined)
                if progress_callback is not None:
                    progress_callback(frame_index + 1, total_frames)
        finally:
            writer.release()
            writer = None

        if cancel_callback is not None and cancel_callback():
            raise ExportCancelled("Export cancelled.")

        check = cv2.VideoCapture(temp_path)
        try:
            actual = (
                int(check.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(check.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
            if run_details is not None:
                code = int(check.get(cv2.CAP_PROP_FOURCC))
                run_details["codec"] = "".join(chr((code >> (8 * i)) & 255) for i in range(4))
            if (
                not check.isOpened()
                or int(check.get(cv2.CAP_PROP_FRAME_COUNT)) != total_frames
                or actual != (out_w, out_h)
            ):
                raise OSError("The exported video could not be verified after writing.")
        finally:
            check.release()
        for source in (run_details or {}).get("sources", {}).values():
            stat = os.stat(source["path"])
            if (stat.st_size, stat.st_mtime_ns) != (source["size_bytes"], source["modified_ns"]):
                raise OSError("A source file changed during export. Please reload and try again.")
        os.replace(temp_path, output_path)

    finally:
        if writer is not None:
            writer.release()
        cap.release()
        try:
            os.remove(temp_path)
        except OSError:
            pass
