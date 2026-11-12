"""
Crop-rectangle selection overlay for a QLabel showing a video frame.

CropOverlay sits on top of the video QLabel. The user clicks and drags to
define a crop rectangle. Coordinates are always returned in original video
pixel space (not display pixel space). The area outside the selection is
dimmed and the selection gets a black-and-white border, so it stays visible
on both dark and bright footage.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QWidget

# About 47 % black over everything outside the selection.
_DIM_COLOUR = QColor(0, 0, 0, 120)
_LIGHT_LINE = QColor(255, 255, 255)
_DARK_LINE = QColor(0, 0, 0)
# Selection sizes (display px, per axis) that get the small and the tiny border rules.
_SMALL_SELECTION_PX = 6
_TINY_SELECTION_PX = 3


def _border_offsets(size: int, room: int) -> tuple[int, int]:
    """Return ``(light, dark)`` line offsets for one side of the selection.

    An offset ``k > 0`` is ``k`` px outside the selection edge, ``0`` is the edge pixel
    and ``k < 0`` is ``-k`` px inside it. ``size`` is the selection's extent across
    this side and ``room`` the number of image pixels outside it.

    * Normal selections: light on the edge, dark just inside it, so the border never
      leaves the selection and shows both lines even for a full-frame selection.
    * Small (4-6 px): light on the edge, dark just outside; inside when there is no room.
    * Tiny (1-3 px): both lines outside so the selection itself stays unpainted, falling
      back to inside positions only where the image edge leaves no room.
    """
    if size > _SMALL_SELECTION_PX:
        return 0, -1
    if size > _TINY_SELECTION_PX:
        return 0, (1 if room >= 1 else -1)
    candidates = [k for k in (1, 2) if k <= room] + [-i for i in range(size)]
    return candidates[0], candidates[1] if len(candidates) > 1 else candidates[0]


def _fill_line(painter: QPainter, x1: int, y1: int, x2: int, y2: int, colour: QColor) -> None:
    """Fill the 1 px horizontal or vertical line between two inclusive end pixels."""
    painter.fillRect(min(x1, x2), min(y1, y2), abs(x2 - x1) + 1, abs(y2 - y1) + 1, colour)


def _axis_to_video(pos: int, start: int, length: int, video_length: int) -> int:
    """Map one display coordinate to video coordinates in ``[0, video_length]``.

    The first display pixel maps to 0 and the last display pixel (or beyond)
    maps to the far frame edge, so a drag to an edge can select the full frame.
    """
    if length <= 0:
        return 0
    if pos >= start + length - 1:
        return video_length
    rel = max(0, pos - start) / length
    return int(rel * video_length)


@dataclass
class CropRect:
    """Crop region in original video pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )

    def is_valid(self) -> bool:
        x1, y1, x2, y2 = self.to_tuple()
        return x2 > x1 and y2 > y1


