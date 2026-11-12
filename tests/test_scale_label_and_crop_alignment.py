import numpy as np
import pandas as pd
import pytest
from qt_teardown import OwnedWidgets

from video_data_sync.video_processing import exporter
from video_data_sync.video_processing.exporter import export_video


@pytest.fixture
def owned_widgets():
    """Dispose only the top-level widgets this test registers.

    Request it before ``monkeypatch`` so instance patches are undone first.
    """
    owner = OwnedWidgets()
    yield owner
    owner.dispose_all()


def _data():
    t = np.arange(0, 1.21, 0.05)
    return pd.DataFrame({"Time (s)": t, "signal": np.sin(t)})


def test_export_scale_label_margins_use_configured_font_size_and_linewidth(
    synthetic_video, tmp_path, monkeypatch
):
    calls = []
    real_margins = exporter.scale_label_figure_margins

    def spy(*args, **kwargs):
        calls.append(args)
        return real_margins(*args, **kwargs)

    monkeypatch.setattr(exporter, "scale_label_figure_margins", spy)

    export_video(
        str(tmp_path / "out.mp4"),
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
        "#00aa00",
        None,
        None,
        scale_bar_font_size=30.0,
        scale_bar_linewidth=6.0,
    )

    assert calls, "export should compute scale-label margins"
    font_size, line_width = calls[0][2], calls[0][3]
    assert font_size == 30.0
    assert line_width == 6.0


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_animated_preview_margins_convert_logical_size_to_physical_pixels(
    owned_widgets, monkeypatch
):
    from video_data_sync.plotting import plot_widget
    from video_data_sync.plotting.plot_widget import AnimatedPlotWidget

    _app()
    widget = owned_widgets(AnimatedPlotWidget())
    widget.resize(400, 200)
    calls = []
    real_margins = plot_widget.scale_label_figure_margins

    def spy(*args, **kwargs):
        calls.append(args)
        return real_margins(*args, **kwargs)

    monkeypatch.setattr(plot_widget, "scale_label_figure_margins", spy)
    monkeypatch.setattr(widget, "devicePixelRatioF", lambda: 2.0)

    widget._update_scale_label_margins()

    width_inches, height_inches = calls[-1][0], calls[-1][1]
    assert width_inches == 400 * 2.0 / widget.figure.dpi
    assert height_inches == 200 * 2.0 / widget.figure.dpi


def test_crop_overlay_keeps_selection_aligned_after_resize(owned_widgets):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap

    from video_data_sync.video_processing.cropper import CropOverlay, CropRect

    _app()
    overlay = owned_widgets(CropOverlay())
    overlay.resize(600, 400)
    overlay.show()
    overlay.set_video_dimensions(200, 100)
    pixmap = QPixmap(400, 200)
    pixmap.fill()
    overlay.setPixmap(pixmap)
    overlay.set_crop(CropRect(50, 25, 150, 75))
    assert overlay._selection == QRect(200, 150, 200, 100)

    overlay.resize(800, 600)

    # The pixmap is re-centred, so the selection must follow it: offset (200, 200).
    assert overlay._selection == QRect(300, 250, 200, 100)
    resized_pixmap = QPixmap(800, 400)
    resized_pixmap.fill()
    overlay.setPixmap(resized_pixmap)
    assert overlay._selection == QRect(200, 200, 400, 200)
    crop = overlay.crop
    assert crop is not None
    assert crop.to_tuple() == (50, 25, 150, 75)
    overlay.close()
