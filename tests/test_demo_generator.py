import cv2
import numpy as np
import pandas as pd
import pytest

from examples.generate_demo import DEFAULT_FPS, demo_dataframe, generate_demo


def test_demo_dataframe_is_deterministic_and_has_aligned_event_peak():
    first = demo_dataframe(duration_s=3.0, sample_rate_hz=20.0)
    second = demo_dataframe(duration_s=3.0, sample_rate_hz=20.0)
    assert first.equals(second)
    assert len(first) == 61
    assert first["Time (s)"].iloc[-1] == pytest.approx(3.0)
    event_window = np.abs(first["Time (s)"] - 2.0) <= 0.2
    assert first.loc[event_window, "Synthetic response (AU)"].max() > 0.15


@pytest.mark.parametrize("duration,rate", [(0, 20), (3, 0), (-1, 20)])
def test_demo_dataframe_rejects_non_positive_parameters(duration, rate):
    with pytest.raises(ValueError, match="positive"):
        demo_dataframe(duration_s=duration, sample_rate_hz=rate)


def test_generate_demo_writes_readable_cfr_video_and_csv(tmp_path):
    video_path, csv_path = generate_demo(tmp_path, duration_s=1.0)
    frame = pd.read_csv(csv_path)
    assert list(frame.columns) == [
        "Time (s)",
        "Synthetic response (AU)",
        "Synthetic control (AU)",
    ]
    capture = cv2.VideoCapture(str(video_path))
    try:
        assert capture.isOpened()
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(DEFAULT_FPS, rel=0.01)
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == int(DEFAULT_FPS)
        ok, image = capture.read()
        assert ok and image is not None
    finally:
        capture.release()
