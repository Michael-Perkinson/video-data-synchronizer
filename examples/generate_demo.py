"""Generate a small, deterministic video/time-series synchronization demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from video_data_sync.demo import DEFAULT_DURATION_S, DEFAULT_FPS, demo_dataframe, generate_demo

__all__ = ["DEFAULT_DURATION_S", "DEFAULT_FPS", "demo_dataframe", "generate_demo"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir", nargs="?", type=Path, default=Path(__file__).parent / "generated_demo"
    )
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S, help="Seconds")
    args = parser.parse_args()
    video_path, csv_path = generate_demo(args.output_dir, args.duration)
    print(f"Created constant-frame-rate video: {video_path.resolve()}")
    print(f"Created synthetic trace data: {csv_path.resolve()}")


if __name__ == "__main__":
    main()
