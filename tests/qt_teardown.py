"""Ownership-scoped Qt teardown for widgets that one test created.

Callers pass only the top-level widget that their own fixture or test body
constructed. Nothing here enumerates, closes, or deletes other process windows.
The widget is destroyed while the QApplication is still alive, instead of being
left to interpreter shutdown.

Order of operations:

1. Stop any real worker QThread the widget still owns (cancel, bounded join,
   deliver its queued ``finished``). A worker that does not stop is an error:
   destroying its owner while the thread runs is unsafe, so this raises.
2. Close the widget. A refused close is an error, because it skips the
   window's media release.
3. ``deleteLater`` plus an explicit DeferredDelete flush, then verify that the
   C++ object is really gone.
"""

from __future__ import annotations

import time

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QWidget

#: Worker attributes that MainWindow releases only on ``QThread.finished``.
WORKER_ATTRIBUTES = ("_export_worker", "_demo_worker")
WORKER_STOP_TIMEOUT_MS = 30_000
FINISHED_DELIVERY_TIMEOUT_S = 5.0

# Strong reference: the application must outlive every widget disposed here.
_application_refs: list[QCoreApplication] = []


def _require_application() -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("QApplication no longer exists; cannot dispose a test-owned widget")
    if not any(ref is app for ref in _application_refs):
        _application_refs.append(app)
    return app


def _stop_owned_worker(widget, attribute: str, app: QCoreApplication) -> None:
    worker = getattr(widget, attribute, None)
    if worker is None:
        return
    if (
        isinstance(worker, QThread)
        and shiboken6.isValid(worker)
        and (QThread.isRunning(worker) or QThread.isFinished(worker))
    ):
        cancel = getattr(worker, "request_cancel", None)
        if callable(cancel):
            cancel()
        # Class method: bypass any instance-level test stub of wait().
        if not QThread.wait(worker, WORKER_STOP_TIMEOUT_MS):
            raise RuntimeError(
                f"{type(widget).__name__}.{attribute} still running after "
                f"{WORKER_STOP_TIMEOUT_MS} ms; refusing to destroy its owner"
            )
        # Deliver the queued finished -> clear/deleteLater through the real path.
        deadline = time.monotonic() + FINISHED_DELIVERY_TIMEOUT_S
        while getattr(widget, attribute, None) is worker and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
    if getattr(widget, attribute, None) is worker:
        # Test fakes, never-started workers, or a finished thread whose release
        # was not delivered: none of these is a running thread, so drop the
        # reference rather than let closeEvent wait on a stub.
        setattr(widget, attribute, None)


def _require_clean(widget) -> None:
    if getattr(widget, "_dirty", False):
        # Avoid a modal unsaved-changes prompt that would block offscreen teardown.
        raise RuntimeError("clear the widget's dirty state before disposing it")


def dispose_widget(widget) -> None:
    """Stop owned workers, close, and destroy one test-owned top-level widget."""
    app = _require_application()
    if not shiboken6.isValid(widget):
        return
    _require_clean(widget)
    for attribute in WORKER_ATTRIBUTES:
        _stop_owned_worker(widget, attribute, app)
    # Pumped events (worker results, timers) could have re-dirtied the widget.
    _require_clean(widget)
    if not widget.close():
        raise RuntimeError(f"{type(widget).__name__} refused to close during test teardown")
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    if shiboken6.isValid(widget):
        raise RuntimeError(f"{type(widget).__name__} survived deleteLater during test teardown")


class OwnedWidgets:
    """Widgets that one test body constructed and registered explicitly.

    A module defines a function-scoped yield fixture that returns an instance;
    the test registers each top-level widget at its construction line
    (``tab = owned_widgets(AnimatedTab())``). Teardown disposes only those
    widgets, newest first. Nothing else in the process is touched.
    """

    def __init__(self, before_dispose=None) -> None:
        self._widgets: list = []
        self._before_dispose = before_dispose

    def __call__(self, widget):
        self._widgets.append(widget)
        return widget

    def dispose_all(self) -> None:
        errors: list[BaseException] = []
        while self._widgets:
            widget = self._widgets.pop()
            try:
                if self._before_dispose is not None and shiboken6.isValid(widget):
                    self._before_dispose(widget)
                dispose_widget(widget)
            except Exception as exc:  # dispose the rest, then report the first
                errors.append(exc)
        if errors:
            raise errors[0]


class OwnedPyplotFigures:
    """Pyplot figures that one test created and registered explicitly.

    The Qt manager window is captured at registration, while pyplot still
    tracks the figure. Teardown closes that one figure through pyplot (a no-op
    if the test already closed it), then destroys its window, which
    ``plt.close`` only hides.
    """

    def __init__(self) -> None:
        self._entries: list = []

    def __call__(self, figure):
        manager = getattr(figure.canvas, "manager", None)
        window = getattr(manager, "window", None)
        self._entries.append((figure, window))
        return figure

    def dispose_all(self) -> None:
        from matplotlib import pyplot as plt

        errors: list[BaseException] = []
        while self._entries:
            figure, window = self._entries.pop()
            try:
                plt.close(figure)
                # Only a PySide6 window can be destroyed through this helper.
                if isinstance(window, QWidget) and shiboken6.isValid(window):
                    dispose_widget(window)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
