import os

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from qt_teardown import dispose_widget

from video_data_sync.gui.animated_tab import AnimatedTab


def test_animation_preview_displays_rotated_video_frames(synthetic_video):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    tab = AnimatedTab()
    try:
        tab.update_video(str(synthetic_video), 10, 0, 0.6, None, None, rotation=90)

        displayed = tab.video_label._source_pixmap
        assert isinstance(displayed, QPixmap)
        assert (displayed.width(), displayed.height()) == (48, 64)
        assert (tab._frame_w, tab._frame_h) == (48, 64)
    finally:
        tab._release_cap()
        dispose_widget(tab)
        app.processEvents()
