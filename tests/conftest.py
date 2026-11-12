import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_qsettings(tmp_path):
    """Keep tests out of the user's registry/plist preferences and each other's state."""
    from PySide6.QtCore import QSettings

    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings")
    )
    yield
    QSettings.setDefaultFormat(previous_format)


@pytest.fixture(autouse=True)
def isolated_export_history(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "video_data_sync.export_history.history_directory", lambda: tmp_path / "export-history"
    )


@pytest.fixture(autouse=True)
def isolated_recovery_path(tmp_path, monkeypatch):
    """Recovery timers and close handlers must never touch the user's app-data file."""
    monkeypatch.setattr(
        "video_data_sync.gui.main_window.MainWindow._recovery_path",
        property(lambda self: tmp_path / "recovery.vdsp.json"),
    )


@pytest.fixture
def synthetic_video(tmp_path):
    path = tmp_path / "synthetic.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 10, (64, 48))
    for index in range(12):
        writer.write(np.full((48, 64, 3), index * 15, dtype=np.uint8))
    writer.release()
    return path
