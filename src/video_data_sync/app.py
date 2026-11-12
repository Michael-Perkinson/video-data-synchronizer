"""Application entry point for Video Data Synchronizer."""

import argparse
import sys

import cv2
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from . import __version__
from .gui.main_window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="Video Data Synchronizer")
    parser.parse_args()
    # Conservative default: one export at a time and one OpenCV compute thread.
    # Some platform codec backends may still manage a small internal thread of their own.
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)
    app = QApplication(sys.argv)
    app.setApplicationName("Video Data Synchronizer")
    app.setOrganizationName("Video Data Synchronizer")
    app.setApplicationVersion(__version__)
    # Let macOS use its native controls, menu behavior, and system appearance.
    if sys.platform != "darwin":
        app.setStyle(QStyleFactory.create("Fusion"))
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(245, 245, 245))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 233, 233))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.Text, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.Button, QColor(225, 225, 225))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())
