"""A corrupt saved line-width preference must not stop the Data tab.

Every test uses its own INI file under ``tmp_path`` and disposes only the
DataTab that it constructed. Only ``DataTab`` is imported at module level so
that, on source without the fix, construction/reset tests fail with the
original ``ValueError`` instead of the whole module failing to import.
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from qt_teardown import OwnedWidgets

from video_data_sync.gui.data_tab import DataTab

KEY = "data/lineWidth"
DEFAULT = 1.5
# Values safe to persist through QSettings INI and read back unchanged in type.
MALFORMED_STORED = ["bad", "", "   ", "nan", "inf", "-inf", "1e999", float("nan"), float("inf")]
# Any QVariant type the helper may see; tested directly, not through INI.
MALFORMED_VALUES = [*MALFORMED_STORED, -float("inf"), True, False, None, [1], {"x": 1}, b"2"]
# Raw INI lines whose on-disk form is known: text, empty, list and invalid variant.
MALFORMED_RAW_INI = ["bad", "", "1, 2", "@Invalid()"]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.ini"
    monkeypatch.setattr(
        "video_data_sync.gui.data_tab.QSettings",
        lambda: QSettings(str(path), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr("video_data_sync.gui.data_tab.read_folder_path", lambda: str(tmp_path))
    monkeypatch.setattr("video_data_sync.gui.data_tab.write_folder_path", lambda path: None)
    store = QSettings(str(path), QSettings.Format.IniFormat)
    store.setValue("unrelated/keep", "unchanged")
    store.sync()
    return path


@pytest.fixture
def owned_widgets(app):
    owned = OwnedWidgets()
    yield owned
    owned.dispose_all()


def _store(path, value) -> None:
    store = QSettings(str(path), QSettings.Format.IniFormat)
    store.setValue(KEY, value)
    store.sync()


def _preference_bytes(path) -> bytes:
    """Compare the complete persisted file, including unrelated preferences."""
    QSettings(str(path), QSettings.Format.IniFormat).sync()
    return path.read_bytes()


@pytest.mark.parametrize("value", MALFORMED_VALUES)
def test_helper_returns_default_for_malformed_values(value):
    from video_data_sync.gui.data_tab import DEFAULT_LINE_WIDTH, _saved_line_width

    assert DEFAULT_LINE_WIDTH == DEFAULT
    assert _saved_line_width(value) == DEFAULT


# float() of this int raises OverflowError; kept out of parametrize ids on purpose.
def test_helper_returns_default_for_overflowing_int():
    from video_data_sync.gui.data_tab import _saved_line_width

    assert _saved_line_width(10**10000) == DEFAULT


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.5, 2.5), ("2.5", 2.5), (" 3.0 ", 3.0), (4, 4.0), ("0.2", 0.2), (10.0, 10.0)],
)
def test_helper_keeps_finite_numbers(value, expected):
    from video_data_sync.gui.data_tab import _saved_line_width

    result = _saved_line_width(value)
    assert math.isfinite(result)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize("raw", MALFORMED_RAW_INI)
def test_raw_ini_malformed_value_does_not_crash_construction(settings, owned_widgets, raw):
    settings.write_text(
        f"[data]\nlineWidth={raw}\n\n[unrelated]\nkeep=unchanged\n", encoding="utf-8"
    )
    before = _preference_bytes(settings)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(DEFAULT)
    assert _preference_bytes(settings) == before


def test_missing_preference_uses_default(settings, owned_widgets):
    before = _preference_bytes(settings)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(DEFAULT)
    assert _preference_bytes(settings) == before


@pytest.mark.parametrize("value", MALFORMED_STORED)
def test_malformed_preference_falls_back_on_construction(settings, owned_widgets, value):
    _store(settings, value)
    before = _preference_bytes(settings)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(DEFAULT)
    assert _preference_bytes(settings) == before


@pytest.mark.parametrize(("value", "expected"), [(2.5, 2.5), ("3.2", 3.2), ("4", 4.0)])
def test_valid_preference_is_kept_on_construction(settings, owned_widgets, value, expected):
    _store(settings, value)
    before = _preference_bytes(settings)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(expected)
    assert _preference_bytes(settings) == before


@pytest.mark.parametrize(("value", "expected"), [(10.0, 5.0), (0.0, 0.2), ("-3", 0.2)])
def test_out_of_range_preference_keeps_existing_spinbox_bounds(
    settings, owned_widgets, value, expected
):
    _store(settings, value)
    before = _preference_bytes(settings)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(expected)
    assert _preference_bytes(settings) == before


@pytest.mark.parametrize("suspended", [False, True])
@pytest.mark.parametrize("value", MALFORMED_STORED)
def test_malformed_preference_falls_back_on_reset(settings, owned_widgets, value, suspended):
    _store(settings, 3.0)
    tab = owned_widgets(DataTab())
    assert tab.lw_spinner.value() == pytest.approx(3.0)
    # Write through the tab's own QSettings so no cross-instance cache matters.
    tab._settings.setValue(KEY, value)
    before = _preference_bytes(settings)
    tab._suspend_preferences = suspended
    tab.reset_to_preferences()
    assert tab.lw_spinner.value() == pytest.approx(DEFAULT)
    assert _preference_bytes(settings) == before
    assert tab._suspend_preferences is suspended


def test_valid_preference_is_applied_on_reset(settings, owned_widgets):
    tab = owned_widgets(DataTab())
    tab._settings.setValue(KEY, "4.5")
    before = _preference_bytes(settings)
    tab.reset_to_preferences()
    assert tab.lw_spinner.value() == pytest.approx(4.5)
    assert _preference_bytes(settings) == before


@pytest.mark.parametrize("suspended", [False, True])
@pytest.mark.parametrize(("value", "expected"), [(10.0, 5.0), (0.0, 0.2), ("-3", 0.2)])
def test_out_of_range_preference_is_not_rewritten_on_reset(
    settings, owned_widgets, value, expected, suspended
):
    _store(settings, 3.0)
    tab = owned_widgets(DataTab())
    tab._settings.setValue(KEY, value)
    before = _preference_bytes(settings)
    tab._suspend_preferences = suspended
    tab.reset_to_preferences()
    assert tab.lw_spinner.value() == pytest.approx(expected)
    assert _preference_bytes(settings) == before
    assert tab._suspend_preferences is suspended


def test_user_line_width_edits_still_persist_after_reset(settings, owned_widgets):
    _store(settings, "bad")
    tab = owned_widgets(DataTab())
    tab.reset_to_preferences()
    tab.lw_spinner.setValue(2.7)
    tab._settings.sync()
    store = QSettings(str(settings), QSettings.Format.IniFormat)
    assert float(store.value(KEY)) == pytest.approx(2.7)
    assert store.value("unrelated/keep") == "unchanged"
