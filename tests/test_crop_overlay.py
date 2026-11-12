"""Crop overlay: full-frame edge reach and the dimmed-outside selection rendering."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qt_teardown import OwnedWidgets

from video_data_sync.video_processing.cropper import CropOverlay, CropRect

LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def owned_widgets(app):
    owned = OwnedWidgets()
    yield owned
    owned.dispose_all()


def _scaled_overlay(owned_widgets) -> CropOverlay:
    """A 1920 x 1080 video shown 800 x 450 px, filling the overlay exactly."""
    overlay = owned_widgets(CropOverlay())
    overlay.resize(800, 450)
    overlay.set_video_dimensions(1920, 1080)
    pixmap = QPixmap(800, 450)
    pixmap.fill(QColor(200, 200, 200))
    overlay.setPixmap(pixmap)
    overlay.show()
    return overlay


def _drag(overlay: CropOverlay, start: QPoint, end: QPoint) -> CropRect:
    confirmed: list[CropRect] = []
    overlay.crop_confirmed.connect(confirmed.append)
    QTest.mousePress(overlay, LEFT, pos=start)
    QTest.mouseMove(overlay, end)
    QTest.mouseRelease(overlay, LEFT, pos=end)
    assert len(confirmed) == 1
    return confirmed[0]


@pytest.mark.parametrize("end", [QPoint(799, 449), QPoint(850, 500)])
def test_drag_to_or_beyond_bottom_right_edge_reaches_full_frame(owned_widgets, end):
    overlay = _scaled_overlay(owned_widgets)
    crop = _drag(overlay, QPoint(100, 100), end)
    assert crop.to_tuple() == (240, 240, 1920, 1080)


# QTest treats a null QPoint(0, 0) as "widget centre", so the drags end at (0, 1) and (1, 0).
@pytest.mark.parametrize(
    ("end", "expected"),
    [
        (QPoint(0, 1), (0, 2, 960, 540)),
        (QPoint(1, 0), (2, 0, 960, 540)),
        (QPoint(-5, -5), (0, 0, 960, 540)),
    ],
)
def test_drag_to_top_left_edge_reaches_zero(owned_widgets, end, expected):
    overlay = _scaled_overlay(owned_widgets)
    crop = _drag(overlay, QPoint(400, 225), end)
    assert crop.to_tuple() == expected


def test_first_display_pixel_maps_to_zero_on_each_axis(owned_widgets):
    overlay = _scaled_overlay(owned_widgets)
    assert overlay._widget_to_video(QPoint(0, 0)) == QPoint(0, 0)
    assert overlay._widget_to_video(QPoint(0, 1)).x() == 0
    assert overlay._widget_to_video(QPoint(1, 0)).y() == 0


def test_drag_from_far_edge_to_beyond_top_left_selects_exactly_the_frame(owned_widgets):
    overlay = _scaled_overlay(owned_widgets)
    crop = _drag(overlay, QPoint(799, 449), QPoint(-30, -30))
    assert crop.to_tuple() == (0, 0, 1920, 1080)


def test_edge_mapping_stays_within_the_frame(owned_widgets):
    overlay = _scaled_overlay(owned_widgets)
    assert overlay._widget_to_video(QPoint(-500, -500)) == QPoint(0, 0)
    assert overlay._widget_to_video(QPoint(5000, 5000)) == QPoint(1920, 1080)


def test_interior_mapping_is_unchanged(owned_widgets):
    overlay = _scaled_overlay(owned_widgets)
    assert overlay._widget_to_video(QPoint(400, 225)) == QPoint(960, 540)
    assert overlay._widget_to_video(QPoint(798, 448)) == QPoint(int(798 / 800 * 1920), 1075)


def test_picked_pixel_stays_inside_the_frame(owned_widgets):
    overlay = _scaled_overlay(owned_widgets)
    picked: list[tuple[int, int]] = []
    overlay.pixel_picked.connect(lambda x, y: picked.append((x, y)))
    overlay.set_pick_mode(True)
    QTest.mousePress(overlay, LEFT, pos=QPoint(799, 449))
    QTest.mouseRelease(overlay, LEFT, pos=QPoint(799, 449))
    assert picked == [(1919, 1079)]


def test_press_in_letterbox_margin_does_not_dim_and_drag_still_clamps(owned_widgets):
    overlay = owned_widgets(CropOverlay())
    overlay.resize(800, 600)  # 800 x 450 image centred: 75 px margins above and below
    overlay.set_video_dimensions(1920, 1080)
    pixmap = QPixmap(800, 450)
    pixmap.fill(QColor(200, 200, 200))
    overlay.setPixmap(pixmap)
    overlay.show()
    probe = QPoint(10, 300)
    plain = overlay.grab().toImage()
    confirmed: list[CropRect] = []
    overlay.crop_confirmed.connect(confirmed.append)

    QTest.mousePress(overlay, LEFT, pos=QPoint(400, 20))
    pressed = overlay.grab().toImage()
    assert overlay._selection is None
    assert pressed.pixelColor(probe) == plain.pixelColor(probe)

    QTest.mouseMove(overlay, QPoint(600, 300))
    selection = overlay._selection
    assert selection is not None
    assert selection.top() == 75
    QTest.mouseRelease(overlay, LEFT, pos=QPoint(600, 300))
    assert [crop.to_tuple() for crop in confirmed] == [(960, 0, 1440, 540)]


def _brightness(colour: QColor) -> int:
    return colour.red() + colour.green() + colour.blue()


def test_selection_dims_outside_and_leaves_inside_unchanged(owned_widgets):
    overlay = owned_widgets(CropOverlay())
    overlay.resize(400, 300)
    overlay.set_video_dimensions(400, 300)
    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor(200, 200, 200))
    overlay.setPixmap(pixmap)
    overlay.show()
    outside, inside = QPoint(20, 20), QPoint(200, 150)

    plain = overlay.grab().toImage()
    overlay.set_crop(CropRect(100, 100, 300, 200))
    selected = overlay.grab().toImage()
    overlay.clear_crop()
    cleared = overlay.grab().toImage()

    assert _brightness(selected.pixelColor(outside)) < _brightness(plain.pixelColor(outside))
    assert selected.pixelColor(inside) == plain.pixelColor(inside)
    # The selection edge carries the white border line.
    assert selected.pixelColor(QPoint(100, 150)) == QColor(255, 255, 255)
    # No crop: nothing is dimmed.
    assert cleared.pixelColor(outside) == plain.pixelColor(outside)


def _filled_overlay(owned_widgets, size: tuple[int, int], image: tuple[int, int], colour: QColor):
    overlay = owned_widgets(CropOverlay())
    overlay.resize(*size)
    overlay.set_video_dimensions(*image)
    pixmap = QPixmap(*image)
    pixmap.fill(colour)
    overlay.setPixmap(pixmap)
    overlay.show()
    return overlay


def _side_points(inset: int) -> list[QPoint]:
    """One point on each side of a 400 x 300 rect, ``inset`` px in from the edge."""
    return [
        QPoint(200, inset),
        QPoint(200, 299 - inset),
        QPoint(inset, 150),
        QPoint(399 - inset, 150),
    ]


def test_full_frame_selection_on_white_shows_dark_line_on_every_side(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), QColor(255, 255, 255))
    overlay.set_crop(CropRect(0, 0, 400, 300))
    image = overlay.grab().toImage()
    for point in _side_points(1):
        assert _brightness(image.pixelColor(point)) < 100, point


def test_full_frame_selection_on_black_shows_light_line_on_every_side(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), QColor(0, 0, 0))
    overlay.set_crop(CropRect(0, 0, 400, 300))
    image = overlay.grab().toImage()
    for point in _side_points(0):
        assert _brightness(image.pixelColor(point)) > 600, point


@pytest.mark.parametrize(
    "crop",
    [CropRect(0, 0, 400, 225), CropRect(100, 0, 200, 50), CropRect(100, 175, 200, 225)],
)
def test_letterbox_bars_are_never_dimmed_or_bordered(owned_widgets, crop):
    # QLabel centres a 400 x 225 image in 400 x 300 at y = 150 - 112 = 38:
    # image rows 38..262, bars at y 0..37 and 263..299.
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 225), QColor(200, 200, 200))
    assert overlay._display_rect() == QRect(0, 38, 400, 225)
    plain = overlay.grab().toImage()
    overlay.set_crop(crop)
    selected = overlay.grab().toImage()
    bar_rows = [*range(0, 38), *range(263, 300)]
    for y in bar_rows:
        for x in range(0, 400, 3):
            assert selected.pixelColor(x, y) == plain.pixelColor(x, y), (x, y)
    # Bar rows touching the image are checked at every pixel.
    for y in (37, 263):
        for x in range(400):
            assert selected.pixelColor(x, y) == plain.pixelColor(x, y), (x, y)
    # The image rows next to the bars do change (dimmed or bordered) somewhere.
    for y in (38, 262):
        assert any(selected.pixelColor(x, y) != plain.pixelColor(x, y) for x in range(400)), y


@pytest.mark.parametrize(
    "crop",
    [CropRect(0, 0, 225, 300), CropRect(0, 100, 50, 200), CropRect(175, 100, 225, 200)],
)
def test_pillarbox_bars_are_never_dimmed_or_bordered(owned_widgets, crop):
    # A 225 x 300 image in 400 x 300 starts at x = 200 - 112 = 88: image columns 88..312.
    overlay = _filled_overlay(owned_widgets, (400, 300), (225, 300), QColor(200, 200, 200))
    assert overlay._display_rect() == QRect(88, 0, 225, 300)
    plain = overlay.grab().toImage()
    overlay.set_crop(crop)
    selected = overlay.grab().toImage()
    for x in [*range(0, 88), *range(313, 400)]:
        for y in range(0, 300, 3):
            assert selected.pixelColor(x, y) == plain.pixelColor(x, y), (x, y)
    for x in (87, 313):
        for y in range(300):
            assert selected.pixelColor(x, y) == plain.pixelColor(x, y), (x, y)
    for x in (88, 312):
        assert any(selected.pixelColor(x, y) != plain.pixelColor(x, y) for y in range(300)), x


_TINY_CENTRES = {
    # (size, at corner) -> unpainted pixels relative to the selection's top-left
    (1, False): [(0, 0)],
    (2, False): [(0, 0), (0, 1), (1, 0), (1, 1)],
    (4, False): [(1, 1), (1, 2), (2, 1), (2, 2)],
    (4, True): [(2, 2)],
}


@pytest.mark.parametrize("fill", [QColor(0, 0, 0), QColor(255, 255, 255)])
@pytest.mark.parametrize("corner", [False, True])
@pytest.mark.parametrize("size", [1, 2, 4])
def test_tiny_selections_show_light_and_dark_lines_and_keep_their_centre(
    owned_widgets, fill, corner, size
):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), fill)
    plain = overlay.grab().toImage()
    x0, y0 = (0, 0) if corner else (200, 150)
    overlay.set_crop(CropRect(x0, y0, x0 + size, y0 + size))
    selected = overlay.grab().toImage()

    near = [
        selected.pixelColor(x, y)
        for x in range(max(0, x0 - 3), x0 + size + 3)
        for y in range(max(0, y0 - 3), y0 + size + 3)
    ]
    assert any(_brightness(colour) > 600 for colour in near)
    assert any(_brightness(colour) < 100 for colour in near)
    for dx, dy in _TINY_CENTRES.get((size, corner), []):
        point = QPoint(x0 + dx, y0 + dy)
        assert selected.pixelColor(point) == plain.pixelColor(point), point


def test_tiny_selection_border_is_outside_when_there_is_room(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), QColor(128, 128, 128))
    overlay.set_crop(CropRect(200, 150, 201, 151))
    selected = overlay.grab().toImage()
    assert _brightness(selected.pixelColor(201, 150)) == 765  # light ring 1 px outside
    assert _brightness(selected.pixelColor(202, 150)) == 0  # dark ring 2 px outside


def test_small_selection_at_image_edge_keeps_dark_line_inside(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), QColor(255, 255, 255))
    overlay.set_crop(CropRect(0, 100, 5, 105))  # 5 x 5, touching the left image edge
    selected = overlay.grab().toImage()
    assert _brightness(selected.pixelColor(0, 102)) == 765  # light edge line
    assert _brightness(selected.pixelColor(1, 102)) == 0  # dark line moved inside
    assert _brightness(selected.pixelColor(5, 102)) == 0  # right side: dark line outside


def _red_bounds(image) -> QRect:
    """Bounds of the pure-red pixels along the image's middle row and column."""
    red = QColor(255, 0, 0)
    mid_x, mid_y = image.width() // 2, image.height() // 2
    xs = [x for x in range(image.width()) if image.pixelColor(x, mid_y) == red]
    ys = [y for y in range(image.height()) if image.pixelColor(mid_x, y) == red]
    return QRect(QPoint(min(xs), min(ys)), QPoint(max(xs), max(ys)))


