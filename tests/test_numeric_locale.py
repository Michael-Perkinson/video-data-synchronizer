"""Numeric line edits accept only "." decimals, whatever the system locale is.

The validators capture the default locale when they are created, so each tab
is constructed after a comma-decimal default locale is installed.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from qt_teardown import OwnedWidgets

from video_data_sync.gui.data_tab import DataTab
from video_data_sync.gui.video_tab import VideoTab


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def german_locale():
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.German))
    try:
        yield
    finally:
        QLocale.setDefault(previous)


def _reset_video_tab(widget) -> None:
    if isinstance(widget, VideoTab):
        widget.reset_state()


@pytest.fixture
def owned_widgets(app, german_locale):
    owned = OwnedWidgets(before_dispose=_reset_video_tab)
    yield owned
    owned.dispose_all()


def test_comma_decimal_locale_is_active(owned_widgets):
    assert QLocale().decimalPoint() == ","


def test_sync_offset_rejects_comma_and_parses_dot(owned_widgets):
    tab = owned_widgets(DataTab())

    tab.offset_input.setText("1,5")
    assert not tab.offset_input.hasAcceptableInput()

    tab.offset_input.setText("1.5")
    assert tab.offset_input.hasAcceptableInput()
    assert tab.offset_s == 1.5

    tab.offset_input.setText("-2.25")
    assert tab.offset_input.hasAcceptableInput()
    assert tab.offset_s == -2.25


def test_y_limit_inputs_reject_comma_and_accept_dot(owned_widgets):
    tab = owned_widgets(DataTab())
    tab.radio_fixed.setChecked(True)

    tab.y_min_input.setText("0,5")
    assert not tab.y_min_input.hasAcceptableInput()

    tab.y_min_input.setText("0.5")
    tab.y_max_input.setText("2.5")
    assert tab.y_min_input.hasAcceptableInput()
    assert tab.y_max_input.hasAcceptableInput()
    assert tab.y_limits() == (0.5, 2.5)


def test_baseline_inputs_reject_comma_and_accept_dot(owned_widgets):
    tab = owned_widgets(DataTab())

    tab.z_start_input.setText("1,5")
    assert not tab.z_start_input.hasAcceptableInput()
    tab.z_start_input.setText("1.5")
    assert tab.z_start_input.hasAcceptableInput()


def test_video_start_and_end_inputs_reject_comma_and_accept_dot(owned_widgets):
    tab = owned_widgets(VideoTab())

    for line_edit in (tab.start_input, tab.end_input):
        line_edit.setText("1,5")
        assert not line_edit.hasAcceptableInput()
        line_edit.setText("1.5")
        assert line_edit.hasAcceptableInput()
        assert float(line_edit.text()) == 1.5
