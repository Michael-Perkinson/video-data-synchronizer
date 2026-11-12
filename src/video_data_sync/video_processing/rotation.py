"""Quarter-turn rotation helpers shared by preview and export paths."""

from __future__ import annotations

from numbers import Integral

import cv2
import numpy as np

SUPPORTED_ROTATIONS = (0, 90, 180, 270)


def validate_rotation(degrees: object) -> int:
    """Return a supported clockwise angle or raise a descriptive ``ValueError``."""
    if (
        isinstance(degrees, bool)
        or not isinstance(degrees, Integral)
        or degrees not in SUPPORTED_ROTATIONS
    ):
        raise ValueError("Rotation must be 0, 90, 180, or 270 degrees clockwise.")
    return int(degrees)


def rotate_frame(frame: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate a BGR frame clockwise without resampling its pixels."""
    angle = validate_rotation(degrees)
    if angle == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def rotated_dimensions(width: int, height: int, degrees: int) -> tuple[int, int]:
    """Return frame dimensions after rotation."""
    angle = validate_rotation(degrees)
    return (height, width) if angle in (90, 270) else (width, height)


def rotate_crop(
    crop: tuple[int, int, int, int], width: int, height: int, degrees: int
) -> tuple[int, int, int, int]:
    """Rotate crop-edge coordinates clockwise with the image."""
    angle = validate_rotation(degrees)
    x1, y1, x2, y2 = crop
    if angle == 90:
        return height - y2, x1, height - y1, x2
    if angle == 180:
        return width - x2, height - y2, width - x1, height - y1
    if angle == 270:
        return y1, width - x2, y2, width - x1
    return x1, y1, x2, y2
