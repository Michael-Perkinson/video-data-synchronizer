"""Pure, shared trace transformations used by preview and export."""

from enum import StrEnum

import numpy as np


class TraceTransform(StrEnum):
    RAW = "raw"
    SUBTRACT_FIRST = "subtract_first"


def window_minimum(values: list[np.ndarray]) -> float:
    """Return the minimum finite raw value across all traces in a window."""
    finite_arrays = [
        np.asarray(array, dtype=float)[np.isfinite(np.asarray(array, dtype=float))]
        for array in values
    ]
    finite = np.concatenate(finite_arrays) if finite_arrays else np.array([], dtype=float)
    return float(np.min(finite)) if finite.size else 0.0


def transform_values(
    values: np.ndarray, transform: TraceTransform | str = TraceTransform.SUBTRACT_FIRST
) -> np.ndarray:
    """Return transformed float values without mutating the input array."""
    result = np.asarray(values, dtype=float).copy()
    mode = TraceTransform(transform)
    if mode is TraceTransform.RAW:
        return result
    finite = np.flatnonzero(np.isfinite(result))
    if finite.size:
        result -= result[finite[0]]
    return result


def normalise_window_values(
    values: list[np.ndarray], transform: TraceTransform | str = TraceTransform.SUBTRACT_FIRST
) -> tuple[list[np.ndarray], float]:
    """Shift all traces so the minimum finite value in the window is zero.

    The returned baseline is the value removed after the configured per-trace
    transform. Keeping this operation shared makes the preview and exporter
    use identical vertical positioning.
    """
    transformed = [transform_values(array, transform) for array in values]
    baseline = window_minimum(transformed)
    return [array - baseline for array in transformed], baseline
