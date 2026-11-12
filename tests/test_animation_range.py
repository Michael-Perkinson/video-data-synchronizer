import os

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication
from qt_teardown import OwnedWidgets, dispose_widget

from video_data_sync.export_history import load_records
from video_data_sync.gui.animated_tab import AnimatedTab
from video_data_sync.plotting.plot_widget import AnimatedPlotWidget, Trace
from video_data_sync.video_processing.exporter import export_video


@pytest.fixture
def owned_widgets():
    """Dispose only the top-level widgets this test registers."""
    owner = OwnedWidgets()
    yield owner
    owner.dispose_all()


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return QApplication([])


def test_animation_keeps_auto_range_by_default_and_can_limit_change(synthetic_video):
    _app()
    tab = AnimatedTab()
    tab.set_composition_settings({"max_change_enabled": False, "max_change_percent": 70.0})
    try:
        tab.update_video(str(synthetic_video), 10, 0, 0.6, None, None)
        tab.update_data(
            pd.DataFrame({"Time (s)": [0.0, 0.3, 0.6], "signal": [10.0, 11.0, 14.0]}),
            "Time (s)",
            "signal",
            "#000000",
            None,
            None,
        )
        assert tab.max_change is None
        assert tab.anim_plot.ax.get_ylim() != (0.0, 2.5)

        tab.set_composition_settings({"max_change_enabled": True, "max_change_percent": 70.0})

        assert tab.max_change == pytest.approx(2.8)
        assert tab.anim_plot.ax.get_ylim()[0] < 0.0
        assert tab.anim_plot.ax.get_ylim()[1] > 2.8
        time_bar, signal_bar = tab.anim_plot.ax.lines[:2]
        xlim = tab.anim_plot.ax.get_xlim()
        ylim = tab.anim_plot.ax.get_ylim()
        signal_x = np.asarray(signal_bar.get_xdata(), dtype=float)
        time_y = np.asarray(time_bar.get_ydata(), dtype=float)
        assert np.isclose((signal_x[0] - xlim[0]) / (xlim[1] - xlim[0]), 0.04)
        assert np.isclose((time_y[0] - ylim[0]) / (ylim[1] - ylim[0]), 0.04)
    finally:
        tab._release_cap()
        dispose_widget(tab)


def test_fixed_max_change_uses_minimum_and_clips_overflow():
    _app()
    widget = AnimatedPlotWidget()
    times = np.array([0.0, 1.0, 2.0])
    values = np.array([0.0, 1.0, 4.0])
    widget.setup(
        [Trace(times, values)],
        y_min=-10.0,
        y_max=10.0,
        max_change=2.0,
        pin_scale_bars_to_axes=True,
    )
    widget.update_lines([(times, values)])

    displayed = np.asarray(widget._lines[0].get_ydata(), dtype=float)
    time_bar, signal_bar = widget.ax.lines[:2]
    assert widget.ax.get_ylim()[0] < 0.0
    assert widget.ax.get_ylim()[1] > 2.0
    np.testing.assert_array_equal(displayed, [0.0, 1.0, 2.0])
    assert widget._trace_signal_offset == 0.0
    assert np.max(np.asarray(time_bar.get_ydata(), dtype=float)) < values.min()
    assert np.max(np.asarray(signal_bar.get_ydata(), dtype=float)) < values.min()
    assert displayed[-1] < widget.ax.get_ylim()[1]
    assert widget._lines[0].get_clip_on()


def test_fixed_range_keeps_oversized_default_scale_bars_inside_axes():
    _app()
    widget = AnimatedPlotWidget()
    widget.setup(
        [Trace(np.array([0.0, 10.0]), np.array([0.0, 4.0]))],
        max_change=4.0,
        time_scale_s=2.0,
        signal_scale=5.0,
    )

    xlim = widget.ax.get_xlim()
    ylim = widget.ax.get_ylim()
    for bar in widget.ax.lines[:2]:
        x = np.asarray(bar.get_xdata(), dtype=float)
        y = np.asarray(bar.get_ydata(), dtype=float)
        assert np.min(x) >= xlim[0]
        assert np.max(x) <= xlim[1]
        assert np.min(y) >= ylim[0]
        assert np.max(y) <= ylim[1]


def test_fixed_range_does_not_shift_when_time_scale_is_hidden():
    _app()
    widget = AnimatedPlotWidget()
    times = np.array([0.0, 1.0, 2.0])
    values = np.array([0.0, 1.0, 4.0])
    widget.setup(
        [Trace(times, values)],
        max_change=2.0,
        pin_scale_bars_to_axes=True,
        show_time_scale=False,
        show_signal_scale=False,
    )
    widget.update_lines([(times, values)])

    assert widget._trace_signal_offset == 0.0
    np.testing.assert_array_equal(widget._lines[0].get_ydata(), [0.0, 1.0, 2.0])


