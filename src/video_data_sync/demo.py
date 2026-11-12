"""Small deterministic inputs used by the in-app demo and documentation."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pandas as pd

DEFAULT_DURATION_S = 6.0
DEFAULT_FPS = 20.0
DEFAULT_SAMPLE_RATE_HZ = 50.0
EVENT_TIMES_S = (2.0, 4.5)


def demo_dataframe(
    duration_s: float = DEFAULT_DURATION_S,
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    seed: int = 20260713,
) -> pd.DataFrame:
    if duration_s <= 0 or sample_rate_hz <= 0:
        raise ValueError("Duration and sample rate must be positive.")
    sample_count = int(round(duration_s * sample_rate_hz)) + 1
    time_s = np.linspace(0.0, duration_s, sample_count)
    rng = np.random.default_rng(seed)
    signal = 0.025 * np.sin(2 * np.pi * 0.35 * time_s)
    control = 0.012 * np.sin(2 * np.pi * 0.35 * time_s + 0.4)
    for event_time in EVENT_TIMES_S:
        signal += 0.20 * np.exp(-0.5 * ((time_s - event_time) / 0.16) ** 2)
    signal += rng.normal(0.0, 0.003, sample_count)
    control += rng.normal(0.0, 0.002, sample_count)
    return pd.DataFrame(
        {
            "Time (s)": time_s,
            "Synthetic response (AU)": signal,
            "Synthetic control (AU)": control,
        }
    )


def write_demo_video(path: Path, duration_s: float, fps: float = DEFAULT_FPS) -> None:
    if duration_s <= 0 or fps <= 0:
        raise ValueError("Duration and frame rate must be positive.")
    width, height = 320, 240
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("The local OpenCV build could not create an MP4 demo video.")
    try:
        for frame_index in range(int(round(duration_s * fps))):
            time_s = frame_index / fps
            frame = np.full((height, width, 3), (30, 30, 30), dtype=np.uint8)
            x = 35 + int((width - 70) * time_s / duration_s)
            cv2.circle(frame, (x, 145), 15, (220, 180, 60), -1)
            active = any(abs(time_s - event) < 0.18 for event in EVENT_TIMES_S)
            cv2.circle(frame, (160, 80), 30, (40, 40, 240) if active else (70, 70, 70), -1)
            cv2.putText(
                frame,
                f"Synthetic demo  {time_s:04.2f} s",
                (28, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (235, 235, 235),
                1,
                cv2.LINE_AA,
            )
            writer.write(frame)
    finally:
        writer.release()


def generate_demo(output_dir: Path, duration_s: float = DEFAULT_DURATION_S) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "synthetic_timeseries.csv"
    video_path = output_dir / "synthetic_video.mp4"
    demo_dataframe(duration_s=duration_s).to_csv(csv_path, index=False)
    write_demo_video(video_path, duration_s=duration_s)
    return video_path, csv_path
