"""
Brightness-flicker correction.

Some recordings pulse lighter/darker frame-to-frame (LED / illumination flicker
or exposure hunting). This module flattens that out by rescaling each frame's
overall luminance to a stable reference level.

It is deliberately cheap: one reference pass over a handful of sampled frames,
then a single mean + gain multiply per frame at playback / export time. It does
NOT correct geometric camera shake — only global brightness pulsing.
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np


def compute_reference_brightness(
    read_frame: Callable[[int], np.ndarray | None],
    start_frame: int,
    end_frame: int,
    crop: tuple[int, int, int, int] | None = None,
    n_samples: int = 20,
) -> float | None:
    """
    Estimate the target brightness for a clip by sampling frames across it.

    read_frame : callable(frame_index) -> BGR frame (or None on failure).
    crop       : (x1, y1, x2, y2) — measured on the same region that will be
                 corrected, so the reference matches what the viewer sees.

    Returns the median mean-luminance of the sampled frames, or None if no
    frame could be read (in which case correction is skipped).
    """
    span = max(1, end_frame - start_frame)
    count = max(1, min(n_samples, span))
    indices = np.linspace(start_frame, end_frame - 1, count).astype(int)

    values: list[float] = []
    for idx in indices:
        frame = read_frame(int(idx))
        if frame is None:
            continue
        if crop is not None:
            x1, y1, x2, y2 = crop
            frame = frame[y1:y2, x1:x2]
        values.append(float(frame.mean()))

    return float(np.median(values)) if values else None


def correct_frame_brightness(
    frame: np.ndarray,
    target_mean: float | None,
    min_gain: float = 0.5,
    max_gain: float = 2.0,
) -> np.ndarray:
    """
    Scale a frame's luminance towards target_mean.

    Computes a single gain = target_mean / frame_mean and applies it uniformly
    across channels (preserving colour balance). The gain is clamped so a very
    dark / blown-out frame can't be amplified into garbage. If target_mean is
    None or the frame is essentially black, the frame is returned unchanged.
    """
    if target_mean is None:
        return frame
    current = float(frame.mean())
    if current <= 1e-6:
        return frame
    gain = target_mean / current
    gain = max(min_gain, min(max_gain, gain))
    return cv2.convertScaleAbs(frame, alpha=gain, beta=0)
