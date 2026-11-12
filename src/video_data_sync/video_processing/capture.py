"""
VideoCapture wrapper.

Provides a clean interface over cv2.VideoCapture with guaranteed cleanup,
frame seeking, and conversion to Qt-compatible images.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PySide6.QtGui import QImage, QPixmap


class VideoCapture:
    """
    Thin wrapper around cv2.VideoCapture.

    Usage:
        cap = VideoCapture(path)
        pixmap = cap.pixmap_at(frame_index)
        cap.release()

    Or as a context manager:
        with VideoCapture(path) as cap:
            pixmap = cap.pixmap_at(0)
    """

    def __init__(self, path: str) -> None:
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise OSError(f"Cannot open video: {path}")
        self.path = path

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def frame_count(self) -> int:
        return int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

    @property
    def fps(self) -> float:
        return self._cap.get(cv2.CAP_PROP_FPS)

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0

    @property
    def width(self) -> int:
        return int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    @property
    def height(self) -> int:
        return int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # ------------------------------------------------------------------
    # Frame reading
    # ------------------------------------------------------------------

    def read_frame(self, frame_index: int) -> np.ndarray | None:
        """Read a single BGR frame at the given index (random access, seeks)."""
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame = self._cap.read()
        return frame if ret else None

    def set_pos(self, frame_index: int) -> None:
        """Seek so the next read_next() returns the given frame index."""
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

    def read_next(self) -> np.ndarray | None:
        """Read the next BGR frame sequentially (no seek). Returns None at EOF."""
        ret, frame = self._cap.read()
        return frame if ret else None

    def read_frame_at_time(self, time_s: float) -> np.ndarray | None:
        """Read the frame at the given time in seconds."""
        frame_index = int(time_s * self.fps)
        return self.read_frame(frame_index)

    def frame_index_at_time(self, time_s: float) -> int:
        return max(0, min(int(time_s * self.fps), self.frame_count - 1))

    def time_at_frame(self, frame_index: int) -> float:
        return frame_index / self.fps if self.fps > 0 else 0.0

    # ------------------------------------------------------------------
    # Segment extraction
    # ------------------------------------------------------------------

    def extract_frames(
        self,
        start_s: float,
        end_s: float,
        crop: tuple[int, int, int, int] | None = None,
        colour_lut: np.ndarray | None = None,
    ) -> list[np.ndarray]:
        """
        Extract all frames between start_s and end_s (in seconds).

        crop       : (x1, y1, x2, y2) in original video pixel coordinates.
        colour_lut : optional 256-element uint8 LUT from video.colour.build_lut().
        Returns BGR frames.
        """
        start_frame = self.frame_index_at_time(start_s)
        # `end_s` is an exclusive time bound. Include every frame whose
        # presentation timestamp is before it, including the final video frame.
        end_frame = min(self.frame_count, math.ceil(end_s * self.fps))

        self._cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frames: list[np.ndarray] = []

        while True:
            pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
            if pos >= end_frame:
                break
            ret, frame = self._cap.read()
            if not ret:
                break
            if crop is not None:
                x1, y1, x2, y2 = crop
                frame = frame[y1:y2, x1:x2]
            if colour_lut is not None:
                frame = cv2.LUT(frame, colour_lut)
            frames.append(frame)

        return frames

    # ------------------------------------------------------------------
    # Qt conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def frame_to_qimage(frame: np.ndarray) -> QImage:
        """Convert a BGR numpy frame to a QImage (RGB888)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        return QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

    @staticmethod
    def frame_to_pixmap(frame: np.ndarray) -> QPixmap:
        return QPixmap.fromImage(VideoCapture.frame_to_qimage(frame))

    def pixmap_at(self, frame_index: int) -> QPixmap | None:
        frame = self.read_frame(frame_index)
        if frame is None:
            return None
        return self.frame_to_pixmap(frame)

    def pixmap_at_time(self, time_s: float) -> QPixmap | None:
        frame = self.read_frame_at_time(time_s)
        if frame is None:
            return None
        return self.frame_to_pixmap(frame)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def release(self) -> None:
        if self._cap.isOpened():
            self._cap.release()

    def __enter__(self) -> VideoCapture:
        return self

    def __exit__(self, *_) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()
