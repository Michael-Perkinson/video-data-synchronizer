"""Regressions for settings access and responsive playback/export controls."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from qt_teardown import dispose_widget

from video_data_sync.gui.main_window import MainWindow


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
        win.show()
        app.processEvents()
        yield win
    finally:
        win._set_dirty(False)
        dispose_widget(win)


@pytest.mark.parametrize("size", [(1024, 768), (1280, 720)])
def test_all_tabs_fit_small_client_area(app, window, size):
    for index in range(window.tabs.count()):
        window.tabs.setTabEnabled(index, True)
        window.tabs.setCurrentIndex(index)
        window.resize(*size)
        app.processEvents()
        assert window.width() <= size[0], f"tab {index} forces horizontal overflow"
        assert window.height() <= size[1], f"tab {index} forces vertical overflow"


@pytest.mark.parametrize("tab_index", [0, 1])
def test_settings_focus_reveals_controls_after_scrolling(app, window, tab_index):
    window.tabs.setCurrentIndex(tab_index)
    # Geometry note: the settings columns may fit at 1024 x 600 (their gaps shrink to
    # nothing), so a shorter window (it stops at its minimum height) makes them scroll.
    window.resize(1024, 420)
    app.processEvents()
    tab = (window.video_tab, window.data_tab)[tab_index]
    scroll = tab.controls_scroll
    column = scroll.widget()
    controls = [
        child
        for child in column.findChildren(QWidget)
        if child.isVisible() and child.isEnabled() and child.focusPolicy() != Qt.FocusPolicy.NoFocus
    ]
    assert len(controls) > 5
    assert scroll.verticalScrollBar().maximum() > 0
    # Exercise focus entering from outside the scrolled column as well as
    # movement within it. Checking only a pre-scrolled top control misses this.
    for child in controls:
        window.tabs.tabBar().setFocus()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        child.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        centre = child.mapTo(scroll.viewport(), child.rect().center())
        assert scroll.viewport().rect().contains(centre), type(child).__name__
    assert scroll.horizontalScrollBar().maximum() == 0


def test_playback_bar_wraps_and_keeps_export_controls_visible(app, window):
    window.tabs.setTabEnabled(2, True)
    window.tabs.setCurrentIndex(2)
    tab = window.animated_tab
    bar = tab.controls_bar
    window.resize(1024, 720)
    app.processEvents()
    # Font metrics differ by platform, so 1024 px may already fit on one row.
    # Narrow just enough to force the wrapped arrangement instead of assuming
    # a platform-specific threshold.
    chrome_width = window.width() - bar.width()
    if not bar.wrapped:
        window.resize(bar.one_row_width() + chrome_width - 1, 720)
        app.processEvents()
    assert bar.wrapped
    for control in (
        tab.btn_play,
        tab.btn_pause,
        tab.btn_reset,
        tab.btn_advanced,
        tab.btn_export,
        tab.btn_export_multiple,
        tab.speed_combo,
        tab.chk_deflicker,
    ):
        assert control.isVisible()
        centre = control.mapTo(bar, control.rect().center())
        assert bar.rect().contains(centre), control.objectName()
    window.resize(bar.one_row_width() + chrome_width + 40, 900)
    app.processEvents()
    assert not bar.wrapped