def test_negative_transformed_minimum_sets_the_fixed_range():
    _app()
    widget = AnimatedPlotWidget()
    times = np.array([0.0, 1.0, 2.0])
    values = np.array([-5.0, -4.0, -1.0])
    widget.setup([Trace(times, values)], max_change=2.0)
    widget.update_lines([(times, values)])

    y_min, y_max = widget.ax.get_ylim()
    assert y_min < -5.0  # Axis includes the automatically pinned scale bars.
    assert y_max > -3.0
    np.testing.assert_array_equal(widget._lines[0].get_ydata(), [-5.0, -4.0, -3.0])


def test_negative_infinity_does_not_make_the_fixed_preview_range_infinite():
    _app()
    widget = AnimatedPlotWidget()
    times = np.array([0.0, 1.0, 2.0])
    values = np.array([-np.inf, -4.0, 6.0])
    widget.setup([Trace(times, values)], max_change=2.0)
    widget.update_lines([(times, values)])

    y_min, y_max = widget.ax.get_ylim()
    assert y_min < -4.0  # Axis includes the automatically pinned scale bars.
    assert y_max > -2.0
    assert np.all(np.isfinite((y_min, y_max)))
    np.testing.assert_array_equal(widget._lines[0].get_ydata(), [-np.inf, -4.0, -2.0])


def test_export_uses_the_same_fixed_change_range(synthetic_video, tmp_path):
    export_video(
        output_path=str(tmp_path / "fixed-range.mp4"),
        video_path=str(synthetic_video),
        fps=10,
        start_s=0,
        end_s=0.6,
        crop=None,
        colour_lut=None,
        speed_factor=1,
        df=pd.DataFrame({"Time (s)": [0.0, 0.3, 0.6], "signal": [10.0, 11.0, 14.0]}),
        time_col="Time (s)",
        data_col="signal",
        offset_s=0,
        color="#000000",
        y_min=None,
        y_max=None,
        max_change=2.0,
        pin_scale_bars_to_axes=True,
    )

    records = load_records(tmp_path / "export-history")
    assert len(records) == 1
    assert records[0]["resolved"]["y_limits"][0] < 0.0
    assert records[0]["resolved"]["y_limits"][1] > 2.0
    assert records[0]["resolved"]["data_y_range"] == [0.0, 2.0]
    assert records[0]["settings"]["max_change"] == 2.0
    assert records[0]["settings"]["pin_scale_bars_to_axes"] is True


def test_export_fixed_range_auto_pins_oversized_scale_bars(synthetic_video, tmp_path):
    output = tmp_path / "fixed-range-oversized-bar.mp4"
    export_video(
        output_path=str(output),
        video_path=str(synthetic_video),
        fps=10,
        start_s=0,
        end_s=0.6,
        crop=None,
        colour_lut=None,
        speed_factor=1,
        df=pd.DataFrame({"Time (s)": [0.0, 0.3, 0.6], "signal": [0.0, 1.0, 4.0]}),
        time_col="Time (s)",
        data_col="signal",
        offset_s=0,
        color="#000000",
        y_min=None,
        y_max=None,
        max_change=4.0,
        time_scale_s=2.0,
        signal_scale=5.0,
    )

    record = load_records(tmp_path / "export-history")[0]
    y_min, y_max = record["resolved"]["y_limits"]
    assert record["resolved"]["data_y_range"] == [0.0, 4.0]
    assert record["resolved"]["pin_scale_bars_to_axes"] is True
    assert y_max - y_min > 4.0


def test_export_fixed_range_supports_negative_minimum(synthetic_video, tmp_path):
    export_video(
        output_path=str(tmp_path / "negative-range.mp4"),
        video_path=str(synthetic_video),
        fps=10,
        start_s=0,
        end_s=0.6,
        crop=None,
        colour_lut=None,
        speed_factor=1,
        df=pd.DataFrame({"Time (s)": [0.0, 0.3, 0.6], "signal": [-5.0, -4.0, -1.0]}),
        time_col="Time (s)",
        data_col="signal",
        offset_s=0,
        color="#000000",
        y_min=None,
        y_max=None,
        trace_transform="raw",
        max_change=2.0,
        pin_scale_bars_to_axes=True,
    )

    records = load_records(tmp_path / "export-history")
    assert records[0]["resolved"]["y_limits"][0] < -5.0
    assert records[0]["resolved"]["y_limits"][1] > -3.0
    assert records[0]["resolved"]["data_y_range"] == [-5.0, -3.0]


