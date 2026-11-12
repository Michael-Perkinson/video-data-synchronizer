import cv2
import numpy as np
import pandas as pd
import pytest
from matplotlib import pyplot as plt
from qt_teardown import OwnedPyplotFigures

from video_data_sync.data.transforms import normalise_window_values, window_minimum
from video_data_sync.plotting.composition import composition_geometry, named_anchor
from video_data_sync.video_processing.exporter import (
    ExportCancelled,
    _draw_scale_bars,
    export_video,
)


def _data():
    t = np.arange(0, 1.21, 0.05)
    return pd.DataFrame({"Time (s)": t, "signal": np.sin(t)})


@pytest.fixture
def owned_figures():
    """Close and destroy only the pyplot figures this test registers."""
    owner = OwnedPyplotFigures()
    yield owner
    owner.dispose_all()


def test_window_normalisation_uses_the_minimum_across_the_window():
    values, baseline = normalise_window_values([np.array([10.0, 12.0, 8.0])])

    assert baseline == -2.0
    assert values[0].tolist() == [2.0, 4.0, 0.0]
    assert window_minimum([np.array([10.0, 12.0, 8.0])]) == 8.0


def test_composition_geometry_side_by_side_and_stacked():
    side = composition_geometry(64, 48, 0.6, "side_by_side")
    assert side.output_width == side.video_width + side.plot_width
    assert side.video_height == side.plot_height == side.output_height == 540
    stacked = composition_geometry(64, 48, 0.6, "stacked")
    assert stacked.output_height == stacked.video_height + stacked.plot_height
    assert stacked.video_width == stacked.plot_width == stacked.output_width == 540


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Bottom right", (0.3, 0.5)),
        ("Bottom left", (0.8, 0.5)),
        ("Top right", (0.3, 0.8)),
        ("Top left", (0.8, 0.8)),
    ],
)
def test_named_anchor(name, expected):
    assert named_anchor(name) == expected


def test_export_scale_bar_custom_labels_keep_numeric_lengths(owned_figures):
    figure, ax = plt.subplots()
    owned_figures(figure)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    _draw_scale_bars(
        ax,
        1,
        1,
        2,
        3,
        "detected unit",
        time_label="sec",
        signal_label="AU",
    )

    assert [text.get_text() for text in ax.texts] == ["2 sec", "3 AU"]
    plt.close(figure)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"video_width_fraction": 0.01},
        {"scale_bar_font_size": 0},
        {"scale_bar_linewidth": -1},
        {"time_scale_s": 0},
        {"signal_scale": -2},
        {"scale_bar_anchor_x": 2},
    ],
)
def test_export_rejects_invalid_composition_settings(synthetic_video, tmp_path, kwargs):
    with pytest.raises(ValueError):
        export_video(
            str(tmp_path / "bad.mp4"),
            str(synthetic_video),
            10,
            0,
            0.5,
            None,
            None,
            1,
            _data(),
            "Time (s)",
            "signal",
            0,
            "blue",
            None,
            None,
            **kwargs,
        )


def test_export_end_to_end_and_verifies_readable_output(synthetic_video, tmp_path):
    output = tmp_path / "out.mp4"
    export_video(
        str(output),
        str(synthetic_video),
        10,
        0,
        0.6,
        (0, 0, 64, 48),
        None,
        1,
        _data(),
        "Time (s)",
        "signal",
        0,
        "#00aa00",
        None,
        None,
    )
    cap = cv2.VideoCapture(str(output))
    assert cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 6
    assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 540
    cap.release()


def test_export_rotates_before_applying_crop(synthetic_video, tmp_path):
    output = tmp_path / "rotated.mp4"
    export_video(
        str(output),
        str(synthetic_video),
        10,
        0,
        0.6,
        (6, 8, 42, 56),
        None,
        1,
        _data(),
        "Time (s)",
        "signal",
        0,
        "blue",
        None,
        None,
        rotation=90,
    )

    cap = cv2.VideoCapture(str(output))
    geometry = composition_geometry(36, 48, 0.6, "side_by_side")
    assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (
        geometry.output_width,
        geometry.output_height,
    )
    cap.release()


def test_cancel_preserves_existing_destination(synthetic_video, tmp_path):
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"original")
    with pytest.raises(ExportCancelled):
        export_video(
            str(output),
            str(synthetic_video),
            10,
            0,
            1,
            None,
            None,
            1,
            _data(),
            "Time (s)",
            "signal",
            0,
            "blue",
            None,
            None,
            cancel_callback=lambda: True,
        )
    assert output.read_bytes() == b"original"


def test_stacked_export_dimensions_and_terminal_progress(synthetic_video, tmp_path):
    output = tmp_path / "stacked.mp4"
    progress = []
    export_video(
        str(output),
        str(synthetic_video),
        10,
        0,
        0.3,
        None,
        None,
        1,
        _data(),
        "Time (s)",
        "signal",
        0,
        "blue",
        None,
        None,
        orientation="stacked",
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    cap = cv2.VideoCapture(str(output))
    expected = composition_geometry(64, 48, 0.6, "stacked")
    assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (
        expected.output_width,
        expected.output_height,
    )
    cap.release()
    assert progress[-1] == (3, 3)


@pytest.mark.parametrize(("speed", "expected_frames"), [(0.5, 12), (2.0, 3)])
def test_speed_resamples_timeline_at_normal_fps(synthetic_video, tmp_path, speed, expected_frames):
    output = tmp_path / f"speed-{speed}.mp4"
    export_video(
        str(output),
        str(synthetic_video),
        10,
        0,
        0.6,
        None,
        None,
        speed,
        _data(),
        "Time (s)",
        "signal",
        0,
        "blue",
        None,
        None,
    )
    cap = cv2.VideoCapture(str(output))
    assert cap.isOpened()
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == expected_frames
    assert cap.get(cv2.CAP_PROP_FPS) <= 60
    cap.release()
