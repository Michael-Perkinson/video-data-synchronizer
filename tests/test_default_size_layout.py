"""At the default window size the settings columns show every control without scrolling,
and they fill the column height instead of sitting squeezed at the top."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QWidget
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow

DEFAULT_SIZE = (1300, 800)
TALL_SIZE = (1300, 1400)
MIN_VIEW_FRACTION = 0.60
# The last control must end in the lower part of the column at the default size.
MIN_FILL_FRACTION = 0.80
# In a very tall window the spread is capped: no larger empty band between two
# consecutive controls (this includes a group box frame and title).
MAX_TALL_GAP_PX = 80


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(MainWindow, "_start_preload_tabs", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_recovery_if_available", lambda self: None)
    win = MainWindow()
    try:
        win._ensure_data_tab()
        win._ensure_animated_tab()
        win._preload_done = True
        for index in range(win.tabs.count()):
            win.tabs.setTabEnabled(index, True)
        win.show()
        app.processEvents()
        yield win
    finally:
        win._set_dirty(False)
        dispose_widget(win)


def _settings_tabs(window: MainWindow):
    assert window.data_tab is not None
    return [(0, window.video_tab), (1, window.data_tab)]


def _widest_widgets(column: QWidget, count: int = 3) -> str:
    """Names of the shown widgets with the largest minimum width, for the report."""
    shown = [child for child in column.findChildren(QWidget) if child.isVisibleTo(column)]

    def minimum_width(widget: QWidget) -> int:
        return max(widget.minimumWidth(), widget.minimumSizeHint().width())

    widest = sorted(shown, key=minimum_width, reverse=True)[:count]
    return ", ".join(
        f"{type(w).__name__}({getattr(w, 'text', lambda: '')()!r})={minimum_width(w)}"
        for w in widest
    )


def _leaf_controls(column: QWidget) -> list[QWidget]:
    """Shown widgets that hold no other shown widget (rows' actual controls and labels)."""
    shown = [
        child
        for child in column.findChildren(QWidget)
        if child.isVisibleTo(column) and child.width() > 0 and child.height() > 0
    ]
    leaves = []
    for widget in shown:
        if isinstance(widget, QGroupBox):
            continue
        children = [child for child in widget.findChildren(QWidget) if child is not widget]
        if any(child.isVisibleTo(column) for child in children):
            continue  # a container (e.g. a spin box around its line edit)
        if isinstance(widget, QLabel) and not widget.text() and widget.pixmap().isNull():
            continue  # empty status labels and colour swatches
        leaves.append(widget)
    return leaves


def _vertical_spans(column: QWidget, viewport: QWidget) -> list[tuple[int, int, str]]:
    spans = []
    for widget in _leaf_controls(column):
        top = widget.mapTo(viewport, QPoint(0, 0)).y()
        name = f"{type(widget).__name__}({getattr(widget, 'text', lambda: '')()!r})"
        spans.append((top, top + widget.height(), name))
    return sorted(spans)


def _largest_gap(spans: list[tuple[int, int, str]]) -> tuple[int, str]:
    """Largest empty vertical band between consecutive controls (side-by-side ones overlap)."""
    largest, where = 0, ""
    bottom, below = spans[0][1], spans[0][2]
    for top, end, name in spans[1:]:
        if top - bottom > largest:
            largest, where = top - bottom, f"{below} -> {name}"
        if end > bottom:
            bottom, below = end, name
    return largest, where


def _measure_column(app, window: MainWindow, index: int, tab) -> tuple[list[str], dict]:
    """Measure one settings column; return its problems instead of asserting at once."""
    window.tabs.setCurrentIndex(index)
    app.processEvents()
    scroll = tab.controls_scroll
    column = scroll.widget()
    viewport = scroll.viewport()
    layout = column.layout()
    assert layout is not None
    minimum = layout.totalMinimumSize()
    spans = _vertical_spans(column, viewport)
    last_bottom = max(end for _top, end, _name in spans)
    gap, where = _largest_gap(spans)
    report = {
        "tab": index,
        "viewport": viewport.height(),
        "minimum_height": minimum.height(),
        "minimum_fraction": minimum.height() / viewport.height(),
        "last_bottom": last_bottom,
        "fill_fraction": last_bottom / viewport.height(),
        "largest_gap": gap,
        "largest_gap_at": where,
        "column": column.width(),
        "minimum_width": minimum.width(),
    }
    print(
        f"tab {index}: viewport {viewport.height()} px; minimum content {minimum.height()} px"
        f" ({report['minimum_fraction']:.0%}); last control ends at {last_bottom} px"
        f" ({report['fill_fraction']:.0%}); largest gap {gap} px ({where});"
        f" column {column.width()} px; minimum row width {minimum.width()} px;"
        f" widest: {_widest_widgets(column)}"
    )
    problems = []
    if scroll.verticalScrollBar().maximum() != 0:
        problems.append(f"tab {index} needs vertical scrolling")
    if scroll.horizontalScrollBar().maximum() != 0:
        problems.append(f"tab {index} needs horizontal scrolling")
    # No row needs more than the fixed column width (otherwise controls get squeezed).
    if minimum.width() > column.width():
        problems.append(f"tab {index}: minimum row width {minimum.width()} > {column.width()}")
    # Every shown control lies completely inside the visible viewport.
    for child in column.findChildren(QWidget):
        if not child.isVisibleTo(column) or child.width() <= 0 or child.height() <= 0:
            continue
        top_left = child.mapTo(viewport, QPoint(0, 0))
        bottom_right = child.mapTo(viewport, QPoint(child.width() - 1, child.height() - 1))
        if not (viewport.rect().contains(top_left) and viewport.rect().contains(bottom_right)):
            problems.append(f"tab {index}: {type(child).__name__} outside the viewport")
    return problems, report


def test_default_size_is_unchanged(window):
    assert (window.width(), window.height()) == DEFAULT_SIZE


def test_settings_columns_fit_and_fill_at_default_size(app, window):
    problems: list[str] = []
    reports = []
    for index, tab in _settings_tabs(window):
        found, report = _measure_column(app, window, index, tab)
        problems += found
        reports.append(report)
    for report in reports:
        if report["fill_fraction"] < MIN_FILL_FRACTION:
            problems.append(
                f"tab {report['tab']}: column only filled to {report['fill_fraction']:.0%}"
            )
    assert problems == []


def test_video_column_still_fits_with_colour_note_shown(app, window):
    window.video_tab._brightness_slider.setValue(20)
    app.processEvents()
    assert not window.video_tab.lbl_colour_note.isHidden()
    problems, _report = _measure_column(app, window, 0, window.video_tab)
    window.video_tab._reset_colour()
    assert problems == []


def test_spread_is_capped_in_a_tall_window(app, window):
    window.resize(*TALL_SIZE)
    app.processEvents()
    problems: list[str] = []
    for index, tab in _settings_tabs(window):
        found, report = _measure_column(app, window, index, tab)
        problems += found
        if report["largest_gap"] > MAX_TALL_GAP_PX:
            problems.append(
                f"tab {index}: gap {report['largest_gap']} px at {report['largest_gap_at']}"
            )
        # The leftover height goes to the bottom, so the controls do not reach it.
        if report["fill_fraction"] > 0.9:
            problems.append(f"tab {index}: controls spread to {report['fill_fraction']:.0%}")
    assert problems == []


def test_video_and_plot_areas_keep_most_of_the_width(app, window):
    window.tabs.setCurrentIndex(0)
    app.processEvents()
    video_fraction = window.video_tab.video_display.width() / window.width()
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.data_tab is not None
    plot_fraction = window.data_tab.plot.width() / window.width()
    print(f"video area {video_fraction:.0%}, data plot area {plot_fraction:.0%} of the window")
    assert video_fraction >= MIN_VIEW_FRACTION
    assert plot_fraction >= MIN_VIEW_FRACTION


def test_time_unit_combo_still_fits_milliseconds(app, window):
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    assert window.data_tab is not None
    combo = window.data_tab.time_unit_combo
    assert combo.width() > combo.fontMetrics().horizontalAdvance("Milliseconds")
