"""
Main window — tab shell and signal wiring only.

No business logic lives here. It wires signals from each tab to the
appropriate handler and passes state between tabs.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import (
    QCoreApplication,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from video_data_sync import __version__
from video_data_sync.gui.video_tab import VideoTab

if TYPE_CHECKING:
    from video_data_sync.gui.animated_tab import AnimatedTab
    from video_data_sync.gui.data_tab import DataTab


def _with_mp4_extension(path: str) -> str:
    """Return ``path`` with a ``.mp4`` extension; an existing one in any case is kept."""
    return path if path.lower().endswith(".mp4") else path + ".mp4"


class _ExportWorker(QThread):
    """Runs export_video off the main thread and reports frame progress."""

    progress = Signal(int, int)  # (current_frame, total_frames)
    succeeded = Signal()
    cancelled = Signal()
    error = Signal(str)

    def __init__(self, kwargs: dict | list[dict]) -> None:
        super().__init__()
        raw_jobs = [kwargs] if isinstance(kwargs, dict) else kwargs
        self._jobs = [dict(job) for job in raw_jobs]
        self._job_totals = [int(job.pop("_estimated_frames", 1)) for job in self._jobs]
        self._cancel_requested = threading.Event()

    def run(self) -> None:
        try:
            from video_data_sync.video_processing.exporter import ExportCancelled, export_video
        except Exception as exc:
            self.error.emit(str(exc))
            return

        try:
            completed = 0
            overall_total = sum(self._job_totals)
            for job, estimated in zip(self._jobs, self._job_totals, strict=True):
                if self._cancel_requested.is_set():
                    raise ExportCancelled("Export cancelled.")

                def report(current: int, _total: int, offset: int = completed) -> None:
                    self.progress.emit(offset + current, overall_total)

                export_video(
                    **job,
                    progress_callback=report,
                    cancel_callback=self._cancel_requested.is_set,
                )
                completed += estimated
            self.succeeded.emit()
        except ExportCancelled:
            self.cancelled.emit()
        except Exception as e:
            self.error.emit(str(e))

    def request_cancel(self) -> None:
        self._cancel_requested.set()


class _DemoWorker(QThread):
    """Create or reuse the small synthetic demo without blocking the UI."""

    ready = Signal(str, str)
    error = Signal(str)

    def __init__(self, output_dir: Path) -> None:
        super().__init__()
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            from video_data_sync.demo import generate_demo

            video_path = self._output_dir / "synthetic_video.mp4"
            csv_path = self._output_dir / "synthetic_timeseries.csv"
            if not video_path.is_file() or not csv_path.is_file():
                video_path, csv_path = generate_demo(self._output_dir)
            self.ready.emit(str(video_path), str(csv_path))
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        if not QCoreApplication.organizationName():
            QCoreApplication.setOrganizationName("VideoDataSync")
        if not QCoreApplication.applicationName():
            QCoreApplication.setApplicationName("Video Data Synchronizer")
        QCoreApplication.setApplicationVersion(__version__)
        self.setWindowTitle("Video Data Synchronizer — Untitled[*]")
        self.resize(1300, 800)
        self._settings = QSettings()
        self._project_path: str | None = None
        self._dirty = False
        self._suspend_dirty = False
        self._demo_worker: _DemoWorker | None = None
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.setInterval(750)
        self._recovery_timer.timeout.connect(self._write_recovery_snapshot)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { font-weight: bold; font-size: 13px; padding: 6px 18px; }"
        )
        self.setCentralWidget(self.tabs)

        self.video_tab = VideoTab()
        self.data_tab: DataTab | None = None
        self.animated_tab: AnimatedTab | None = None
        self._data_placeholder = self._placeholder("Preparing plot tools...")
        self._animated_placeholder = self._placeholder("Preparing animation tools...")
        self._constructing_tab = False
        self._video_state: tuple[Any, ...] | None = None
        self._preload_done = False
        self._preload_error: Exception | None = None
        self._preload_thread: threading.Thread | None = None
        self._data_tab_cls: type[DataTab] | None = None
        self._animated_tab_cls: type[AnimatedTab] | None = None

        self.tabs.addTab(self.video_tab, "Video Cropping")
        self.tabs.addTab(self._data_placeholder, "Full Data Plot")
        self.tabs.addTab(self._animated_placeholder, "Animated Plot")

        # Animated tab is locked until both video and data are ready
        self._video_ready = False
        self._data_ready = False
        self.tabs.setTabEnabled(2, False)

        self._export_worker: _ExportWorker | None = None

        self._wire_signals()
        self._build_file_menu()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._update_tab_status()
        QTimer.singleShot(0, self._start_preload_tabs)
        QTimer.singleShot(250, self._offer_recovery_if_available)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    @staticmethod
    def _placeholder(text: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #666;")
        layout.addWidget(label)
        return widget

    def _wire_signals(self) -> None:
        self.video_tab.cropped_confirmed.connect(self._on_video_cropped)
        self.video_tab.project_changed.connect(lambda: self._set_dirty(True))

    def _build_file_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")
        for label, shortcut, handler in (
            ("&New Project", "Ctrl+N", self._new_project),
            ("&Open Project…", "Ctrl+O", self._open_project),
            ("&Save Project", "Ctrl+S", self._save_project),
            ("Save Project &As…", "Ctrl+Shift+S", self._save_project_as),
        ):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            menu.addAction(action)
        self._recent_menu = QMenu("Open &Recent", self)
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        menu.addMenu(self._recent_menu)
        history_action = QAction("Export &History…", self)
        history_action.triggered.connect(self._show_export_history)
        menu.addAction(history_action)
        preset_menu = self.menuBar().addMenu("&Presets")
        save_preset_action = QAction("&Save Appearance Preset…", self)
        save_preset_action.triggered.connect(self._save_appearance_preset)
        preset_menu.addAction(save_preset_action)
        load_preset_action = QAction("&Load Appearance Preset…", self)
        load_preset_action.triggered.connect(self._load_appearance_preset)
        preset_menu.addAction(load_preset_action)
        help_menu = self.menuBar().addMenu("&Help")
        demo = QAction("Open &Demo Project", self)
        demo.triggered.connect(self._open_demo_project)
        help_menu.addAction(demo)
        help_menu.addSeparator()
        about = QAction("&About", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Video Data Synchronizer",
            f"Video Data Synchronizer {__version__}\n\n"
            "Align a constant-frame-rate video with one or two numeric time-series "
            "signals using "
            "a single fixed time offset. Processing stays on this computer.",
        )

    def _show_export_history(self) -> None:
        from video_data_sync.gui.history_dialog import ExportHistoryDialog

        dialog = ExportHistoryDialog(self)

        def restore(record):
            if self._restore_export_settings(record):
                dialog.accept()

        dialog.restore_requested.connect(restore)
        dialog.exec()

    def _restore_export_settings(self, record: dict) -> bool:
        if not self._confirm_discard():
            return False
        project = deepcopy(record.get("context", {}).get("project"))
        if not project:
            return False
        changed = []
        for source in record.get("sources", {}).values():
            path = Path(source["path"])
            if path.is_file():
                stat = path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (
                    source.get("size_bytes"),
                    source.get("modified_ns"),
                ):
                    changed.append(path.name)
        if changed:
            answer = QMessageBox.question(
                self,
                "Source files have changed",
                "These files have changed since the export: "
                + ", ".join(changed)
                + "\nRestore settings using the current files?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if self._load_project_path(record["record_path"], remember=False, document=project):
            self._project_path = None
            self._set_dirty(True)
            self.statusBar().showMessage("Export settings restored. Save as a new project.", 7000)
            return True
        return False

    def _open_demo_project(self) -> None:
        if self._demo_worker is not None:
            self.statusBar().showMessage("The demo is already being prepared.", 4000)
            return
        if not self._confirm_discard():
            return
        output_dir = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
            / "synthetic-demo-v2"
        )
        progress = QProgressDialog("Preparing synthetic demo...", "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowTitle("Open Demo Project")
        progress.setMinimumDuration(0)
        worker = _DemoWorker(output_dir)
        self._demo_worker = worker

        def ready(video_path: str, data_path: str) -> None:
            progress.close()
            self._load_demo_files(video_path, data_path)

        def failed(message: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Demo could not be created", message)

        worker.ready.connect(ready)
        worker.error.connect(failed)
        # Sole release point: ready/error arrive before the thread has finished,
        # so the reference must survive until QThread.finished. Otherwise a close
        # in that window would not wait and could destroy a running QThread.
        worker.finished.connect(lambda: self._clear_demo_worker(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _clear_demo_worker(self, worker: _DemoWorker) -> None:
        """Clear only the demo worker that owns the current preparation."""
        if self._demo_worker is worker:
            self._demo_worker = None

    def _load_demo_files(self, video_path: str, data_path: str) -> bool:
        if self._preload_thread is not None and not self._preload_done:
            QTimer.singleShot(50, lambda: self._load_demo_files(video_path, data_path))
            return True
        self._reset_project_state()
        self._clear_recovery()
        self._ensure_data_tab()
        self._ensure_animated_tab()
        if self.data_tab is None or self.animated_tab is None:
            QMessageBox.critical(self, "Demo unavailable", "Plot tools could not be initialized.")
            return False
        if not self.video_tab.load_project_video(video_path, 0.0, 6.0, None, None):
            return False
        if not self.data_tab.load_file(data_path):
            return False
        self.data_tab.apply_project_style(
            {
                "overlay": True,
                "transform": "raw",
                "color": "#1f77b4",
                "color2": "#d62728",
                "linewidth": 1.5,
            }
        )
        self.data_tab.select_project_columns("Synthetic response (AU)", "Synthetic control (AU)")
        self._push_data_to_animated_tab()
        self.tabs.setCurrentIndex(2)
        self._set_dirty(True)
        QMessageBox.information(
            self,
            "Synthetic demo ready",
            "The demo contains flashes at 2.0 s and 4.5 s that align with peaks in the "
            "response signal.\n\nReview the crop, confirm the detected columns, preview the start, "
            "middle, and end, then try an export.",
        )
        return True

    def _set_dirty(self, dirty: bool) -> None:
        if dirty and self._suspend_dirty:
            return
        self._dirty = dirty
        name = os.path.basename(self._project_path) if self._project_path else "Untitled"
        self.setWindowTitle(f"Video Data Synchronizer — {name}[*]")
        self.setWindowModified(dirty)
        if dirty:
            self._recovery_timer.start()

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current project?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Save:
            return self._save_project()
        return result == QMessageBox.StandardButton.Discard

    @property
    def _recovery_path(self) -> Path:
        root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        return root / "recovery.vdsp.json"

    def _write_recovery_snapshot(self) -> None:
        if not self._dirty:
            return
        path = self._recovery_path
        temporary: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            document = self._project_document(portable_paths=False)
            document["recovery"] = {
                "saved_at": datetime.now(UTC).isoformat(),
                "project_path": self._project_path,
            }
            fd, temporary = tempfile.mkstemp(prefix=".recovery-", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except (OSError, TypeError, ValueError):
            # Recovery is best-effort and must never interrupt normal work.
            return
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _clear_recovery(self) -> None:
        self._recovery_timer.stop()
        try:
            self._recovery_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _offer_recovery_if_available(self) -> None:
        path = self._recovery_path
        if not path.is_file() or self._dirty:
            return
        try:
            with path.open(encoding="utf-8") as handle:
                document = json.load(handle)
            metadata = document.get("recovery", {})
            project_path = metadata.get("project_path")
            saved_at = str(metadata.get("saved_at", "an unknown time"))
        except (OSError, TypeError, ValueError):
            self._clear_recovery()
            return
        name = os.path.basename(project_path) if project_path else "Untitled project"
        answer = QMessageBox.question(
            self,
            "Recover unsaved work?",
            f"Recover unsaved changes for {name}?\n\nRecovery snapshot: {saved_at}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._clear_recovery()
            return
        if self._load_project_path(str(path), remember=False, clear_recovery=False):
            self._project_path = str(project_path) if project_path else None
            self._clear_recovery()
            self._set_dirty(True)

    def _recent_projects(self) -> list[str]:
        stored = self._settings.value("projects/recent", [])
        values = stored if isinstance(stored, list) else ([stored] if stored else [])
        return [str(path) for path in values]

    def _remember_recent_project(self, path: str) -> None:
        absolute = os.path.abspath(path)
        paths = [item for item in self._recent_projects() if item != absolute]
        self._settings.setValue("projects/recent", [absolute, *paths][:8])

    def _forget_recent_project(self, path: str) -> None:
        self._settings.setValue(
            "projects/recent", [item for item in self._recent_projects() if item != path]
        )

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = self._recent_projects()
        if not paths:
            empty = self._recent_menu.addAction("No recent projects")
            empty.setEnabled(False)
            return
        for path in paths:
            label = f"{os.path.basename(path)} — {os.path.dirname(path)}"
            action = self._recent_menu.addAction(label)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, selected=path: self._open_recent(selected)
            )
        self._recent_menu.addSeparator()
        clear = self._recent_menu.addAction("Clear Recent Projects")
        clear.triggered.connect(lambda: self._settings.remove("projects/recent"))

    def _open_recent(self, path: str) -> None:
        if not os.path.isfile(path):
            self._forget_recent_project(path)
            QMessageBox.warning(
                self, "Project not found", f"This project no longer exists:\n{path}"
            )
            return
        if self._confirm_discard():
            self._load_project_path(path)

    def _appearance_preset_document(self) -> dict[str, Any]:
        from video_data_sync.settings.presets import default_preset

        document = default_preset()
        data = self.data_tab
        if data is not None:
            first = data.column_combo.currentText() or str(
                self._settings.value("data/preferredColumn1", "")
            )
            second = data.column_combo2.currentText() or str(
                self._settings.value("data/preferredColumn2", "")
            )
            channels = list(dict.fromkeys(name for name in (first, second) if name))
            colors = data.column_colors
            if first:
                colors[first] = data.trace_color
            if second and second != first:
                colors[second] = data.trace_color2
            document["traces"].update(
                colors_by_column=colors,
                legend_labels=data.legend_labels,
                preferred_channels=channels[:2],
                line_width=data.linewidth,
                transform=data.trace_transform,
                overlay=data.chk_overlay.isChecked(),
            )
        anim = self.animated_tab
        if anim is not None:
            sizes = anim.display_container.sizes()
            if len(sizes) != 2 or min(sizes) <= 0:
                sizes = [3, 2]
            document["composition"].update(
                layout="stacked" if anim.layout_combo.currentIndex() else "side_by_side",
                panel_proportions=[int(value) for value in sizes],
                advanced=anim.composition_settings,
            )
            stored_speeds = self._settings.value("export/speeds", [])
            speed_values = stored_speeds if isinstance(stored_speeds, list) else [stored_speeds]
            speeds: list[float] = []
            for value in speed_values:
                try:
                    speed = float(str(value).lower().rstrip("x"))
                except ValueError:
                    continue
                if 0.05 <= speed <= 16 and speed not in speeds:
                    speeds.append(speed)
            document["export"] = {
                "speeds": speeds or [anim.speed_factor],
                "deflicker": anim.deflicker_enabled,
            }
        document["video_colour"] = {
            "brightness": self.video_tab._brightness(),
            "contrast": self.video_tab._contrast(),
            "gamma": self.video_tab._gamma(),
            "r_gain": self.video_tab._r_gain(),
            "g_gain": self.video_tab._g_gain(),
            "b_gain": self.video_tab._b_gain(),
        }
        return document

    def _save_appearance_preset(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from video_data_sync.settings.presets import PresetValidationError, save_preset

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Appearance Preset",
            str(self._settings.value("folders/preset", "appearance.vdsp-preset.json")),
            "Appearance presets (*.vdsp-preset.json *.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".vdsp-preset.json"
        try:
            save_preset(path, self._appearance_preset_document())
        except (OSError, PresetValidationError) as exc:
            QMessageBox.critical(self, "Preset could not be saved", str(exc))
            return
        self._settings.setValue("folders/preset", os.path.dirname(path))
        self.statusBar().showMessage(f"Appearance preset saved: {path}", 5000)

    def _load_appearance_preset(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from video_data_sync.settings.presets import PresetValidationError, load_preset

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Appearance Preset",
            str(self._settings.value("folders/preset", "")),
            "Appearance presets (*.vdsp-preset.json *.json)",
        )
        if not path:
            return
        try:
            document = load_preset(path)
            self._apply_appearance_preset(document)
        except (OSError, PresetValidationError) as exc:
            QMessageBox.critical(self, "Preset could not be loaded", str(exc))
            return
        self._settings.setValue("folders/preset", os.path.dirname(path))
        self.statusBar().showMessage(f"Appearance preset loaded: {path}", 5000)

    def _apply_appearance_preset(self, document: dict[str, Any]) -> None:
        traces = document["traces"]
        channels = traces["preferred_channels"]
        colors = traces["colors_by_column"]
        if channels:
            self._settings.setValue("data/preferredColumn1", channels[0])
            self._settings.setValue("data/traceColor", colors.get(channels[0], "#000000"))
        if len(channels) > 1:
            self._settings.setValue("data/preferredColumn2", channels[1])
            self._settings.setValue("data/traceColor2", colors.get(channels[1], "#d62728"))
        self._settings.setValue("data/lineWidth", traces["line_width"])
        self._settings.setValue("data/transform", traces["transform"])
        self._settings.setValue("data/overlay", traces["overlay"])
        if self.data_tab is not None:
            self.data_tab.apply_appearance_settings(traces)

        composition = document["composition"]
        export = document["export"]
        orientation = 1 if composition["layout"] == "stacked" else 0
        self._settings.setValue("composition/layout", orientation)
        self._settings.setValue("composition/panelSizes", composition["panel_proportions"])
        self._settings.setValue("composition/advanced", json.dumps(composition["advanced"]))
        self._settings.setValue("composition/speed", f"{export['speeds'][0]:g}x")
        self._settings.setValue("composition/deflicker", export["deflicker"])
        self._settings.setValue("export/speeds", [f"{speed:g}" for speed in export["speeds"]])
        if self.animated_tab is not None:
            self.animated_tab.apply_project_export_settings(
                {
                    "orientation": orientation,
                    "splitter_sizes": composition["panel_proportions"],
                    "speed": f"{export['speeds'][0]:g}x",
                    "deflicker": export["deflicker"],
                    "advanced": composition["advanced"],
                }
            )
        self.video_tab.apply_colour_settings(document["video_colour"], persist=True)
        if self._video_state is not None:
            self.video_tab._on_confirm()
        self._set_dirty(True)

    def _new_project(self) -> None:
        if not self._confirm_discard():
            return
        self._reset_project_state()
        self._clear_recovery()

    def _reset_project_state(self) -> None:
        self._project_path = None
        self._video_state = None
        self._video_ready = self._data_ready = False
        self.video_tab.reset_state()
        if self.data_tab is not None:
            self.data_tab.reset_to_preferences()
        if self.animated_tab is not None:
            cast(Any, self.animated_tab).reset_state()
        self._update_animated_tab_state()
        self._set_dirty(False)

    @staticmethod
    def _portable_path(source: str, project_path: str | None) -> str:
        if not project_path:
            return source
        try:
            return os.path.relpath(source, os.path.dirname(os.path.abspath(project_path)))
        except ValueError:
            return source

    def _project_document(
        self, project_path: str | None = None, *, portable_paths: bool = True
    ) -> dict[str, Any]:
        project_path = project_path or self._project_path
        video: dict[str, Any] = {}
        video_state = self._video_state
        cap = self.video_tab._cap
        if video_state is not None or cap is not None:
            if cap is not None:
                path = cap.path
                start, end = 0.0, cap.duration_s
                rotation = self.video_tab.rotation
            elif video_state is not None:
                path, _fps, start, end, crop, _lut = video_state[:6]
                rotation = video_state[6] if len(video_state) > 6 else 0
            else:
                raise RuntimeError("Video state became unavailable while saving the project.")
            try:
                start, end = (
                    float(self.video_tab.start_input.text()),
                    float(self.video_tab.end_input.text()),
                )
            except ValueError:
                pass
            crop = self.video_tab.video_display.crop
            video = {
                "path": self._portable_path(path, project_path) if portable_paths else path,
                "start_s": start,
                "end_s": end,
                "rotation": rotation,
                "crop": list(crop.to_tuple()) if crop and crop.is_valid() else None,
                "colour": {
                    "brightness": self.video_tab._brightness(),
                    "contrast": self.video_tab._contrast(),
                    "gamma": self.video_tab._gamma(),
                    "r_gain": self.video_tab._r_gain(),
                    "g_gain": self.video_tab._g_gain(),
                    "b_gain": self.video_tab._b_gain(),
                },
            }
        data: dict[str, Any] = {}
        if self.data_tab is not None and self.data_tab.data_path:
            data = {
                "path": (
                    self._portable_path(self.data_tab.data_path, project_path)
                    if portable_paths
                    else self.data_tab.data_path
                ),
                "time_unit": self.data_tab.time_unit_combo.currentText(),
                "time_column": self.data_tab.time_column_combo.currentText(),
                "column": self.data_tab.selected_column,
                "column2": self.data_tab.selected_column2,
                "overlay": self.data_tab.overlay_enabled,
                "offset_s": self.data_tab.offset_s,
                "color": self.data_tab.trace_color,
                "color2": self.data_tab.trace_color2,
                "legend_labels": self.data_tab.legend_labels,
                "linewidth": self.data_tab.linewidth,
                "y_limits": list(self.data_tab.y_limits()),
                "y_mode": "fixed" if self.data_tab.radio_fixed.isChecked() else "auto",
                "transform": self.data_tab.trace_transform,
                "zscore": self.data_tab.zscore_recipe,
            }
        composition: dict[str, Any] = {}
        if self.animated_tab is not None:
            composition = {
                "splitter_sizes": self.animated_tab.display_container.sizes(),
                "orientation": self.animated_tab.layout_combo.currentIndex(),
                "speed": self.animated_tab.speed_combo.currentText(),
                "deflicker": self.animated_tab.deflicker_enabled,
                "advanced": self.animated_tab.composition_settings,
            }
        return {"schema_version": 1, "video": video, "data": data, "composition": composition}

    def _write_project(self, path: str) -> bool:
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(
                prefix=".vdsp-", suffix=".tmp", dir=os.path.dirname(os.path.abspath(path))
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._project_document(path), handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return True

    def _save_project(self) -> bool:
        if not self._project_path:
            return self._save_project_as()
        if not self._write_project(self._project_path):
            return False
        self._remember_recent_project(self._project_path)
        self._clear_recovery()
        self._set_dirty(False)
        return True

    def _save_project_as(self) -> bool:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            str(self._settings.value("folders/project", "")),
            "Sync projects (*.vdsp.json)",
        )
        if not path:
            return False
        if not path.endswith(".vdsp.json"):
            path += ".vdsp.json"
        if not self._write_project(path):
            return False
        self._project_path = path
        self._settings.setValue("folders/project", os.path.dirname(path))
        self._remember_recent_project(path)
        self._clear_recovery()
        self._suspend_dirty = False
        self._set_dirty(False)
        return True

    def _resolve_source(self, stored: str, project_path: str, label: str) -> str:
        from PySide6.QtWidgets import QFileDialog

        candidate = (
            stored if os.path.isabs(stored) else os.path.join(os.path.dirname(project_path), stored)
        )
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        replacement, _ = QFileDialog.getOpenFileName(
            self, f"Relink missing {label}", os.path.dirname(project_path), "All files (*)"
        )
        if not replacement:
            raise FileNotFoundError(f"Missing {label} source: {candidate}")
        return os.path.abspath(replacement)

    def _open_project(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            str(self._settings.value("folders/project", "")),
            "Sync projects (*.vdsp.json *.json)",
        )
        if path:
            self._load_project_path(path)

    def _load_project_path(
        self,
        path: str,
        *,
        remember: bool = True,
        clear_recovery: bool = True,
        document: dict | None = None,
    ) -> bool:
        staged_video: VideoTab | None = None
        staged_data: Any = None
        staged_animated: Any = None
        try:
            if document is None:
                with open(path, encoding="utf-8") as handle:
                    doc = json.load(handle)
            else:
                doc = deepcopy(document)
            if not isinstance(doc, dict) or doc.get("schema_version") != 1:
                raise ValueError("Unsupported project schema version.")
            for key in ("video", "data", "composition"):
                if not isinstance(doc.get(key, {}), dict):
                    raise ValueError(f"Project section '{key}' must be an object.")
            video, data = doc.get("video", {}), doc.get("data", {})
            if video:
                video["path"] = self._resolve_source(str(video["path"]), path, "video")
                from video_data_sync.video_processing.capture import VideoCapture

                probe = VideoCapture(video["path"])
                try:
                    start, end = float(video["start_s"]), float(video["end_s"])
                    if start < 0 or end <= start or end > probe.duration_s + 0.5 / probe.fps:
                        raise ValueError("Saved clip range is outside the video duration.")
                finally:
                    probe.release()
            if data:
                data["path"] = self._resolve_source(str(data["path"]), path, "data")
                from video_data_sync.data.loader import read_file

                read_file(data["path"])
            from video_data_sync.gui.animated_tab import AnimatedTab
            from video_data_sync.gui.data_tab import DataTab

            staged_video = VideoTab()
            staged_data = DataTab()
            staged_animated = AnimatedTab()
            staged_video_state: list[tuple[Any, ...]] = []
            staged_video.cropped_confirmed.connect(
                lambda *args: staged_video_state.append(tuple(args))
            )
            if data:
                if not staged_data.load_file(
                    data["path"],
                    time_column=data.get("time_column") or None,
                    time_unit=data.get("time_unit", "Auto-detect"),
                    remember_folder=False,
                ):
                    raise ValueError("The data source could not be loaded.")
                project_style = dict(data)
                project_style.setdefault("legend_labels", {})
                staged_data.apply_project_style(project_style)
                if data.get("zscore"):
                    staged_data.recreate_zscore(data["zscore"])
                staged_data.select_project_columns(
                    str(data.get("column", "")), str(data.get("column2", ""))
                )
                staged_data.offset_input.setText(str(data.get("offset_s", 0)))
            if video and not staged_video.load_project_video(
                video["path"],
                video["start_s"],
                video["end_s"],
                video.get("crop"),
                video.get("colour"),
                rotation=video.get("rotation", 0),
            ):
                raise ValueError("The video source could not be loaded.")
            comp = doc.get("composition", {})
            staged_animated.apply_project_export_settings(comp)
            if staged_video_state:
                staged_animated.update_video(*staged_video_state[-1])
            if data and not staged_data.dataframe.empty and staged_data.time_col:
                y_min, y_max = staged_data.y_limits()
                staged_animated.update_data(
                    staged_data.dataframe,
                    staged_data.time_col,
                    staged_data.selected_column,
                    staged_data.trace_color,
                    y_min,
                    y_max,
                    staged_data.linewidth,
                    staged_data.offset_s,
                    data_col2=staged_data.selected_column2,
                    color2=staged_data.trace_color2,
                    trace_transform=staged_data.trace_transform,
                    legend_labels=staged_data.legend_labels,
                )
        except Exception as exc:
            self._discard_staged_tabs(staged_video, staged_data, staged_animated)
            QMessageBox.critical(self, "Open failed", str(exc))
            return False
        finally:
            self._suspend_dirty = False
        assert staged_video is not None and staged_data is not None and staged_animated is not None
        if staged_data.data_path is not None:
            staged_data.persist_folder_preference()
        old_video, old_data, old_animated = self.video_tab, self.data_tab, self.animated_tab
        self._suspend_dirty = True
        self.video_tab, self.data_tab, self.animated_tab = (
            staged_video,
            staged_data,
            staged_animated,
        )
        self._replace_tab(0, staged_video, "Video Cropping")
        self._replace_tab(1, staged_data, "Full Data Plot")
        self._replace_tab(2, staged_animated, "Animated Plot")
        self._wire_signals()
        staged_data.data_loaded.connect(self._on_data_loaded)
        staged_data.project_changed.connect(lambda: self._set_dirty(True))
        staged_animated.export_requested.connect(self._on_export_requested)
        staged_animated.batch_export_requested.connect(self._on_batch_export_requested)
        if hasattr(staged_animated, "offset_changed"):
            cast(Any, staged_animated).offset_changed.connect(self._on_animated_offset_changed)
        staged_animated.composition_changed.connect(lambda: self._set_dirty(True))
        staged_animated.speed_combo.currentTextChanged.connect(lambda: self._set_dirty(True))
        staged_animated.chk_deflicker.toggled.connect(lambda: self._set_dirty(True))
        self._video_state = staged_video_state[-1] if staged_video_state else None
        self._video_ready, self._data_ready = bool(video), bool(data)
        self._update_animated_tab_state()
        # Replaced tabs stay parented to the tab stack, and a child widget's
        # close() never delivers a closeEvent, so release their native video
        # handles and playback timer explicitly.
        if old_video is not None:
            old_video.release_media()
        if old_animated is not None:
            old_animated._release_cap()
        for old in (old_video, old_data, old_animated):
            if old is not None:
                old.close()
        self._project_path = path
        self._settings.setValue("folders/project", os.path.dirname(path))
        if remember:
            self._remember_recent_project(path)
        if clear_recovery:
            self._clear_recovery()
        self._set_dirty(False)
        self._suspend_dirty = False
        return True

    @staticmethod
    def _discard_staged_tabs(staged_video, staged_data, staged_animated) -> None:
        """Free tabs staged by a rejected project load.

        Staged tabs are never parented or wired into this window, so the
        current project is untouched; only their own native video handles,
        playback timer and Qt objects are released.
        """
        if staged_video is not None:
            staged_video.release_media()
        if staged_animated is not None:
            staged_animated._release_cap()
        for staged in (staged_video, staged_data, staged_animated):
            if staged is not None:
                staged.deleteLater()

    def _replace_tab(self, index: int, widget: QWidget, label: str) -> None:
        enabled = self.tabs.isTabEnabled(index)
        current = self.tabs.currentIndex()
        self._constructing_tab = True
        try:
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, widget, label)
            self.tabs.setTabEnabled(index, enabled)
            if current == index:
                self.tabs.setCurrentIndex(index)
        finally:
            self._constructing_tab = False

    def _ensure_data_tab(self) -> None:
        if self.data_tab is not None:
            return
        if self._preload_thread is not None and not self._preload_done:
            return
        data_tab_cls = self._data_tab_cls
        if data_tab_cls is None:
            from video_data_sync.gui.data_tab import DataTab

            data_tab_cls = DataTab
        self.data_tab = data_tab_cls()
        self.data_tab.data_loaded.connect(self._on_data_loaded)
        self.data_tab.project_changed.connect(lambda: self._set_dirty(True))
        self._replace_tab(1, self.data_tab, "Full Data Plot")
        self._update_tab_status()

    def _ensure_animated_tab(self) -> None:
        if self.animated_tab is not None:
            return
        if self._preload_thread is not None and not self._preload_done:
            return
        animated_tab_cls = self._animated_tab_cls
        if animated_tab_cls is None:
            from video_data_sync.gui.animated_tab import AnimatedTab

            animated_tab_cls = AnimatedTab
        self.animated_tab = animated_tab_cls()
        self.animated_tab.export_requested.connect(self._on_export_requested)
        self.animated_tab.batch_export_requested.connect(self._on_batch_export_requested)
        if hasattr(self.animated_tab, "offset_changed"):
            cast(Any, self.animated_tab).offset_changed.connect(self._on_animated_offset_changed)
        if hasattr(self.animated_tab, "composition_changed"):
            self.animated_tab.composition_changed.connect(lambda: self._set_dirty(True))
        self.animated_tab.speed_combo.currentTextChanged.connect(lambda: self._set_dirty(True))
        self.animated_tab.chk_deflicker.toggled.connect(lambda: self._set_dirty(True))
        self._replace_tab(2, self.animated_tab, "Animated Plot")
        if self._video_state is not None:
            self.animated_tab.update_video(*self._video_state)
        self._push_data_to_animated_tab()
        self._update_tab_status()

    def _on_tab_changed(self, index: int) -> None:
        if self._constructing_tab:
            return
        if index == 1:
            self._ensure_data_tab()
        elif index == 2:
            self._ensure_animated_tab()

    def _start_preload_tabs(self) -> None:
        """Warm heavy imports off the UI thread, then build tabs on the UI thread."""
        if self._preload_thread is not None:
            return

        def _load() -> None:
            try:
                from video_data_sync.gui.animated_tab import AnimatedTab
                from video_data_sync.gui.data_tab import DataTab

                self._data_tab_cls = DataTab
                self._animated_tab_cls = AnimatedTab
            except Exception as e:
                self._preload_error = e
            finally:
                self._preload_done = True

        self._preload_thread = threading.Thread(target=_load, daemon=True)
        self._preload_thread.start()
        QTimer.singleShot(50, self._finish_preload_tabs)

    def _finish_preload_tabs(self) -> None:
        if not self._preload_done:
            QTimer.singleShot(50, self._finish_preload_tabs)
            return
        if self._preload_error is not None:
            return
        self._ensure_data_tab()
        QTimer.singleShot(0, self._ensure_animated_tab)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_video_cropped(
        self, path, fps, start_s, end_s, crop_rect, colour_lut, rotation=0
    ) -> None:
        """Relay video parameters to the animated tab."""
        self._video_state = (path, fps, start_s, end_s, crop_rect, colour_lut, rotation)
        if self.animated_tab is not None:
            self.animated_tab.update_video(
                path, fps, start_s, end_s, crop_rect, colour_lut, rotation
            )
        self._video_ready = True
        self._push_data_to_animated_tab()
        self._update_animated_tab_state()
        self._set_dirty(True)

    def _on_data_loaded(self, df, time_col: str) -> None:
        """Relay updated data to the animated tab."""
        self._data_ready = True
        self._push_data_to_animated_tab()
        self._update_animated_tab_state()
        self._set_dirty(True)

    def _on_animated_offset_changed(self, offset_s: float) -> None:
        if self.data_tab is not None:
            self.data_tab.offset_input.setText(f"{offset_s:.3f}")

    def _update_animated_tab_state(self) -> None:
        """Enable the Animated tab only when both video and data are ready."""
        ready = self._video_ready and self._data_ready
        self.tabs.setTabEnabled(2, ready)
        self._update_tab_status()

    def _update_tab_status(self) -> None:
        """Explain each workflow step without relying on tab text colour."""
        self.tabs.setTabToolTip(
            0,
            "Load a video, choose the clip and appearance, then click Apply Video Settings.",
        )
        self.tabs.setTabToolTip(
            1,
            "Load a CSV or XLSX file, check its time and signal columns, and set Sync Offset.",
        )
        if self._video_ready and self._data_ready:
            tooltip = "Preview synchronized playback and export the composed video."
        else:
            missing = []
            if not self._video_ready:
                missing.append("apply video settings on Video Cropping")
            if not self._data_ready:
                missing.append("load data on Full Data Plot")
            tooltip = "Animated Plot becomes available after you " + " and ".join(missing) + "."
        self.tabs.setTabToolTip(2, tooltip)

    def _push_data_to_animated_tab(self) -> None:
        if self.data_tab is None or self.animated_tab is None:
            return
        df = self.data_tab.dataframe
        time_col = self.data_tab.time_col
        col = self.data_tab.selected_column
        color = self.data_tab.trace_color
        y_min, y_max = self.data_tab.y_limits()

        if df.empty or not time_col or not col:
            return

        cast(Any, self.animated_tab).update_data(
            df,
            time_col,
            col,
            color,
            y_min,
            y_max,
            self.data_tab.linewidth,
            self.data_tab.offset_s,
            data_col2=self.data_tab.selected_column2,
            color2=self.data_tab.trace_color2,
            trace_transform=self.data_tab.trace_transform,
            legend_labels=self.data_tab.legend_labels,
        )

    def _on_batch_export_requested(self) -> None:
        speeds = self._choose_export_speeds()
        if speeds:
            self._on_export_requested(speeds)

    def _choose_export_speeds(self) -> list[float]:
        from PySide6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QLineEdit,
            QVBoxLayout,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Export Speeds")
        layout = QVBoxLayout(dialog)
        current_speed = self.animated_tab.speed_factor if self.animated_tab is not None else 1.0
        layout.addWidget(
            QLabel(
                f"Export Video uses the Preview speed ({current_speed:g}x). "
                "This dialog controls the speeds for this multi-export only; "
                "they run sequentially."
            )
        )
        saved = self._settings.value("export/speeds", ["0.5", "1", "2"])
        saved_values = {str(value) for value in (saved if isinstance(saved, list) else [saved])}
        checks: list[tuple[float, QCheckBox]] = []
        form = QFormLayout()
        for speed in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
            check = QCheckBox(f"{speed:g}x")
            check.setChecked(f"{speed:g}" in saved_values)
            checks.append((speed, check))
            form.addRow(check)
        custom = QLineEdit()
        custom.setPlaceholderText("e.g. 1.5, 3")
        form.addRow("Other speeds:", custom)
        layout.addLayout(form)
        summary = QLabel()
        summary.setWordWrap(True)
        layout.addWidget(summary)

        def selected_values() -> list[float] | None:
            values = [speed for speed, check in checks if check.isChecked()]
            try:
                values.extend(
                    float(part.strip().lower().rstrip("x"))
                    for part in custom.text().split(",")
                    if part.strip()
                )
            except ValueError:
                return None
            return list(dict.fromkeys(values))

        def update_summary() -> None:
            values = selected_values()
            if values is None:
                summary.setText("Enter custom speeds as numbers, separated by commas.")
                return
            if not values:
                summary.setText("No speeds selected.")
                return
            labels = ", ".join(f"{speed:g}x" for speed in values)
            names = ", ".join(f"<base>_{speed:g}x.mp4" for speed in values)
            summary.setText(f"Export {len(values)} videos at: {labels}\nNames: {names}")

        for _, check in checks:
            check.toggled.connect(update_summary)
        custom.textChanged.connect(lambda _text: update_summary())
        update_summary()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            values = selected_values()
            if values is None:
                QMessageBox.warning(self, "Invalid speed", "Custom speeds must be numbers.")
                continue
            if not values or any(not 0.05 <= speed <= 16 for speed in values):
                QMessageBox.warning(
                    self, "Invalid speed", "Choose at least one speed from 0.05x to 16x."
                )
                continue
            self._settings.setValue("export/speeds", [f"{speed:g}" for speed in values])
            return values
        return []

    def _on_export_requested(self, speeds: list[float] | None = None) -> None:
        if self._export_worker is not None:
            self.statusBar().showMessage("An export is already running.", 5000)
            return
        anim = self.animated_tab
        data_tab = self.data_tab
        if anim is None or data_tab is None:
            QMessageBox.warning(self, "Nothing to export", "Load video and data first.")
            return
        if speeds is None and not anim.validate_speed():
            QMessageBox.warning(self, "Invalid speed", "Preview speed must be from 0.05x to 16x.")
            return
        # Export the visible video controls and capture that same state in history.
        if self.video_tab._cap is not None and not self.video_tab._on_confirm():
            return
        if not anim.video_path:
            QMessageBox.warning(
                self,
                "Nothing to export",
                "Apply video settings on the Video Cropping tab first.",
            )
            return

        df = data_tab.dataframe
        time_col = data_tab.time_col
        col = data_tab.selected_column
        if df.empty or not time_col or not col:
            QMessageBox.warning(self, "Nothing to export", "Load data first.")
            return

        from PySide6.QtWidgets import QFileDialog

        source_stem = os.path.splitext(os.path.basename(anim.video_path))[0]
        initial = os.path.join(
            str(self._settings.value("folders/export", "")), f"{source_stem}_sync.mp4"
        )
        title = "Choose Base Filename" if speeds else "Save Video"
        out_path, _ = QFileDialog.getSaveFileName(self, title, initial, "MP4 files (*.mp4)")
        if not out_path:
            return
        chosen_path = out_path
        out_path = _with_mp4_extension(out_path)
        export_speeds = speeds or [anim.speed_factor]
        if speeds is None:
            output_paths = [out_path]
            # The save dialog confirmed only the path it returned, not the appended one.
            if out_path != chosen_path and os.path.exists(out_path):
                answer = QMessageBox.question(
                    self,
                    "Replace existing export?",
                    f"{os.path.basename(out_path)} already exists. Replace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        else:
            stem, extension = os.path.splitext(out_path)
            output_paths = [f"{stem}_{speed:g}x{extension}" for speed in export_speeds]
            existing = [path for path in output_paths if os.path.exists(path)]
            if existing:
                answer = QMessageBox.question(
                    self,
                    "Replace existing exports?",
                    f"{len(existing)} generated output file(s) already exist. Replace them?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        self._settings.setValue("folders/export", os.path.dirname(os.path.abspath(out_path)))

        crop_tuple = anim.crop.to_tuple() if (anim.crop and anim.crop.is_valid()) else None
        composition = anim.composition_settings
        anchor_positions = {
            "Bottom right": (0.3, 0.5),
            "Bottom left": (0.8, 0.5),
            "Top right": (0.3, 0.8),
            "Top left": (0.8, 0.8),
        }
        anchor_x, anchor_y = anchor_positions.get(str(composition.get("anchor")), (0.8, 0.5))

        base_kwargs = dict(
            video_path=anim.video_path,
            fps=anim.fps,
            start_s=anim.start_s,
            end_s=anim.end_s,
            crop=crop_tuple,
            colour_lut=anim.colour_lut,
            rotation=anim.rotation,
            df=df.copy(deep=True),
            time_col=time_col,
            data_col=col,
            data_col2=data_tab.selected_column2,
            offset_s=data_tab.offset_s,
            color=data_tab.trace_color,
            color2=data_tab.trace_color2,
            legend_labels=data_tab.legend_labels,
            y_min=data_tab.y_limits()[0],
            y_max=data_tab.y_limits()[1],
            max_change=anim.max_change,
            pin_scale_bars_to_axes=True,
            linewidth=anim.linewidth,
            deflicker=anim.deflicker_enabled,
            orientation="stacked" if anim.layout_combo.currentIndex() == 1 else "side_by_side",
            video_width_fraction=getattr(anim, "video_width_fraction", 0.6),
            trace_transform=data_tab.trace_transform,
            show_time_scale=bool(composition.get("show_time_scale", True)),
            show_signal_scale=bool(composition.get("show_signal_scale", True)),
            scale_bar_font_size=float(composition.get("scale_font_size", 16)),
            scale_bar_linewidth=float(composition.get("scale_line_width", 2.8)),
            time_scale_s=float(composition.get("time_scale_s", 0)) or None,
            signal_scale=float(composition.get("signal_scale", 0)) or None,
            time_scale_label=str(composition.get("time_scale_label", "")),
            signal_scale_label=str(composition.get("signal_scale_label", "")),
            scale_bar_anchor_x=anchor_x,
            scale_bar_anchor_y=anchor_y,
            # Plot backgrounds are intentionally fixed to white. Keep the
            # legacy project field readable without allowing it to affect an
            # export.
            plot_background="#ffffff",
            audit_context={
                "project_name": os.path.basename(self._project_path)
                if self._project_path
                else source_stem,
                "project_path": self._project_path,
                "data_path": data_tab.data_path,
                "loaded_data_fingerprint": data_tab.source_fingerprint,
                "project": self._project_document(portable_paths=False),
            },
        )

        # Progress dialog
        duration_s = anim.end_s - anim.start_s
        jobs = []
        for speed, path in zip(export_speeds, output_paths, strict=True):
            job = dict(base_kwargs)
            audit_context = cast(dict[str, Any], deepcopy(base_kwargs["audit_context"]))
            audit_context["project"]["composition"]["speed"] = f"{speed:g}x"
            job["audit_context"] = audit_context
            estimated = max(1, math.ceil(duration_s * min(anim.fps, 60.0) / speed))
            job.update(output_path=path, speed_factor=speed, _estimated_frames=estimated)
            jobs.append(job)
        overall_frames = sum(int(job["_estimated_frames"]) for job in jobs)
        progress = QProgressDialog("Exporting video(s)...", "Cancel", 0, overall_frames, self)
        progress.setWindowTitle("Export")
        progress.setLabelText("Preparing export and identifying source files…")
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = _ExportWorker(jobs)
        self._export_worker = worker
        anim.set_export_busy(True)

        def on_progress(current: int, total: int) -> None:
            progress.setLabelText("Exporting video(s)…")
            progress.setMaximum(total)
            progress.setValue(current)

        def on_finished() -> None:
            progress.setValue(overall_frames)
            progress.close()
            QMessageBox.information(
                self,
                "Export complete",
                "Saved to:\n"
                + "\n".join(output_paths)
                + "\n\nSettings saved alongside each video. "
                "Find all runs in File → Export History.",
            )

        # Terminal result signals only notify. Ownership and the busy state are
        # released by QThread.finished, after run() has actually returned, so
        # close/shutdown guards and the single-export guard never see a cleared
        # worker while its thread is still running.
        def on_error(msg: str) -> None:
            progress.close()
            QMessageBox.critical(self, "Export failed", msg)

        worker.progress.connect(on_progress)
        worker.succeeded.connect(on_finished)
        progress.canceled.connect(worker.request_cancel)
        progress.canceled.connect(lambda: progress.setLabelText("Cancelling safely..."))

        def on_cancelled() -> None:
            progress.close()
            self.statusBar().showMessage("Export cancelled safely.", 5000)

        worker.cancelled.connect(on_cancelled)
        worker.error.connect(on_error)
        # Sole release point; connected before deleteLater so the reference is
        # dropped before the QThread object can be destroyed.
        worker.finished.connect(lambda: self._clear_export_worker(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _clear_export_worker(self, worker: _ExportWorker) -> None:
        """Clear only the worker that owns the current export lifecycle."""
        if self._export_worker is worker:
            self._export_worker = None
            if self.animated_tab is not None:
                self.animated_tab.set_export_busy(False)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        if self._export_worker is not None and self._export_worker.isRunning():
            self._export_worker.request_cancel()
            if not self._export_worker.wait(10000):
                QMessageBox.warning(
                    self, "Export still stopping", "Please wait for export cancellation to finish."
                )
                event.ignore()
                return
        if self._demo_worker is not None and self._demo_worker.isRunning():
            if not self._demo_worker.wait(5000):
                QMessageBox.warning(
                    self, "Demo still preparing", "Please wait for demo preparation to finish."
                )
                event.ignore()
                return
        self._clear_recovery()
        self._release_media()
        super().closeEvent(event)

    def _release_media(self) -> None:
        """Release native video handles and stop playback on an accepted close.

        Child tabs do not receive their own ``closeEvent`` when the main window
        closes, so without this their OpenCV captures and playback timer stay
        alive until interpreter teardown. Only release; no state-changing
        resets that could emit ``project_changed`` and re-arm recovery.
        """
        self.video_tab.release_media()
        if self.animated_tab is not None:
            self.animated_tab._release_cap()
