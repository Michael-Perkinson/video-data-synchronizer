from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import to_rgba
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QSizePolicy
from qt_teardown import OwnedWidgets

from video_data_sync.gui.animated_tab import (
    MAX_VIDEO_WIDTH_FRACTION,
    MIN_VIDEO_WIDTH_FRACTION,
    AnimatedTab,
    AspectRatioLabel,
)
from video_data_sync.plotting.composition import (
    axes_composition,
    place_trace_origin,
    scale_label_figure_margins,
)
from video_data_sync.plotting.plot_widget import AnimatedPlotWidget, Trace


def _app():
    return QApplication.instance() or QApplication([])


def _drop_test_fake_capture(tab) -> None:
    # Some tests inject object() as tab._cap; closeEvent would call .release() on it.
    cap = getattr(tab, "_cap", None)
    if cap is not None and not callable(getattr(cap, "release", None)):
        tab._cap = None


@pytest.fixture
def owned_widgets():
    """Dispose only the top-level widgets this test registers.

    Request it before ``monkeypatch`` so instance patches are undone first.
    """
    owner = OwnedWidgets(before_dispose=_drop_test_fake_capture)
    yield owner
    owner.dispose_all()


def test_preview_applies_complete_advanced_composition_model():
    _app()
    widget = AnimatedPlotWidget()
    trace = Trace(np.array([0.0, 10.0]), np.array([0.0, 8.0]), label="dF/F")
    widget.setup(
        [trace],
        show_time_scale=False,
        show_signal_scale=True,
        scale_font_size=13,
        scale_line_width=4.5,
        time_scale_s=2.0,
        signal_scale=4.0,
        anchor=(0.8, 0.8),
        background="#123456",
    )

    assert widget.ax.get_facecolor() == to_rgba("#ffffff")
    assert widget.figure.get_facecolor() == to_rgba("#ffffff")
    assert len(widget.ax.texts) == 1
    assert widget.ax.texts[0].get_fontsize() == 13
    scale_line = widget.ax.lines[0]
    assert scale_line.get_linewidth() == 4.5
    assert np.allclose(scale_line.get_xdata(), [-2.1, -2.1])
    assert np.allclose(scale_line.get_ydata(), [12.2, 8.2])


def test_preview_can_hide_both_scale_bars():
    _app()
    widget = AnimatedPlotWidget()
    widget.setup(
        [Trace(np.array([0.0, 1.0]), np.array([0.0, 1.0]))],
        show_time_scale=False,
        show_signal_scale=False,
    )
    assert len(widget.ax.texts) == 0
    assert len(widget.ax.lines) == 1  # animated trace only


def test_preview_custom_scale_labels_keep_numeric_lengths():
    _app()
    widget = AnimatedPlotWidget()
    widget.setup(
        [Trace(np.array([0.0, 120.0]), np.array([0.0, 8.0]), label="dF/F")],
        time_scale_s=60.0,
        signal_scale=4.0,
        time_scale_label="seconds",
        signal_scale_label="custom units",
    )

    assert [text.get_text() for text in widget.ax.texts] == [
        "60 seconds",
        "4 custom units",
    ]


def test_advanced_reset_restores_every_default_and_requests_preview(owned_widgets, monkeypatch):
    _app()
    tab = owned_widgets(AnimatedTab())
    updates = []
    monkeypatch.setattr(tab, "_setup_plot_if_ready", lambda: updates.append(True))
    tab.set_composition_settings(
        {
            "scale_font_size": 31,
            "scale_line_width": 7.0,
            "show_time_scale": False,
            "show_signal_scale": False,
            "time_scale_s": 12.0,
            "signal_scale": 5.0,
            "time_scale_label": "sec",
            "signal_scale_label": "AU",
            "anchor": "Top left",
            "background": "#abcdef",
        }
    )
    updates.clear()
    tab._reset_composition_defaults()
    assert tab.composition_settings == AnimatedTab.COMPOSITION_DEFAULTS
    assert updates == [True]


