import numpy as np
import pytest

from video_data_sync.video_processing.rotation import (
    rotate_crop,
    rotate_frame,
    rotated_dimensions,
    validate_rotation,
)


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [
        (0, [[1, 2, 3], [4, 5, 6]]),
        (90, [[4, 1], [5, 2], [6, 3]]),
        (180, [[6, 5, 4], [3, 2, 1]]),
        (270, [[3, 6], [2, 5], [1, 4]]),
    ],
)
def test_rotate_frame_uses_clockwise_quarter_turns(degrees, expected):
    frame = np.repeat(np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)[:, :, None], 3, axis=2)

    rotated = rotate_frame(frame, degrees)

    assert rotated[:, :, 0].tolist() == expected
    assert rotated_dimensions(3, 2, degrees) == (rotated.shape[1], rotated.shape[0])


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [
        (0, (2, 1, 7, 5)),
        (90, (3, 2, 7, 7)),
        (180, (3, 3, 8, 7)),
        (270, (1, 3, 5, 8)),
    ],
)
def test_rotate_crop_tracks_the_rotated_image_edges(degrees, expected):
    assert rotate_crop((2, 1, 7, 5), 10, 8, degrees) == expected


@pytest.mark.parametrize("degrees", [True, 90.0, -90, 45, "90"])
def test_rotation_rejects_unsupported_or_non_integer_angles(degrees):
    with pytest.raises(ValueError, match="0, 90, 180, or 270"):
        validate_rotation(degrees)
