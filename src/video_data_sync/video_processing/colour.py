"""
Per-frame colour correction.

All functions operate on BGR numpy arrays (cv2 convention) and return a
corrected copy. No GUI imports.

Global parameters (applied to all channels equally)
----------------------------------------------------
brightness : int, range -100 to +100 (0 = no change)
contrast   : float, range 0.5 to 2.0 (1.0 = no change)
gamma      : float, range 0.2 to 3.0 (1.0 = no change)

Per-channel gains (for white-balance / colour-cast correction)
--------------------------------------------------------------
r_gain, g_gain, b_gain : float, range 0.0 to 2.0 (1.0 = no change)
    Multiplied against the respective channel after global adjustments.
    Useful for correcting sodium-lighting orange casts etc.
"""

from __future__ import annotations

import cv2
import numpy as np


def build_lut(
    brightness: int = 0,
    contrast: float = 1.0,
    gamma: float = 1.0,
    r_gain: float = 1.0,
    g_gain: float = 1.0,
    b_gain: float = 1.0,
) -> np.ndarray | None:
    """
    Build a (256, 3) uint8 LUT for per-channel colour correction.

    Returns an array with shape (256, 3) where columns are B, G, R
    (cv2 channel order). Pass to apply_lut().

    If all parameters are identity values, returns None instead so callers
    can skip the LUT step entirely.
    """
    identity = (
        brightness == 0
        and contrast == 1.0
        and gamma == 1.0
        and r_gain == 1.0
        and g_gain == 1.0
        and b_gain == 1.0
    )
    if identity:
        return None

    table = np.arange(256, dtype=np.float32)
    table = (table - 128) * contrast + 128 + brightness
    table = np.clip(table, 0, 255)
    if gamma != 1.0:
        table = np.power(table / 255.0, 1.0 / gamma) * 255.0
    table = np.clip(table, 0, 255)

    # Per-channel gain (BGR order)
    b_table = np.clip(table * b_gain, 0, 255).astype(np.uint8)
    g_table = np.clip(table * g_gain, 0, 255).astype(np.uint8)
    r_table = np.clip(table * r_gain, 0, 255).astype(np.uint8)

    # Stack as (256, 1, 3) for cv2.LUT
    lut = np.stack([b_table, g_table, r_table], axis=1).reshape(256, 1, 3)
    return lut


def apply_correction(
    frame: np.ndarray,
    brightness: int = 0,
    contrast: float = 1.0,
    gamma: float = 1.0,
    r_gain: float = 1.0,
    g_gain: float = 1.0,
    b_gain: float = 1.0,
) -> np.ndarray:
    """
    Apply colour correction to a BGR frame. Returns a corrected copy.
    """
    lut = build_lut(brightness, contrast, gamma, r_gain, g_gain, b_gain)
    if lut is None:
        return frame
    return apply_lut(frame, lut)


def apply_lut(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Apply a pre-built (256, 1, 3) LUT to a BGR frame."""
    return cv2.LUT(frame, lut)
