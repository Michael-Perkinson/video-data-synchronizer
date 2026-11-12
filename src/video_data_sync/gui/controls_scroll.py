"""Scrollable settings column shared by the Video and Data tabs.

On short screens the settings column is taller than the window can be. The
column scrolls vertically instead of forcing the window taller than the
display, and keyboard focus always scrolls the focused control into view.

Spare height is spread through the column with ``add_gap`` spacers. A gap
prefers its full extra height, shrinks to nothing when space is tight, and
never grows beyond it, so a very tall window puts the remaining space in the
column's bottom stretch instead of spreading the controls apart.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStyle,
    QWidget,
)

#: Base spacing between rows and blocks of a settings column (px).
ROW_SPACING = 8
#: Largest extra height between rows inside a group (row gap up to about 18 px).
ROW_GAP = 10
#: Largest extra height between top-level blocks (block gap up to about 24 px).
GROUP_GAP = 16


def add_gap(layout: QBoxLayout, extra: int) -> None:
    """Add up to ``extra`` px of spare height at this point of ``layout``.

    The spacer's preferred height is ``extra``, its minimum is 0 (Maximum policy:
    it may shrink but never grow). Spacers are empty layout items, so they do not
    add another layout spacing around themselves.
    """
    layout.addSpacerItem(
        QSpacerItem(0, extra, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Maximum)
    )


class ControlsScrollArea(QScrollArea):
    """Vertical-only scroll area around a fixed-width settings column."""

    def __init__(self, column: QWidget, column_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column.setFixedWidth(column_width)
        self.setWidget(column)
        # The column height is managed here (see _fit_column), not by
        # widgetResizable: for a column with word-wrapping labels QScrollArea
        # uses heightForWidth(), the PREFERRED height with every gap at its cap,
        # as the smallest column height, so the gaps could never shrink.
        self.setWidgetResizable(False)
        column.installEventFilter(self)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The column's own controls take focus; the area itself is not a tab stop.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scrollbar = self.verticalScrollBar()
        # Some platform styles (notably macOS) report a scroll-bar extent that
        # is a few pixels smaller than the real scroll bar. Reserving only the
        # metric leaves the viewport narrower than the fixed-width column and
        # creates a hidden horizontal scroll range.
        scrollbar_width = max(
            self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, scrollbar),
            scrollbar.sizeHint().width(),
        )
        # Reserve room for the scroll bar so it never covers the controls.
        self.setFixedWidth(column_width + scrollbar_width)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            # QScrollArea scrolls only for Tab moves inside the column. This also
            # covers focus that enters from outside, e.g. Tab wrapping from the
            # tab bar to the first control while the column is scrolled down.
            app.focusChanged.connect(self._reveal_focus)

    def _minimum_column_height(self, column: QWidget) -> int:
        """Smallest height at which the column's layout can show every control."""
        layout = column.layout()
        if layout is None:
            return column.minimumSizeHint().height()
        if layout.hasHeightForWidth():
            # Word-wrapping items count with their wrapped height; gaps count as 0.
            minimum = layout.minimumHeightForWidth(column.width())
        else:
            minimum = layout.totalMinimumSize().height()
        return max(minimum, column.minimumHeight())

    def _fit_column(self) -> None:
        """Make the column as tall as the viewport, or its minimum height if larger.

        Between the minimum and the preferred height the gaps shrink; the
        column scrolls only when even its minimum height does not fit.
        """
        column = self.widget()
        if column is None:
            return
        height = max(self.viewport().height(), self._minimum_column_height(column))
        if column.height() != height:
            column.resize(column.width(), height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_column()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Content changes (a label shown, text re-wrapped) re-request the layout.
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self._fit_column()
        return super().eventFilter(watched, event)

    @Slot(QWidget, QWidget)
    def _reveal_focus(self, _old: QWidget | None, new: QWidget | None) -> None:
        column = self.widget()
        if new is not None and column is not None and column.isAncestorOf(new):
            self.ensureWidgetVisible(new)
