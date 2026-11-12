"""Pure tests for the export's output-frame to source-frame mapping (no codec needed)."""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest

from video_data_sync.video_processing.exporter import (
    _ceil_frames,
    _frame_at,
    _output_frame_source,
)

FRAMES = 1800
UNBOUNDED_END = 10**9
FPS_TEXTS = ["24", "25", "30", "59.94", "60"]
# Clip boundaries 0.01 s .. 60.00 s in 0.01 s steps, as exact decimal strings.
TIME_TEXTS = [f"{hundredths / 100:.2f}" for hundredths in range(1, 6001)]


def test_end_frame_ceil_ignores_float_error_above_whole_frames():
    old_mismatches = 0
    for fps_text in FPS_TEXTS:
        for end_text in TIME_TEXTS:
            value = float(end_text) * float(fps_text)
            exact = math.ceil(Fraction(end_text) * Fraction(fps_text))
            assert _ceil_frames(value) == exact
            old_mismatches += int(np.ceil(value)) != exact
    # Guard: the sweep really contains cases the previous np.ceil got wrong (e.g. 0.28 s x 25).
    assert old_mismatches > 0


@pytest.mark.parametrize("start_text", ["0.0", "1.3"])
@pytest.mark.parametrize("speed_text", ["1", "0.5", "2"])
def test_total_frames_ceil_ignores_float_error_above_whole_frames(start_text, speed_text):
    start_s, speed = float(start_text), float(speed_text)
    for fps_text in FPS_TEXTS:
        fps = float(fps_text)
        for end_text in TIME_TEXTS:
            end_s = float(end_text)
            if end_s <= start_s:
                continue
            value = (end_s - start_s) * fps / speed
            exact_value = (Fraction(end_text) - Fraction(start_text)) * Fraction(fps_text)
            assert _ceil_frames(value) == math.ceil(exact_value / Fraction(speed_text))


@pytest.mark.parametrize("fps_text", ["24", "25", "30", "59.94", "60"])
@pytest.mark.parametrize("start_text", ["0.0", "1.0", "1.3"])
def test_one_to_one_export_has_no_duplicate_or_dropped_frames(fps_text, start_text):
    fps = float(fps_text)
    start_s = float(start_text)
    start_frame = _frame_at(start_s, fps)
    assert start_frame == math.floor(Fraction(start_text) * Fraction(fps_text))

    for index in range(FRAMES):
        _, frame = _output_frame_source(
            index,
            start_s=start_s,
            speed_factor=1.0,
            output_fps=fps,
            source_fps=fps,
            start_frame=start_frame,
            end_frame=UNBOUNDED_END,
        )
        assert frame == start_frame + index


@pytest.mark.parametrize("start_s", [0.0, 1.0, 1.3])
def test_one_to_one_export_at_ntsc_rate_has_no_duplicate_or_dropped_frames(start_s):
    fps = 60000 / 1001
    start_frame = _frame_at(start_s, fps)
    for index in range(FRAMES):
        _, frame = _output_frame_source(
            index,
            start_s=start_s,
            speed_factor=1.0,
            output_fps=fps,
            source_fps=fps,
            start_frame=start_frame,
            end_frame=UNBOUNDED_END,
        )
        assert frame == start_frame + index


def test_previous_truncating_mapping_fails_this_case():
    """Guard that the 1.3 s / 30 fps case really exercises the floating-point defect."""
    fps, start_s = 30.0, 1.3
    old_start = int(start_s * fps)
    mismatches = sum(
        int((start_s + index * 1.0 / fps) * fps) != old_start + index for index in range(FRAMES)
    )
    assert mismatches > 0


@pytest.mark.parametrize(
    ("start_text", "source_fps_text", "output_fps_text", "speed_text"),
    [
        ("1.3", "30", "25", "1"),
        ("0.0", "59.94", "30", "2"),
        ("1.3", "24", "60", "0.5"),
        ("2.5", "25", "30", "3"),
        ("1.0", "60", "24", "1.5"),
        ("1.3", "30", "30", "0.25"),
    ],
)
def test_general_mapping_selects_floor_of_source_time_within_clip(
    start_text, source_fps_text, output_fps_text, speed_text
):
    start_s = float(start_text)
    source_fps = float(source_fps_text)
    output_fps = float(output_fps_text)
    speed = float(speed_text)
    start_frame = _frame_at(start_s, source_fps)
    end_frame = start_frame + 500
    exact_step = Fraction(speed_text) / Fraction(output_fps_text)
    previous = start_frame

    for index in range(FRAMES):
        source_time_s, frame = _output_frame_source(
            index,
            start_s=start_s,
            speed_factor=speed,
            output_fps=output_fps,
            source_fps=source_fps,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        # Data/plot timing keeps the existing convention (data time = this + offset).
        assert source_time_s == start_s + index * speed / output_fps
        exact_time = Fraction(start_text) + index * exact_step
        exact_frame = math.floor(exact_time * Fraction(source_fps_text))
        assert frame == max(start_frame, min(end_frame - 1, exact_frame))
        assert start_frame <= frame <= end_frame - 1
        assert frame >= previous
        previous = frame


def test_mapping_is_clamped_to_last_clip_frame():
    _, frame = _output_frame_source(
        10_000,
        start_s=1.0,
        speed_factor=2.0,
        output_fps=30.0,
        source_fps=30.0,
        start_frame=30,
        end_frame=90,
    )
    assert frame == 89


def test_mapping_is_clamped_to_first_clip_frame():
    _, frame = _output_frame_source(
        0,
        start_s=0.999,
        speed_factor=1.0,
        output_fps=30.0,
        source_fps=30.0,
        start_frame=30,
        end_frame=90,
    )
    assert frame == 30
