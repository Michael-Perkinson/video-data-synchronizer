"""Searchable export history with readable settings and direct folder access."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
)

from video_data_sync.export_history import history_directory, load_records


def record_summary(record: dict) -> str:
    settings = record.get("settings", {})
    context = record.get("context", {})
    project = context.get("project", {})
    colour = project.get("video", {}).get("colour", {})
    data = project.get("data", {})
    lines = [
        f"Output: {record.get('output_path', '')}",
        f"Status: {record.get('status', '')}",
        f"Started: {record.get('started_at', '')}",
        f"Finished: {record.get('finished_at') or 'Not recorded (running or interrupted)'}",
        f"Run ID: {record.get('run_id', '')}",
        f"Project: {context.get('project_name', 'Untitled')}",
        "",
        "Sources",
    ]
    for name, source in record.get("sources", {}).items():
        lines.append(f"{name.title()}: {source.get('path', '')}")
        lines.append(f"SHA-256: {source.get('sha256', '')}")
    lines += [
        "",
        "Timing and signals",
        f"Clip: {settings.get('start_s')} to {settings.get('end_s')} seconds",
        f"Data offset: {settings.get('offset_s')} seconds",
        f"Speed: {settings.get('speed_factor')}x",
        f"Time column: {data.get('time_column', settings.get('time_col'))}",
        f"Time unit: {data.get('time_unit', 'Seconds (prepared data)')}",
        f"Signals: {settings.get('data_col')} / {settings.get('data_col2') or 'none'}",
        f"Trace transform: {settings.get('trace_transform')}",
        f"Trace colours: {settings.get('color')} / {settings.get('color2')}",
        "",
        "Video adjustments",
        f"Crop (pixels): {settings.get('crop') or 'Full frame'}",
        *[f"{key.replace('_', ' ').title()}: {value}" for key, value in colour.items()],
        f"De-flicker: {settings.get('deflicker')}",
        "",
        "Actual output and automatic settings",
        *[
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in record.get("resolved", {}).items()
            if key != "sources"
        ],
    ]
    if record.get("error"):
        lines.extend(["", f"Error: {record['error']}"])
    if record.get("sidecar_error"):
        lines.extend(["", f"Settings copy error: {record['sidecar_error']}"])
    lines.extend(["", "Complete settings", json.dumps(record, indent=2, ensure_ascii=False)])
    return "\n".join(lines)


class ExportHistoryDialog(QDialog):
    restore_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export History")
        self.resize(1000, 650)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search video, data, project, output, speed, or run ID…")
        layout.addWidget(self.search)
        splitter = QSplitter()
        self.runs = QListWidget()
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        splitter.addWidget(self.runs)
        splitter.addWidget(self.details)
        splitter.setSizes([350, 650])
        layout.addWidget(splitter, 1)
        layout.addWidget(QLabel("Settings are saved automatically for every export attempt."))
        buttons = QHBoxLayout()
        self.output_button = QPushButton("Open output folder")
        self.restore_button = QPushButton("Restore settings")
        self.output_button.clicked.connect(self._open_output)
        self.restore_button.clicked.connect(self._restore)
        buttons.addWidget(self.output_button)
        buttons.addWidget(self.restore_button)
        history = QPushButton("Open history folder")
        history.clicked.connect(lambda: self._open_folder(history_directory()))
        buttons.addWidget(history)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self._filter)
        self.runs.currentItemChanged.connect(self._selection_changed)
        self.refresh()

    @staticmethod
    def _open_folder(path: Path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def refresh(self):
        self._records = load_records()
        self._filter()

    def _filter(self, _text=None):
        query = self.search.text().casefold().split()
        self.runs.clear()
        for record in self._records:
            searchable = record_summary(record).casefold()
            if not all(word in searchable for word in query):
                continue
            name = Path(record.get("output_path", "")).name
            speed = record.get("settings", {}).get("speed_factor", "?")
            item = QListWidgetItem(
                f"{name} · {speed}x · {record.get('status', '')}\n{record.get('started_at', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.runs.addItem(item)
        if self.runs.count():
            self.runs.setCurrentRow(0)
        else:
            self.details.setPlainText("No exports found." if query else "No exports recorded yet.")
            self._selection_changed()

    def _record(self):
        item = self.runs.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self, *_args):
        record = self._record()
        self.output_button.setEnabled(bool(record))
        self.restore_button.setEnabled(bool(record and record.get("context", {}).get("project")))
        if record:
            self.details.setPlainText(record_summary(record))

    def _open_output(self):
        if record := self._record():
            self._open_folder(Path(record["output_path"]).parent)

    def _restore(self):
        if record := self._record():
            self.restore_requested.emit(record)
