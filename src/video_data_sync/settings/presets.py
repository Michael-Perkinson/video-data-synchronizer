"""Versioned JSON presets for reusable appearance and export preferences.

Presets deliberately contain no recording identity or analysis state. In
particular, paths, clip times, crop rectangles, offsets, baselines and absolute
y-axis limits belong in a project file, never in an appearance preset. An
optional graph-range percentage is a reusable plot preference that follows the
current signal span.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from matplotlib.colors import is_color_like

PRESET_KIND = "video-data-sync-appearance-preset"
PRESET_SCHEMA_VERSION = 1

_ROOT_KEYS = {"kind", "schema_version", "traces", "composition", "export", "video_colour"}
_TRACE_KEYS = {
    "colors_by_column",
    "legend_labels",
    "preferred_channels",
    "line_width",
    "transform",
    "overlay",
}
_COMPOSITION_KEYS = {"layout", "panel_proportions", "advanced"}
_ADVANCED_KEYS = {
    "scale_font_size",
    "scale_line_width",
    "show_time_scale",
    "show_signal_scale",
    "time_scale_s",
    "signal_scale",
    "max_change_enabled",
    "max_change_percent",
    "max_change",
    "anchor",
    "background",
    "time_scale_label",
    "signal_scale_label",
}
_EXPORT_KEYS = {"speeds", "deflicker"}
_VIDEO_COLOUR_KEYS = {"brightness", "contrast", "gamma", "r_gain", "g_gain", "b_gain"}
_TRANSFORMS = {"raw", "subtract_first"}
_LAYOUTS = {"side_by_side", "stacked"}
_ANCHORS = {"Top left", "Top right", "Bottom left", "Bottom right"}


class PresetValidationError(ValueError):
    """Raised when an appearance preset is malformed or unsupported."""


def default_preset() -> dict[str, Any]:
    """Return a new, fully populated preset using application defaults."""

    return {
        "kind": PRESET_KIND,
        "schema_version": PRESET_SCHEMA_VERSION,
        "traces": {
            "colors_by_column": {},
            "legend_labels": {},
            "preferred_channels": [],
            "line_width": 1.5,
            "transform": "subtract_first",
            "overlay": False,
        },
        "composition": {
            "layout": "side_by_side",
            "panel_proportions": [3, 2],
            "advanced": {
                "scale_font_size": 16,
                "scale_line_width": 2.8,
                "show_time_scale": True,
                "show_signal_scale": True,
                "time_scale_s": 0.0,
                "signal_scale": 0.0,
                "max_change_enabled": False,
                "max_change_percent": 70.0,
                "anchor": "Bottom left",
                "background": "#ffffff",
                # Empty means use the automatically generated label.
                "time_scale_label": "",
                "signal_scale_label": "",
            },
        },
        "export": {"speeds": [1.0], "deflicker": False},
        "video_colour": {
            "brightness": 0,
            "contrast": 1.0,
            "gamma": 1.0,
            "r_gain": 1.0,
            "g_gain": 1.0,
            "b_gain": 1.0,
        },
    }


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PresetValidationError(f"{path} must be an object.")
    if not all(isinstance(key, str) for key in value):
        raise PresetValidationError(f"{path} keys must be strings.")
    return value


def _exact_keys(
    value: dict[str, Any],
    expected: set[str],
    path: str,
    *,
    optional: set[str] | frozenset[str] = frozenset(),
) -> None:
    missing = (expected - optional) - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise PresetValidationError(f"{path} is missing: {', '.join(sorted(missing))}.")
    if unknown:
        raise PresetValidationError(f"{path} has unknown fields: {', '.join(sorted(unknown))}.")


def _number(value: object, path: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PresetValidationError(f"{path} must be a number.")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise PresetValidationError(f"{path} must be between {low:g} and {high:g}.")
    return result


def _integer(value: object, path: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PresetValidationError(f"{path} must be a whole number.")
    if not low <= value <= high:
        raise PresetValidationError(f"{path} must be between {low} and {high}.")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise PresetValidationError(f"{path} must be true or false.")
    return value


def _short_text(value: object, path: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 128 or (not empty and not value.strip()):
        qualifier = "a string of at most 128 characters" if empty else "a non-empty string"
        raise PresetValidationError(f"{path} must be {qualifier}.")
    return value


def validate_preset(document: object) -> dict[str, Any]:
    """Validate and normalize a decoded preset, returning a detached copy.

    Unknown fields are rejected. Besides catching spelling errors, this makes it
    impossible to accidentally put recording-specific project fields in a
    preset without a deliberate schema change.
    """

    root = _object(document, "preset")
    _exact_keys(root, _ROOT_KEYS, "preset")
    if root["kind"] != PRESET_KIND:
        raise PresetValidationError("This JSON file is not an appearance preset.")
    if (
        isinstance(root["schema_version"], bool)
        or not isinstance(root["schema_version"], int)
        or root["schema_version"] != PRESET_SCHEMA_VERSION
    ):
        raise PresetValidationError(
            f"Unsupported preset schema version: {root['schema_version']!r}."
        )

    traces = _object(root["traces"], "traces")
    _exact_keys(traces, _TRACE_KEYS, "traces", optional={"legend_labels"})
    colors = _object(traces["colors_by_column"], "traces.colors_by_column")
    normalized_colors: dict[str, str] = {}
    for column, color in colors.items():
        clean_column = _short_text(column, "trace column name")
        clean_color = _short_text(color, f"traces.colors_by_column[{column!r}]")
        if not is_color_like(clean_color):
            raise PresetValidationError(
                f"traces.colors_by_column[{column!r}] is not a valid colour."
            )
        normalized_colors[clean_column] = clean_color
    labels = _object(traces.get("legend_labels", {}), "traces.legend_labels")
    normalized_labels = {
        _short_text(column, "legend label column name"): _short_text(
            label, f"traces.legend_labels[{column!r}]"
        )
        for column, label in labels.items()
    }
    channels = traces["preferred_channels"]
    if not isinstance(channels, list) or len(channels) > 2:
        raise PresetValidationError("traces.preferred_channels must be a list of up to two names.")
    normalized_channels = [
        _short_text(channel, f"traces.preferred_channels[{index}]")
        for index, channel in enumerate(channels)
    ]
    if len(set(normalized_channels)) != len(normalized_channels):
        raise PresetValidationError("traces.preferred_channels cannot contain duplicates.")
    transform = traces["transform"]
    if not isinstance(transform, str) or transform not in _TRANSFORMS:
        raise PresetValidationError("traces.transform must be 'raw' or 'subtract_first'.")

    composition = _object(root["composition"], "composition")
    _exact_keys(composition, _COMPOSITION_KEYS, "composition")
    layout = composition["layout"]
    if not isinstance(layout, str) or layout not in _LAYOUTS:
        raise PresetValidationError("composition.layout must be 'side_by_side' or 'stacked'.")
    proportions = composition["panel_proportions"]
    if not isinstance(proportions, list) or len(proportions) != 2:
        raise PresetValidationError("composition.panel_proportions must contain two numbers.")
    normalized_proportions = [
        _integer(value, f"composition.panel_proportions[{index}]", 1, 1_000_000)
        for index, value in enumerate(proportions)
    ]
    advanced = _object(composition["advanced"], "composition.advanced")
    _exact_keys(
        advanced,
        _ADVANCED_KEYS,
        "composition.advanced",
        optional={"max_change_enabled", "max_change_percent", "max_change"},
    )
    anchor = advanced["anchor"]
    if not isinstance(anchor, str) or anchor not in _ANCHORS:
        raise PresetValidationError(
            f"composition.advanced.anchor must be one of {sorted(_ANCHORS)}."
        )
    background = _short_text(advanced["background"], "composition.advanced.background")
    if not is_color_like(background):
        raise PresetValidationError("composition.advanced.background is not a valid colour.")

    export = _object(root["export"], "export")
    _exact_keys(export, _EXPORT_KEYS, "export")
    speeds = export["speeds"]
    if not isinstance(speeds, list) or not 1 <= len(speeds) <= 32:
        raise PresetValidationError("export.speeds must contain between 1 and 32 speeds.")
    normalized_speeds = [
        _number(speed, f"export.speeds[{index}]", 0.05, 16.0) for index, speed in enumerate(speeds)
    ]
    if len(set(normalized_speeds)) != len(normalized_speeds):
        raise PresetValidationError("export.speeds cannot contain duplicates.")

    colour = _object(root["video_colour"], "video_colour")
    _exact_keys(colour, _VIDEO_COLOUR_KEYS, "video_colour")

    return {
        "kind": PRESET_KIND,
        "schema_version": PRESET_SCHEMA_VERSION,
        "traces": {
            "colors_by_column": normalized_colors,
            "legend_labels": normalized_labels,
            "preferred_channels": normalized_channels,
            "line_width": _number(traces["line_width"], "traces.line_width", 0.1, 20.0),
            "transform": transform,
            "overlay": _boolean(traces["overlay"], "traces.overlay"),
        },
        "composition": {
            "layout": layout,
            "panel_proportions": normalized_proportions,
            "advanced": {
                "scale_font_size": _integer(
                    advanced["scale_font_size"], "composition.advanced.scale_font_size", 6, 72
                ),
                "scale_line_width": _number(
                    advanced["scale_line_width"], "composition.advanced.scale_line_width", 0.1, 20
                ),
                "show_time_scale": _boolean(
                    advanced["show_time_scale"], "composition.advanced.show_time_scale"
                ),
                "show_signal_scale": _boolean(
                    advanced["show_signal_scale"], "composition.advanced.show_signal_scale"
                ),
                "time_scale_s": _number(
                    advanced["time_scale_s"], "composition.advanced.time_scale_s", 0, 1e12
                ),
                "signal_scale": _number(
                    advanced["signal_scale"], "composition.advanced.signal_scale", 0, 1e12
                ),
                "max_change_enabled": _boolean(
                    advanced.get("max_change_enabled", False),
                    "composition.advanced.max_change_enabled",
                ),
                **(
                    {
                        "max_change": _number(
                            advanced["max_change"],
                            "composition.advanced.max_change",
                            1e-6,
                            1e12,
                        )
                    }
                    if "max_change" in advanced and "max_change_percent" not in advanced
                    else {
                        "max_change_percent": _number(
                            advanced.get("max_change_percent", 70.0),
                            "composition.advanced.max_change_percent",
                            1e-6,
                            100.0,
                        )
                    }
                ),
                "anchor": anchor,
                # Keep accepting this legacy field, but plot backgrounds are
                # intentionally fixed to white in the application.
                "background": "#ffffff",
                "time_scale_label": _short_text(
                    advanced["time_scale_label"],
                    "composition.advanced.time_scale_label",
                    empty=True,
                ),
                "signal_scale_label": _short_text(
                    advanced["signal_scale_label"],
                    "composition.advanced.signal_scale_label",
                    empty=True,
                ),
            },
        },
        "export": {
            "speeds": normalized_speeds,
            "deflicker": _boolean(export["deflicker"], "export.deflicker"),
        },
        "video_colour": {
            "brightness": _integer(colour["brightness"], "video_colour.brightness", -100, 100),
            "contrast": _number(colour["contrast"], "video_colour.contrast", 0.5, 2.0),
            "gamma": _number(colour["gamma"], "video_colour.gamma", 0.2, 3.0),
            "r_gain": _number(colour["r_gain"], "video_colour.r_gain", 0, 2.0),
            "g_gain": _number(colour["g_gain"], "video_colour.g_gain", 0, 2.0),
            "b_gain": _number(colour["b_gain"], "video_colour.b_gain", 0, 2.0),
        },
    }


def load_preset(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate a UTF-8 JSON appearance preset."""

    try:
        with Path(path).open(encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PresetValidationError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."
        ) from exc
    return validate_preset(document)


def save_preset(path: str | os.PathLike[str], document: object) -> None:
    """Validate and atomically replace an appearance preset JSON file."""

    normalized = validate_preset(deepcopy(document))
    destination = Path(path).expanduser().absolute()
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