class CropOverlay(QLabel):
    """
    A QLabel that displays a video frame and allows the user to drag a crop
    rectangle over it.

    Signals:
        crop_changed(CropRect): emitted whenever the crop rect changes.
        crop_confirmed(CropRect): emitted on mouse release.
        pixel_picked(int, int): emitted in pick mode on click — video pixel (x, y).
    """

    crop_changed = Signal(object)
    crop_confirmed = Signal(object)
    pixel_picked = Signal(int, int)
    size_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Visible selection in widget coordinates; None draws nothing.
        self._selection: QRect | None = None
        self._origin: QPoint | None = None
        self._crop: CropRect | None = None
        self._pick_mode: bool = False

        # Original video dimensions — must be set after loading a video
        self._video_w: int = 1
        self._video_h: int = 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_video_dimensions(self, width: int, height: int) -> None:
        self._video_w = width
        self._video_h = height

    def setPixmap(self, pixmap: QPixmap | QImage) -> None:
        super().setPixmap(pixmap)
        if self._crop is not None:
            self.set_crop(self._crop)

    def set_crop(self, crop: CropRect | None) -> None:
        """Set crop coordinates and align the visible selection with them."""
        self._crop = crop
        if crop is None or not crop.is_valid():
            self._set_selection(None)
            return

        display = self._display_rect()
        x1, y1, x2, y2 = crop.to_tuple()
        px1 = display.left() + round(x1 * display.width() / max(self._video_w, 1))
        py1 = display.top() + round(y1 * display.height() / max(self._video_h, 1))
        px2 = display.left() + round(x2 * display.width() / max(self._video_w, 1))
        py2 = display.top() + round(y2 * display.height() / max(self._video_h, 1))
        self._set_selection(QRect(px1, py1, max(1, px2 - px1), max(1, py2 - py1)))

    def set_pick_mode(self, active: bool) -> None:
        """When active, next left-click emits pixel_picked instead of starting a crop."""
        self._pick_mode = active
        if active:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def clear_crop(self) -> None:
        self._crop = None
        self._set_selection(None)

    @property
    def crop(self) -> CropRect | None:
        return self._crop

    # ------------------------------------------------------------------
    # Selection painting
    # ------------------------------------------------------------------

    def _set_selection(self, rect: QRect | None) -> None:
        if rect is not None:
            rect = rect.intersected(self.rect())
            if rect.isEmpty():
                rect = None
        self._selection = rect
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        sel = self._selection
        if sel is None:
            return
        image = self._display_rect()
        sel = sel.intersected(image)
        if sel.isEmpty():
            return
        painter = QPainter(self)
        try:
            # Nothing is painted outside the displayed image (letterbox bars stay as they are).
            painter.setClipRect(image)
            left, top = image.left(), image.top()
            right, bottom = left + image.width(), top + image.height()  # exclusive
            # Dim the four image bands around the selection; the inside stays undimmed.
            painter.fillRect(left, top, image.width(), sel.top() - top, _DIM_COLOUR)
            sel_end_y = sel.top() + sel.height()
            painter.fillRect(left, sel_end_y, image.width(), bottom - sel_end_y, _DIM_COLOUR)
            painter.fillRect(left, sel.top(), sel.left() - left, sel.height(), _DIM_COLOUR)
            sel_end_x = sel.left() + sel.width()
            painter.fillRect(sel_end_x, sel.top(), right - sel_end_x, sel.height(), _DIM_COLOUR)
            # A light and a dark 1 px ring; each side's line positions come from
            # _border_offsets (see there). Dark first, so light wins where they meet.
            w, h = sel.width(), sel.height()
            room_left, room_top = sel.left() - left, sel.top() - top
            room_right, room_bottom = right - 1 - sel.right(), bottom - 1 - sel.bottom()
            sides = (
                _border_offsets(w, room_left),
                _border_offsets(h, room_top),
                _border_offsets(w, room_right),
                _border_offsets(h, room_bottom),
            )
            for ring, colour in ((1, _DARK_LINE), (0, _LIGHT_LINE)):
                k_left, k_top, k_right, k_bottom = (side[ring] for side in sides)
                x1, y1 = sel.left() - k_left, sel.top() - k_top
                x2, y2 = sel.right() + k_right, sel.bottom() + k_bottom
                _fill_line(painter, x1, y1, x2, y1, colour)
                _fill_line(painter, x1, y2, x2, y2, colour)
                _fill_line(painter, x1, y1, x1, y2, colour)
                _fill_line(painter, x2, y1, x2, y2, colour)
        finally:
            painter.end()

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------

    def _display_rect(self) -> QRect:
        """The sub-rect of this widget where QLabel draws the pixmap.

        Derived the same way QLabel/QStyle.drawItemPixmap place it: the pixmap's
        logical (device-independent) size aligned in the margin-adjusted contents
        rect with ``QStyle.alignedRect``. Centring there is ``w / 2 - pw / 2`` in
        integer arithmetic, which differs from ``(w - pw) // 2`` by 1 px when the
        widget size is even and the pixmap size is odd.
        """
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return self.rect()
        scale = pixmap.devicePixelRatio() or 1.0
        size = QSize(int(pixmap.width() / scale + 0.5), int(pixmap.height() / scale + 0.5))
        margin = self.margin()
        contents = self.contentsRect().adjusted(margin, margin, -margin, -margin)
        return QStyle.alignedRect(self.layoutDirection(), self.alignment(), size, contents)

    def _widget_to_video(self, point: QPoint) -> QPoint:
        """Map a widget pixel to original video coordinates, clamped to the frame edges.

        The result is in ``[0, video_w] x [0, video_h]``: a crop edge, not a pixel index.
        """
        dr = self._display_rect()
        return QPoint(
            _axis_to_video(point.x(), dr.left(), dr.width(), self._video_w),
            _axis_to_video(point.y(), dr.top(), dr.height(), self._video_h),
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The centred pixmap moves on resize; keep a committed selection aligned with it.
        if self._origin is None and self._crop is not None:
            self.set_crop(self._crop)
        self.size_changed.emit()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._pick_mode:
                pt = self._widget_to_video(event.position().toPoint())
                self._pick_mode = False
                self.setCursor(Qt.CursorShape.CrossCursor)
                # A picked pixel is an index, so keep it inside the frame.
                self.pixel_picked.emit(
                    min(pt.x(), max(self._video_w - 1, 0)),
                    min(pt.y(), max(self._video_h - 1, 0)),
                )
                return
            origin = event.position().toPoint()
            self._origin = origin
            # Clip to the image: a press in the letterbox margin paints nothing yet.
            self._set_selection(
                QRect(origin.x(), origin.y(), 1, 1).intersected(self._display_rect())
            )

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            current = event.position().toPoint()
            self._set_selection(
                QRect(self._origin, current).normalized().intersected(self._display_rect())
            )
            v_origin = self._widget_to_video(self._origin)
            v_current = self._widget_to_video(current)
            self._crop = CropRect(v_origin.x(), v_origin.y(), v_current.x(), v_current.y())
            self.crop_changed.emit(self._crop)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            current = event.position().toPoint()
            v_origin = self._widget_to_video(self._origin)
            v_current = self._widget_to_video(current)
            self._crop = CropRect(v_origin.x(), v_origin.y(), v_current.x(), v_current.y())
            self._origin = None
            if self._crop.is_valid():
                self.crop_confirmed.emit(self._crop)
            else:
                self.clear_crop()