def test_plot_background_is_locked_to_white_in_tab_settings(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.set_composition_settings({"background": "#abcdef"})

    assert tab.composition_settings["background"] == "#ffffff"


def test_scale_label_margins_adapt_to_short_panes_and_corner():
    left, right, bottom, top = scale_label_figure_margins(
        3.2,
        2.0,
        16.0,
        2.8,
        (0.8, 0.8),
        show_time=True,
        show_signal=True,
    )

    assert left > 0.035
    assert right == 0.035
    assert bottom == 0.035
    assert top > 0.15
    axis_width = 1.0 - left - right
    axis_height = 1.0 - bottom - top
    side_clearance_points = (left + 0.04 * axis_width) * 3.2 * 72.0
    top_clearance_points = (top + 0.04 * axis_height) * 2.0 * 72.0
    assert side_clearance_points >= 1.2 * 16.0 + 2.8 / 2 + 2.0
    assert top_clearance_points >= 1.7 * 16.0 + 2.8 / 2 + 2.0


@pytest.mark.parametrize("anchor", [(0.8, 0.5), (0.3, 0.5), (0.8, 0.8), (0.3, 0.8)])
def test_scale_bar_labels_start_at_the_shared_origin(anchor):
    _app()
    widget = AnimatedPlotWidget()
    trace = Trace(np.array([0.0, 10.0]), np.array([0.0, 8.0]))

    widget.setup([trace], anchor=anchor, time_scale_s=2.0, signal_scale=4.0)

    time_bar, signal_bar = widget.ax.lines[:2]
    time_text, signal_text = widget.ax.texts
    origin = np.array(
        [
            float(np.asarray(time_bar.get_xdata(), dtype=float)[0]),
            float(np.asarray(time_bar.get_ydata(), dtype=float)[0]),
        ],
        dtype=float,
    )
    time_offset = np.asarray(time_text.get_position(), dtype=float)
    signal_offset = np.asarray(signal_text.get_position(), dtype=float)
    left = anchor[0] > 0.5
    bottom = anchor[1] <= 0.65
    signal_points_up = bottom
    assert np.allclose(cast(Any, time_text).xy, origin)
    assert np.allclose(cast(Any, signal_text).xy, origin)
    assert time_text.get_horizontalalignment() == ("left" if left else "right")
    assert time_offset[1] < 0 if bottom else time_offset[1] > 0
    expected_signal_x_offset = -(2.8 / 2 + 2.0) if left else (2.8 / 2 + 2.0)
    np.testing.assert_allclose(signal_offset, (expected_signal_x_offset, 0.0))
    assert signal_text.get_horizontalalignment() == "left"
    assert signal_text.get_verticalalignment() == ("bottom" if left == bottom else "top")
    assert signal_text.get_rotation_mode() == "anchor"
    assert signal_text.get_rotation() == (90 if signal_points_up else 270)
    signal_y = np.asarray(signal_bar.get_ydata(), dtype=float)
    assert signal_y[1] > origin[1] if signal_points_up else signal_y[1] < origin[1]


@pytest.mark.parametrize("anchor", [(0.8, 0.5), (0.3, 0.5), (0.8, 0.8), (0.3, 0.8)])
def test_all_scale_bar_anchors_keep_bars_outside_trace_bounds(anchor):
    _app()
    widget = AnimatedPlotWidget()
    trace = Trace(np.array([5.0, 15.0]), np.array([-8.0, 0.0]))
    widget.setup([trace], anchor=anchor, time_scale_s=2.0, signal_scale=4.0)

    time_bar, vertical_bar = widget.ax.lines[:2]
    time_x = np.asarray(time_bar.get_xdata(), dtype=float)
    vertical_x = np.asarray(vertical_bar.get_xdata(), dtype=float)
    vertical_y = np.asarray(vertical_bar.get_ydata(), dtype=float)
    trace_times = np.asarray(trace.time_s, dtype=float)
    trace_values = np.asarray(trace.values, dtype=float)
    if anchor[0] < 0.5:
        assert np.min(time_x) > trace_times[-1]
        assert vertical_x[0] > trace_times[-1]
    else:
        assert np.max(time_x) < trace_times[0]
        assert vertical_x[0] < trace_times[0]
    if anchor[1] > 0.65:
        assert np.min(vertical_y) > np.max(trace_values)
    else:
        assert np.max(vertical_y) < np.min(trace_values)


def test_speed_controls_explain_single_and_batch_exports(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.speed_combo.setCurrentText("1x")

    assert tab.speed_combo.itemText(2) == "1x"
    assert "Export Video" in tab.speed_combo.toolTip()
    assert "Export Speeds" in tab.speed_combo.toolTip()
    assert tab.btn_export.text() == "Export Video (1x)"
    assert tab.btn_export.toolTip() == "Export one video at the selected Preview speed."
    assert tab.btn_export_multiple.text() == "Export Speeds…"
    assert "independently" in tab.btn_export_multiple.toolTip()


def test_invalid_editable_speed_is_rejected_before_single_export(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.speed_combo.setCurrentText("not-a-speed")

    assert not tab.validate_speed()


def test_splitter_keeps_plot_pane_visible_for_wide_video(owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1200, 800)
    tab.show()
    app.processEvents()
    tab._frame_w, tab._frame_h = 1920, 1080
    tab._set_default_split()
    app.processEvents()

    display_width = tab.display_container.width()
    display_height = tab.display_container.height()
    expected = min(
        max(display_height * tab._frame_w / tab._frame_h / display_width, MIN_VIDEO_WIDTH_FRACTION),
        MAX_VIDEO_WIDTH_FRACTION,
    )
    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(expected, abs=0.03)
    assert sizes[1] / sum(sizes) >= 1 - MAX_VIDEO_WIDTH_FRACTION - 0.03
    assert tab.video_label.alignment() & Qt.AlignmentFlag.AlignHCenter


def test_dragged_split_survives_video_reload_and_resize(synthetic_video, owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    app.processEvents()

    tab.display_container.setSizes([400, 800])
    tab._on_splitter_moved()  # what a real handle drag emits
    tab.update_video(str(synthetic_video), 10.0, 0.0, 1.0, None, None)
    tab.resize(2000, 900)
    app.processEvents()

    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(400 / 1200, abs=0.02)

    tab.reset_state()
    tab._frame_w, tab._frame_h = 4000, 1000
    tab.resize(1900, 900)
    app.processEvents()
    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(MAX_VIDEO_WIDTH_FRACTION, abs=0.02)
    tab._release_cap()
    tab.close()


def test_dragged_split_is_clamped_to_the_restorable_maximum(owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    app.processEvents()

    tab.display_container.setSizes([900, 100])
    tab._on_splitter_moved()
    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(MAX_VIDEO_WIDTH_FRACTION, abs=0.02)

    tab._restore_split(sizes)
    restored_sizes = tab.display_container.sizes()
    assert restored_sizes[0] / sum(restored_sizes) == pytest.approx(
        MAX_VIDEO_WIDTH_FRACTION, abs=0.02
    )
    tab.close()


def test_advanced_dialog_preserves_supported_large_scale_font(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab._composition["scale_font_size"] = 60

    def accept_active_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        dialog.accept()

    QTimer.singleShot(0, accept_active_dialog)
    tab._show_composition_dialog()

    assert tab.composition_settings["scale_font_size"] == 60
    tab.close()


def test_play_hint_is_moved_opposite_the_scale_bar_anchor(owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1200, 800)
    tab.show()
    app.processEvents()
    tab.set_composition_settings({"anchor": "Top left"})
    app.processEvents()

    hint = tab.lbl_plot_hint.geometry()
    plot = tab.anim_plot.rect()
    assert hint.right() == plot.right() - 8
    assert hint.top() > plot.center().y()
    tab.close()


def test_saved_project_split_survives_video_reload_and_resize(synthetic_video, owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    app.processEvents()

    tab.apply_project_export_settings({"splitter_sizes": [500, 500]})
    tab.update_video(str(synthetic_video), 10.0, 0.0, 1.0, None, None)
    tab.resize(2000, 900)
    app.processEvents()

    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(0.5, abs=0.02)
    tab._release_cap()
    tab.close()


def test_project_with_starved_plot_split_reopens_at_aspect_default(synthetic_video, owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    app.processEvents()

    # Saved under the old 0.85 cap: plot pane only ~15%.
    tab.apply_project_export_settings({"splitter_sizes": [1080, 190]})
    tab._frame_w, tab._frame_h = 4000, 1000
    tab.resize(1900, 900)
    app.processEvents()

    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(MAX_VIDEO_WIDTH_FRACTION, abs=0.02)
    tab.close()


def test_legacy_project_without_split_keeps_aspect_default(synthetic_video, owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    app.processEvents()

    tab.apply_project_export_settings({})
    tab._frame_w, tab._frame_h = 4000, 1000
    tab.resize(1900, 900)
    app.processEvents()

    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(MAX_VIDEO_WIDTH_FRACTION, abs=0.02)
    tab.close()


def test_long_status_message_wraps_instead_of_widening_the_window(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    layout = tab.layout()
    assert layout is not None
    layout.activate()
    baseline = tab.minimumSizeHint().width()

    message = (
        "No data overlap — adjust Sync Offset on Full Data Plot. "
        "Data: 0.0–100.0 s  |  Video window (+ offset): 250.0–310.0 s"
    )
    tab.lbl_status.setText(message)
    layout.activate()

    single_line_width = tab.lbl_status.fontMetrics().horizontalAdvance(message)
    assert tab.minimumSizeHint().width() - baseline < single_line_width // 4


def test_empty_plot_shows_play_hint_until_playback_starts(synthetic_video, owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    tab.update_video(str(synthetic_video), 10.0, 0.0, 1.0, None, None)
    assert tab.lbl_plot_hint.isHidden()

    tab.update_data(
        pd.DataFrame({"t": [0.0, 0.5, 1.0], "signal": [0.0, 1.0, 2.0]}),
        "t",
        "signal",
        "#000000",
        None,
        None,
    )
    assert not tab.lbl_plot_hint.isHidden()
    assert "Play" in tab.lbl_plot_hint.text()

    tab._on_play()
    assert tab.lbl_plot_hint.isHidden()
    tab._on_reset()
    assert not tab.lbl_plot_hint.isHidden()
    tab._release_cap()


def test_initial_animation_starts_without_a_dot(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    cast(Any, tab)._cap = object()
    tab._start_s = 0.0
    tab._end_s = 1.0
    tab._df = pd.DataFrame({"t": [0.0, 0.5, 1.0], "signal": [0.0, 1.0, 2.0]})
    tab._time_col = "t"
    tab._data_col = "signal"
    tab._rebuild_trace_cache()

    tab._setup_plot_if_ready()

    assert np.asarray(tab.anim_plot.ax.lines[-1].get_xdata(), dtype=float).size == 0
    assert np.asarray(tab.anim_plot.ax.lines[-1].get_ydata(), dtype=float).size == 0
    assert tab.anim_plot.ax.lines[-1].get_marker() == "None"


def test_no_overlap_shows_inline_guidance_until_play_is_requested(owned_widgets, monkeypatch):
    _app()
    tab = owned_widgets(AnimatedTab())
    cast(Any, tab)._cap = object()
    tab._start_s = 2.0
    tab._end_s = 3.0
    tab._df = pd.DataFrame({"t": [0.0, 1.0], "signal": [0.0, 1.0]})
    tab._time_col = "t"
    tab._data_col = "signal"
    tab._rebuild_trace_cache()
    warnings = []
    monkeypatch.setattr(
        "video_data_sync.gui.animated_tab.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )

    tab._setup_plot_if_ready()

    assert warnings == []
    assert "Sync Offset on Full Data Plot" in tab.lbl_status.text()
    tab._on_play()
    assert len(warnings) == 1
    assert "Sync Offset on the Full Data Plot tab" in warnings[0][2]


def test_animation_scale_bars_are_pinned_to_the_pane_bottom_left(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    cast(Any, tab)._cap = object()
    tab._start_s = 0.0
    tab._end_s = 1.0
    tab._df = pd.DataFrame({"t": [0.0, 0.5, 1.0], "signal": [0.0, 1.0, 2.0]})
    tab._time_col = "t"
    tab._data_col = "signal"
    tab._rebuild_trace_cache()

    tab._setup_plot_if_ready()

    time_bar, signal_bar = tab.anim_plot.ax.lines[:2]
    xlim = tab.anim_plot.ax.get_xlim()
    ylim = tab.anim_plot.ax.get_ylim()
    signal_x = np.asarray(signal_bar.get_xdata(), dtype=float)
    time_y = np.asarray(time_bar.get_ydata(), dtype=float)
    assert np.isclose((signal_x[0] - xlim[0]) / (xlim[1] - xlim[0]), 0.04)
    assert np.isclose((time_y[0] - ylim[0]) / (ylim[1] - ylim[0]), 0.04)


def test_animation_scale_bars_stay_pinned_when_trace_crosses_its_minimum(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    cast(Any, tab)._cap = object()
    tab._start_s = 0.0
    tab._end_s = 1.0
    tab._df = pd.DataFrame({"t": [0.0, 0.5, 1.0], "signal": [0.0, -2.0, -1.0]})
    tab._time_col = "t"
    tab._data_col = "signal"
    tab._rebuild_trace_cache()

    tab._setup_plot_if_ready()

    time_bar, signal_bar = tab.anim_plot.ax.lines[:2]
    xlim = tab.anim_plot.ax.get_xlim()
    ylim = tab.anim_plot.ax.get_ylim()
    signal_x = np.asarray(signal_bar.get_xdata(), dtype=float)
    time_y = np.asarray(time_bar.get_ydata(), dtype=float)
    assert np.isclose((signal_x[0] - xlim[0]) / (xlim[1] - xlim[0]), 0.04)
    assert np.isclose((time_y[0] - ylim[0]) / (ylim[1] - ylim[0]), 0.04)


def test_origin_shifts_up_when_a_later_segment_drops_below_the_bar():
    comp = axes_composition(0.0, 5.0, -2.0, 0.0, time_scale=2.0, signal_scale=1.0)
    # The first segment is flat above the bar; a later segment dips below it.
    t = np.array([0.0, 0.5, 1.0, 1.5])
    v = np.array([0.0, 0.0, -3.0, 0.0])

    times, signals, out_comp, placement = place_trace_origin(comp, [t], [v])

    assert placement.used_fallback
    bar_left, bar_right = sorted(
        (out_comp.bar_x, out_comp.bar_x + out_comp.time_direction * out_comp.time_scale)
    )
    # place_trace_origin already returns coordinates with its offsets applied.
    rendered_x = times[0]
    overlap = signals[0][(rendered_x >= bar_left) & (rendered_x <= bar_right)]
    assert overlap.size > 0
    assert float(overlap.min()) >= out_comp.bar_y


def test_origin_leaves_trace_alone_when_all_overlapping_segments_clear_the_bar():
    comp = axes_composition(0.0, 5.0, -2.0, 0.0, time_scale=2.0, signal_scale=1.0)
    t = np.array([0.0, 0.5, 1.0, 1.5])
    v = np.array([0.0, 0.0, 1.0, 0.0])

    _times, _signals, _out_comp, placement = place_trace_origin(comp, [t], [v])

    assert placement.used_fallback is False


def test_animation_subtracts_the_window_minimum_from_all_trace_samples(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    cast(Any, tab)._cap = object()
    tab._fps = 1.0
    tab._start_s = 0.0
    tab._end_s = 1.0
    tab._df = pd.DataFrame({"t": [0.0, 0.5, 1.0], "signal": [10.0, 12.0, 8.0]})
    tab._time_col = "t"
    tab._data_col = "signal"
    tab._rebuild_trace_cache()

    tab._setup_plot_if_ready()
    tab._update_data_line(2)

    values = np.asarray(tab.anim_plot.ax.lines[-1].get_ydata(), dtype=float)
    assert values - np.min(values) == pytest.approx([2.0, 4.0, 0.0])
    assert tab._plot_baseline == -2.0
    assert tab._window_y_min == 8.0


def test_export_actions_are_grouped_in_a_separate_row(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())

    layout = tab.layout()
    assert layout is not None and layout.count() == 2
    assert tab.btn_export.styleSheet() == tab.btn_export_multiple.styleSheet()


def test_layout_is_fixed_side_by_side_and_keeps_full_size(owned_widgets):
    app = _app()
    tab = owned_widgets(AnimatedTab())
    tab.resize(1000, 700)
    tab.show()
    app.processEvents()

    initial_size = tab.size()
    assert tab.layout_combo.count() == 1
    assert tab.layout_combo.itemText(0) == "Side by side"
    assert tab.display_container.orientation() == Qt.Orientation.Horizontal
    horizontal_sizes = tab.display_container.sizes()
    assert horizontal_sizes[0] / sum(horizontal_sizes) == pytest.approx(0.6, abs=0.02)
    assert tab.size() == initial_size

    tab._frame_w, tab._frame_h = 640, 480
    tab._set_default_split()
    expected = min(
        max(
            (tab.display_container.height() * tab._frame_w / tab._frame_h)
            / (tab.display_container.width() - tab.display_container.handleWidth()),
            MIN_VIDEO_WIDTH_FRACTION,
        ),
        MAX_VIDEO_WIDTH_FRACTION,
    )
    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(expected, abs=0.02)

    tab.resize(1400, 900)
    app.processEvents()
    expected = min(
        max(
            (tab.display_container.height() * tab._frame_w / tab._frame_h)
            / (tab.display_container.width() - tab.display_container.handleWidth()),
            MIN_VIDEO_WIDTH_FRACTION,
        ),
        MAX_VIDEO_WIDTH_FRACTION,
    )
    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(expected, abs=0.02)

    assert tab.video_label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert tab.anim_plot.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert tab.display_container.handleWidth() == 10
    assert "Drag to resize" in tab.display_container.handle(1).toolTip()


def test_video_preview_letterboxes_instead_of_clipping():
    _app()
    label = AspectRatioLabel()
    label.resize(200, 200)
    source = QPixmap(400, 200)
    source.fill(QColor("red"))

    label.set_source_pixmap(source)

    displayed = label.pixmap()
    assert displayed is not None
    assert displayed.size().width() == 200
    assert displayed.size().height() == 100


def test_scale_bars_end_before_the_trace_begins():
    _app()
    widget = AnimatedPlotWidget()
    trace = Trace(np.array([5.0, 15.0]), np.array([0.0, -8.0]))
    widget.setup([trace], time_scale_s=2.0, signal_scale=4.0)

    time_bar, vertical_bar = widget.ax.lines[:2]
    trace_times = np.asarray(trace.time_s, dtype=float)
    trace_values = np.asarray(trace.values, dtype=float)
    time_x = np.asarray(time_bar.get_xdata(), dtype=float)
    vertical_x = np.asarray(vertical_bar.get_xdata(), dtype=float)
    vertical_y = np.asarray(vertical_bar.get_ydata(), dtype=float)
    trace_start = trace_times[0]
    assert np.max(time_x) < trace_start
    assert vertical_x[0] < trace_start
    assert np.max(vertical_y) < np.min(trace_values)


def test_bottom_right_mirrors_time_bar_after_the_trace():
    _app()
    widget = AnimatedPlotWidget()
    trace = Trace(np.array([5.0, 15.0]), np.array([0.0, -8.0]))
    widget.setup([trace], time_scale_s=2.0, signal_scale=4.0, anchor=(0.3, 0.5))

    time_bar, vertical_bar = widget.ax.lines[:2]
    trace_times = np.asarray(trace.time_s, dtype=float)
    trace_values = np.asarray(trace.values, dtype=float)
    time_x = np.asarray(time_bar.get_xdata(), dtype=float)
    vertical_x = np.asarray(vertical_bar.get_xdata(), dtype=float)
    vertical_y = np.asarray(vertical_bar.get_ydata(), dtype=float)
    trace_end = trace_times[-1]
    assert np.min(time_x) > trace_end
    assert time_x[1] < time_x[0]
    assert vertical_x[0] > trace_end
    assert np.max(vertical_y) < np.min(trace_values)


def test_default_trace_line_width_is_1_5(owned_widgets):
    _app()
    tab = owned_widgets(AnimatedTab())
    assert tab.linewidth == 1.5


def test_preset_panel_proportions_are_remembered_for_new_tabs(owned_widgets):
    from PySide6.QtCore import QSettings

    app = _app()
    QSettings().setValue("composition/panelSizes", [2, 3])
    tab = owned_widgets(AnimatedTab())
    tab.resize(1800, 800)
    tab.show()
    tab._frame_w, tab._frame_h = 4000, 1000
    tab.resize(1900, 900)
    app.processEvents()

    sizes = tab.display_container.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(0.4, abs=0.02)
    tab.close()