def test_fixed_range_keeps_all_scale_bars_outside_the_data_band():
    _app()
    anchors = ((0.8, 0.5), (0.3, 0.5), (0.8, 0.8), (0.3, 0.8))
    times = np.array([0.0, 5.0])
    values = np.array([-3.0, 2.0])
    clipped_values = np.array([-3.0, -1.0])
    for anchor in anchors:
        widget = AnimatedPlotWidget()
        widget.setup(
            [Trace(times, values)],
            anchor=anchor,
            max_change=2.0,
            pin_scale_bars_to_axes=True,
        )
        widget.update_lines([(times, values)])
        time_bar, signal_bar = widget.ax.lines[:2]
        time_y = np.asarray(time_bar.get_ydata(), dtype=float)
        signal_y = np.asarray(signal_bar.get_ydata(), dtype=float)
        displayed = np.asarray(widget._lines[0].get_ydata(), dtype=float)
        np.testing.assert_array_equal(displayed, clipped_values)
        assert widget._trace_signal_offset == 0.0
        if anchor[1] <= 0.65:
            assert np.max(time_y) < clipped_values.min()
            assert np.max(signal_y) < clipped_values.min()
            assert widget.ax.get_ylim()[1] > clipped_values.max()
        else:
            assert np.min(time_y) > clipped_values.max()
            assert np.min(signal_y) > clipped_values.max()
            assert widget.ax.get_ylim()[0] < clipped_values.min()
        assert np.all(np.diff(np.asarray(widget._lines[0].get_xdata(), dtype=float)) > 0)


def test_graph_range_summary_shows_full_range_and_selected_percentage(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab._window_display_range = (-4.0, 6.0)

    summary = tab._max_change_range_summary(50.0)

    assert "-4 to 6" in summary
    assert "(10 total)" in summary
    assert "graph shows -4 to 1" in summary
    assert "50%" in summary


def test_max_change_percentage_tracks_span_independent_of_minimum(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    assert tab.composition_settings["max_change_percent"] == 70.0
    tab.set_composition_settings({"max_change_enabled": True, "max_change_percent": 70.0})

    tab._window_display_range = (-4.0, 6.0)
    assert tab.max_change == pytest.approx(7.0)
    tab._window_display_range = (10.0, 30.0)
    assert tab.max_change == pytest.approx(14.0)
    tab._window_display_range = (10.0, 10.0)
    assert tab.max_change is None


def test_legacy_absolute_max_change_migrates_to_percentage(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.set_composition_settings({"max_change_enabled": True, "max_change": 2.5})
    pending = tab.composition_settings
    assert pending["max_change"] == 2.5
    assert "max_change_percent" not in pending
    tab._window_display_range = (-4.0, 6.0)

    tab._migrate_legacy_max_change()

    assert tab.composition_settings["max_change_percent"] == pytest.approx(25.0)
    assert "max_change" not in tab.composition_settings
    assert tab.max_change == pytest.approx(2.5)


def test_older_project_uses_auto_range_instead_of_personal_limit(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.set_composition_settings({"max_change_enabled": True, "max_change_percent": 35.0})

    tab.apply_project_export_settings({"advanced": {}})

    assert tab.composition_settings["max_change_enabled"] is False
    assert tab.composition_settings["max_change_percent"] == 70.0


def test_scale_bar_anchor_uses_plot_pane_fraction_for_all_corners():
    _app()
    anchors = ((0.8, 0.5), (0.3, 0.5), (0.8, 0.8), (0.3, 0.8))
    times = np.array([0.0, 5.0])
    values = np.array([0.0, 2.0])
    for anchor in anchors:
        widget = AnimatedPlotWidget()
        widget.setup(
            [Trace(times, values)],
            anchor=anchor,
            pin_scale_bars_to_axes=True,
        )
        widget.update_lines([(times, values)])
        time_bar, signal_bar = widget.ax.lines[:2]
        xlim = widget.ax.get_xlim()
        ylim = widget.ax.get_ylim()
        signal_x = np.asarray(signal_bar.get_xdata(), dtype=float)
        time_y = np.asarray(time_bar.get_ydata(), dtype=float)
        x_fraction = (signal_x[0] - xlim[0]) / (xlim[1] - xlim[0])
        y_fraction = (time_y[0] - ylim[0]) / (ylim[1] - ylim[0])
        assert np.isclose(x_fraction, 0.04 if anchor[0] >= 0.5 else 0.96)
        assert np.isclose(y_fraction, 0.04 if anchor[1] <= 0.65 else 0.96)
        line_x = np.asarray(widget._lines[0].get_xdata(), dtype=float)
        line_y = np.asarray(widget._lines[0].get_ydata(), dtype=float)
        time_bar_x = np.asarray(time_bar.get_xdata(), dtype=float)
        signal_bar_y = np.asarray(signal_bar.get_ydata(), dtype=float)
        if anchor[0] >= 0.5:
            assert np.isclose(line_x[0], np.mean(time_bar_x))
            signal_midpoint = float(np.mean(signal_bar_y))
            if anchor[1] <= 0.65:
                assert line_y[0] >= signal_midpoint
            else:
                assert line_y[0] <= signal_midpoint
        else:
            assert np.all(np.diff(line_x) > 0)
