import cv2
import numpy as np

from video_data_sync.video_processing.capture import VideoCapture
from video_data_sync.video_processing.colour import apply_correction, build_lut
from video_data_sync.video_processing.cropper import CropRect


def test_colour_identity_and_channel_gain():
    frame = np.full((2, 2, 3), 100, dtype=np.uint8)
    assert build_lut() is None
    assert apply_correction(frame) is frame
    corrected = apply_correction(frame, r_gain=2.0)
    assert corrected[0, 0].tolist() == [100, 100, 200]


def test_capture_time_mapping(tmp_path):
    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 10.0, (16, 12))
    assert writer.isOpened()
    for value in range(5):
        writer.write(np.full((12, 16, 3), value * 20, dtype=np.uint8))
    writer.release()
    with VideoCapture(str(path)) as capture:
        assert capture.frame_count == 5
        assert capture.frame_index_at_time(0.2) == 2
        assert capture.time_at_frame(2) == 0.2


def test_capture_extracts_the_final_frame_at_video_duration(synthetic_video):
    with VideoCapture(str(synthetic_video)) as capture:
        full_clip = capture.extract_frames(0.0, capture.duration_s)
        final_interval = capture.extract_frames(capture.duration_s - 0.2, capture.duration_s)

    assert len(full_clip) == 12
    assert len(final_interval) == 2


def test_capture_rejects_corrupt_video(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    try:
        VideoCapture(str(path))
    except OSError as exc:
        assert "Cannot open video" in str(exc)
    else:
        raise AssertionError("Corrupt video was accepted")


def test_crop_rect_validation_and_normalization():
    assert CropRect(8, 7, 2, 3).to_tuple() == (2, 3, 8, 7)
    assert CropRect(8, 7, 2, 3).is_valid()
    assert not CropRect(2, 3, 2, 7).is_valid()