@pytest.mark.parametrize("widget_size", [(400, 300), (401, 301), (400, 301), (401, 300)])
@pytest.mark.parametrize("pixmap_size", [(224, 150), (225, 151), (224, 151), (225, 150)])
def test_display_rect_matches_where_qlabel_draws_the_pixmap(
    owned_widgets, widget_size, pixmap_size
):
    overlay = _filled_overlay(owned_widgets, widget_size, pixmap_size, QColor(255, 0, 0))
    assert overlay._display_rect() == _red_bounds(overlay.grab().toImage())


def test_odd_parity_mapping_starts_at_first_image_row(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 225), QColor(200, 200, 200))
    assert overlay._widget_to_video(QPoint(200, 38)).y() == 0
    assert overlay._widget_to_video(QPoint(200, 39)).y() == 1
    assert overlay._widget_to_video(QPoint(200, 262)).y() == 225
    # A press in the bar row just above the image does not paint a selection.
    QTest.mousePress(overlay, LEFT, pos=QPoint(200, 37))
    assert overlay._selection is None
    QTest.mouseRelease(overlay, LEFT, pos=QPoint(200, 37))
    assert overlay.crop is None


def test_tiny_selection_keeps_its_centre_visible(owned_widgets):
    overlay = _filled_overlay(owned_widgets, (400, 300), (400, 300), QColor(200, 200, 200))
    plain = overlay.grab().toImage()
    overlay.set_crop(CropRect(100, 100, 104, 104))
    selected = overlay.grab().toImage()
    for point in (QPoint(101, 101), QPoint(102, 102)):
        assert selected.pixelColor(point) == plain.pixelColor(point)
    # The 1 px line pair still marks it: light edge, dark line just outside.
    assert _brightness(selected.pixelColor(QPoint(100, 101))) > 600
    assert _brightness(selected.pixelColor(QPoint(99, 101))) < 100
