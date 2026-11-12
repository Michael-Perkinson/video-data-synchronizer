"""Compatibility launcher for running from a source checkout."""

import sys
from pathlib import Path


def main() -> None:
    """Launch the application without requiring an editable install first."""
    source_root = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(source_root))

    from video_data_sync.app import main as package_main

    package_main()


if __name__ == "__main__":
    main()
