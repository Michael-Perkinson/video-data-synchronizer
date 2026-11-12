"""
Full Data Plot Tab.

Lets the user:
  - Load a CSV / Excel file
  - Select which column to plot
  - Choose a trace colour
  - Calculate a z-score relative to a baseline window
  - Toggle auto / fixed y-axis limits
  - Set an offset (seconds) to align data with video time

Emits data_loaded(df, time_col) so other tabs can receive the same dataset.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QLocale, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_data_sync.data.loader import read_file, read_folder_path, write_folder_path
from video_data_sync.data.processor import (
    calculate_z_score,
    find_time_column,
    preferred_signal_columns,
    prepare_dataframe,
    signal_columns_grouped,
)
from video_data_sync.gui.controls_scroll import (
    GROUP_GAP,
    ROW_GAP,
    ROW_SPACING,
    ControlsScrollArea,
    add_gap,
)
from video_data_sync.plotting.plot_widget import PlotWidget, Trace

DEFAULT_LINE_WIDTH = 1.5


def c_locale_validator(validator: QDoubleValidator) -> QDoubleValidator:
    """Make ``validator`` accept only "." decimals, independent of the system locale.

    The accepted text is then always parseable by Python ``float()``.
    """
    locale = QLocale.c()
    locale.setNumberOptions(
        QLocale.NumberOption.OmitGroupSeparator | QLocale.NumberOption.RejectGroupSeparator
    )
    validator.setLocale(locale)
    return validator


def _saved_line_width(value: object) -> float:
    """Convert a persisted ``data/lineWidth`` value to a float, or the default.

    Missing, empty, non-numeric, boolean, non-finite and other unexpected
    values return ``DEFAULT_LINE_WIDTH`` instead of raising. Finite numbers are
    returned unchanged; the spin box still applies its own range limits.
    """
    if value is None or isinstance(value, bool):
        return DEFAULT_LINE_WIDTH
    candidate: str | int | float
    if isinstance(value, (int, float)):
        candidate = value
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        return DEFAULT_LINE_WIDTH
    try:
        number = float(candidate)
    except (ValueError, OverflowError):
        return DEFAULT_LINE_WIDTH
    return number if math.isfinite(number) else DEFAULT_LINE_WIDTH


class DataTab(QWidget):
    """
    Signals
    -------
    data_loaded(pd.DataFrame, str)
        Emitted after a file is loaded and processed.
        Args: (dataframe, time_column_name)
    """

    data_loaded = Signal(object, str)
    project_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._df: pd.DataFrame = pd.DataFrame()
        self._raw_df: pd.DataFrame = pd.DataFrame()  # pre-conversion, for re-processing
        self._time_col: str | None = None
        self._settings = QSettings()
        self._suspend_preferences = False
        self._trace_color = str(self._settings.value("data/traceColor", "#000000"))
        self._trace_color2 = str(self._settings.value("data/traceColor2", "#d62728"))
        self._column_colors = self._load_column_colors()
        self._legend_labels = self._load_legend_labels()
        self._folder_path = str(self._settings.value("folders/data", read_folder_path()))
        self._data_path: str | None = None
        self.source_fingerprint: dict | None = None
        self._zscore_recipe: dict[str, object] | None = None
        self._zscore_steps: list[dict[str, object]] = []
        self.transform_combo = QComboBox()
        self.transform_combo.addItems(["Subtract clip-start value", "Raw values"])
        self._build_ui()
        self.time_unit_combo.setCurrentText(
            str(self._settings.value("data/timeUnit", "Auto-detect"))
        )
        self._restore_line_width_preference()
        self.chk_overlay.setChecked(
            str(self._settings.value("data/overlay", "false")).lower() == "true"
        )
        transform = str(self._settings.value("data/transform", "subtract_first"))
        self.transform_combo.setCurrentText(
            "Raw values" if transform == "raw" else "Subtract clip-start value"
        )
        self.transform_combo.currentTextChanged.connect(self._on_transform_changed)
        self.transform_combo.currentTextChanged.connect(self.project_changed)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- Left control panel ----------------------------------------
        # Spare height is shared by capped gaps between rows and blocks (add_gap);
        # only what is left beyond those caps goes to the bottom stretch.
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 4, 0)
        left.setSpacing(ROW_SPACING)

        # Load
        self.btn_load = QPushButton("Load CSV / Excel")
        self.btn_load.clicked.connect(self._on_load)
        left.addWidget(self.btn_load)
        add_gap(left, GROUP_GAP)

        # Time column and unit override: label beside each control
        self.time_unit_combo = QComboBox()
        self.time_unit_combo.addItems(
            ["Auto-detect", "Seconds", "Minutes", "Milliseconds", "Hours"]
        )
        # Size to the longest choice ("Milliseconds") so no entry is clipped.
        self.time_unit_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.time_unit_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.time_unit_combo.setToolTip(
            "Time unit of the time column. Auto-detect reads it from the column name."
        )
        self.time_unit_combo.currentTextChanged.connect(self._on_time_unit_changed)
        self.time_column_combo = QComboBox()
        self.time_column_combo.setToolTip("Choose the column containing recording timestamps.")
        # Long column names may be cut off in the closed combo; its list shows them in full.
        self.time_column_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.time_column_combo.setMinimumContentsLength(6)
        self.time_column_combo.currentTextChanged.connect(self._on_time_column_changed)
        time_form = QFormLayout()
        time_form.setContentsMargins(0, 0, 0, 0)
        time_form.setSpacing(ROW_SPACING)
        # With wide fonts a field moves below its label instead of widening the column.
        time_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        time_label = QLabel("Time column:")
        time_label.setBuddy(self.time_column_combo)
        unit_label = QLabel("Time unit:")
        unit_label.setBuddy(self.time_unit_combo)
        time_form.addRow(time_label, self.time_column_combo)
        # The unit combo keeps its own (Fixed) width so "Milliseconds" is never clipped.
        time_form.addRow(unit_label, self.time_unit_combo)
        left.addLayout(time_form)
        add_gap(left, GROUP_GAP)

        # Column selector(s)
        col_group = QGroupBox("Data Column")
        col_layout = QVBoxLayout()
        col_layout.setSpacing(ROW_SPACING)

        # ---- Signal 1: column, colour and legend ----
        self.column_combo = QComboBox()
        self.column_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.column_combo.setMinimumContentsLength(8)
        self.column_combo.currentTextChanged.connect(self._on_column_changed)
        legend_row = QHBoxLayout()
        legend_label = QLabel("Legend:")
        self.legend_label_input = QLineEdit()
        legend_label.setBuddy(self.legend_label_input)
        self.legend_label_input.setPlaceholderText("Use column name")
        self.legend_label_input.setMaxLength(128)
        self.legend_label_input.setToolTip(
            "Optional name shown for this signal in plot legends and video exports. "
            "Leave blank to use the column heading."
        )
        self.legend_label_input.editingFinished.connect(
            lambda: self._on_legend_label_changed(secondary=False)
        )
        legend_row.addWidget(legend_label)
        legend_row.addWidget(self.legend_label_input, stretch=1)

        colour_row = QHBoxLayout()
        colour_row.setSpacing(6)
        self.btn_colour = QPushButton("Colour")
        self.btn_colour.setMinimumWidth(50)
        self.btn_colour.clicked.connect(self._on_choose_colour)
        self._colour_preview = QLabel()
        self._colour_preview.setFixedSize(20, 20)
        self._colour_preview.setStyleSheet(
            f"background: {self._trace_color}; border: 1px solid #888;"
        )
        colour_row.addWidget(self.column_combo, stretch=1)
        colour_row.addWidget(self.btn_colour)
        colour_row.addWidget(self._colour_preview)
        col_layout.addLayout(colour_row)
        col_layout.addLayout(legend_row)
        add_gap(col_layout, ROW_GAP)

        # ---- Signal 2 (optional overlay): column, colour and legend ----
        self.chk_overlay = QCheckBox("Overlay 2nd signal")
        self.chk_overlay.toggled.connect(self._on_overlay_toggled)
        col_layout.addWidget(self.chk_overlay)

        self.column_combo2 = QComboBox()
        self.column_combo2.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.column_combo2.setMinimumContentsLength(8)
        self.column_combo2.setEnabled(False)
        self.column_combo2.currentTextChanged.connect(self._on_column_changed)
        legend_row2 = QHBoxLayout()
        legend_label2 = QLabel("2nd legend:")
        self.legend_label_input2 = QLineEdit()
        legend_label2.setBuddy(self.legend_label_input2)
        self.legend_label_input2.setPlaceholderText("Use column name")
        self.legend_label_input2.setMaxLength(128)
        self.legend_label_input2.setToolTip(
            "Optional name shown for the second signal in plot legends and video exports. "
            "Leave blank to use the column heading."
        )
        self.legend_label_input2.setEnabled(False)
        self.legend_label_input2.editingFinished.connect(
            lambda: self._on_legend_label_changed(secondary=True)
        )
        legend_row2.addWidget(legend_label2)
        legend_row2.addWidget(self.legend_label_input2, stretch=1)

        colour_row2 = QHBoxLayout()
        colour_row2.setSpacing(6)
        self.btn_colour2 = QPushButton("Colour")
        self.btn_colour2.setMinimumWidth(50)
        self.btn_colour2.setEnabled(False)
        self.btn_colour2.clicked.connect(self._on_choose_colour2)
        self._colour_preview2 = QLabel()
        self._colour_preview2.setFixedSize(20, 20)
        self._colour_preview2.setStyleSheet(
            f"background: {self._trace_color2}; border: 1px solid #888;"
        )
        colour_row2.addWidget(self.column_combo2, stretch=1)
        colour_row2.addWidget(self.btn_colour2)
        colour_row2.addWidget(self._colour_preview2)
        col_layout.addLayout(colour_row2)
        col_layout.addLayout(legend_row2)

        col_group.setLayout(col_layout)
        left.addWidget(col_group)
        add_gap(left, GROUP_GAP)

        self.lw_spinner = QDoubleSpinBox()
        self.lw_spinner.setRange(0.2, 5.0)
        self.lw_spinner.setSingleStep(0.1)
        self.lw_spinner.setValue(1.5)
        self.lw_spinner.setDecimals(1)
        self.lw_spinner.setFixedWidth(70)
        self.lw_spinner.valueChanged.connect(self._on_linewidth_changed)

        # Trace transform and line width: short labels beside their controls.
        appearance_form = QFormLayout()
        appearance_form.setContentsMargins(0, 0, 0, 0)
        appearance_form.setSpacing(ROW_SPACING)
        appearance_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        transform_label = QLabel("Trace transform:")
        transform_label.setBuddy(self.transform_combo)
        transform_tips = {
            "Subtract clip-start value": "Start each trace at zero at the clip start.",
            "Raw values": "Plot the values exactly as loaded.",
        }
        for index in range(self.transform_combo.count()):
            text = self.transform_combo.itemText(index)
            self.transform_combo.setItemData(
                index, transform_tips.get(text, text), Qt.ItemDataRole.ToolTipRole
            )
        self.transform_combo.setToolTip("How each trace is offset before plotting.")
        # May shrink (eliding the choice text) instead of widening the column.
        self.transform_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.transform_combo.setMinimumContentsLength(6)
        appearance_form.addRow(transform_label, self.transform_combo)
        lw_label = QLabel("Line width:")
        lw_label.setBuddy(self.lw_spinner)
        appearance_form.addRow(lw_label, self.lw_spinner)
        left.addLayout(appearance_form)
        add_gap(left, GROUP_GAP)

        # Y-axis limits: mode on the first row, min and max side by side below.
        y_group = QGroupBox("Y-Axis")
        y_layout = QVBoxLayout()
        y_layout.setSpacing(ROW_SPACING)
        y_radio_row = QHBoxLayout()
        self.radio_auto = QRadioButton("Auto")
        self.radio_fixed = QRadioButton("Fixed")
        self.radio_auto.setChecked(True)
        y_radio_group = QButtonGroup(self)
        y_radio_group.addButton(self.radio_auto)
        y_radio_group.addButton(self.radio_fixed)
        y_radio_row.addWidget(self.radio_auto)
        y_radio_row.addWidget(self.radio_fixed)
        y_radio_row.addStretch()
        y_layout.addLayout(y_radio_row)
        add_gap(y_layout, ROW_GAP)

        fixed_row = QHBoxLayout()
        fixed_row.setSpacing(6)
        self.y_min_input = QLineEdit()
        self.y_max_input = QLineEdit()
        for w in (self.y_min_input, self.y_max_input):
            w.setValidator(c_locale_validator(QDoubleValidator()))
            w.setFixedWidth(70)
            w.setEnabled(False)
        for text, field in (("Min:", self.y_min_input), ("Max:", self.y_max_input)):
            label = QLabel(text)
            label.setBuddy(field)
            fixed_row.addWidget(label)
            fixed_row.addWidget(field)
        fixed_row.addStretch()
        y_layout.addLayout(fixed_row)
        y_group.setLayout(y_layout)
        left.addWidget(y_group)
        add_gap(left, GROUP_GAP)

        self.radio_auto.toggled.connect(self._on_y_mode_changed)
        self.radio_fixed.toggled.connect(self._on_y_mode_changed)
        for w in (self.y_min_input, self.y_max_input):
            w.textChanged.connect(self._replot)
            w.textChanged.connect(self.project_changed)

        # Optional baseline normalization. A group box is otherwise never narrower than its
        # title; the explicit minimum lets only the title (not the controls) be cut off
        # with extremely wide fonts. With normal fonts the title is about 170 px.
        z_group = QGroupBox("Optional: Baseline Z-Score")
        z_group.setToolTip("Optional: z-score the signal against a baseline window.")
        z_group.setMinimumWidth(240)
        z_layout = QVBoxLayout()
        z_layout.setSpacing(ROW_SPACING)
        self.z_start_input = QLineEdit()
        self.z_end_input = QLineEdit()
        z_row = QHBoxLayout()
        z_row.setSpacing(6)
        for text, tip, field in (
            ("Start (s):", "Baseline start (s)", self.z_start_input),
            ("End (s):", "Baseline end (s)", self.z_end_input),
        ):
            field.setValidator(c_locale_validator(QDoubleValidator(0, 99999, 3)))
            field.setFixedWidth(60)
            field.setToolTip(tip)
            field.setAccessibleName(tip)
            label = QLabel(text)
            label.setToolTip(tip)
            label.setBuddy(field)
            # One line with normal fonts; wraps ("Start" / "(s):") only with very wide ones.
            label.setWordWrap(True)
            z_row.addWidget(label)
            z_row.addWidget(field)
        z_row.addStretch()
        z_layout.addLayout(z_row)
        add_gap(z_layout, ROW_GAP)
        self.btn_zscore = QPushButton("Calculate Z-Score")
        self.btn_zscore.clicked.connect(self._on_zscore)
        z_layout.addWidget(self.btn_zscore)
        z_group.setLayout(z_layout)
        left.addWidget(z_group)
        add_gap(left, GROUP_GAP)

        # Sync offset: the explanation first, then the field.
        sync_group = QGroupBox("Sync Offset")
        sync_layout = QVBoxLayout()
        sync_layout.setSpacing(4)
        sync_help = "Seconds into the data recording when the video starts."
        sync_desc = QLabel(
            f"{sync_help}\n+ = data started before video\n− = video started before data"
        )
        sync_desc.setStyleSheet("color: #555; font-size: 10px;")
        sync_desc.setWordWrap(True)
        sync_layout.addWidget(sync_desc)
        add_gap(sync_layout, ROW_GAP)
        offset_row = QHBoxLayout()
        offset_row.setSpacing(6)
        offset_label = QLabel("Offset:")
        offset_label.setToolTip(sync_help)
        self.offset_input = QLineEdit("0.0")
        offset_label.setBuddy(self.offset_input)
        self.offset_input.setToolTip(sync_help)
        self.offset_input.setValidator(c_locale_validator(QDoubleValidator(-99999, 99999, 3)))
        self.offset_input.setFixedWidth(64)
        self.offset_input.textChanged.connect(self._on_offset_changed)
        offset_row.addWidget(offset_label)
        offset_row.addWidget(self.offset_input)
        offset_row.addWidget(QLabel("s"))
        offset_row.addStretch()
        sync_layout.addLayout(offset_row)
        sync_group.setLayout(sync_layout)
        left.addWidget(sync_group)

        # Time range info
        self.lbl_time_info = QLabel("")
        self.lbl_time_info.setWordWrap(True)
        self.lbl_time_info.setStyleSheet("color: #555; font-size: 10px;")
        left.addWidget(self.lbl_time_info)

        # Takes only the height left over once every capped gap is full.
        left.addStretch(1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        # Scroll the settings column so every control stays reachable on short
        # screens instead of forcing the window taller than the display.
        self.controls_scroll = ControlsScrollArea(left_widget, 330)
        root.addWidget(self.controls_scroll)

        # ---- Plot area -------------------------------------------------
        self.plot = PlotWidget()
        root.addWidget(self.plot, stretch=1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Data File",
            self._folder_path,
            "Data files (*.csv *.xlsx)",
        )
        if not path:
            return
        if not self.load_file(path):
            try:
                raw = read_file(path)
                columns = [str(c) for c in raw if pd.api.types.is_numeric_dtype(raw[c])]
            except (OSError, ValueError):
                return
            if columns and not raw.empty:
                column, ok = QInputDialog.getItem(
                    self,
                    "Choose time column",
                    "Column containing increasing timestamps:",
                    columns,
                    0,
                    False,
                )
                if ok:
                    self.load_file(path, time_column=column)

    def load_file(
        self,
        path: str,
        *,
        time_column: str | None = None,
        time_unit: str | None = None,
        remember_folder: bool = True,
    ) -> bool:
        try:
            from video_data_sync.export_history import fingerprint

            before = Path(path).stat()
            raw_df = read_file(path)
            raw_df.columns = raw_df.columns.map(str)
            unit = time_unit or self.time_unit_combo.currentText()
            df, detected = self._prepare(raw_df, time_column, unit)
            identity = fingerprint(path)
            if (before.st_size, before.st_mtime_ns) != (
                identity["size_bytes"],
                identity["modified_ns"],
            ):
                raise ValueError("The data file changed while loading. Please load it again.")
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return False

        import os

        self._folder_path = os.path.dirname(path)
        if remember_folder:
            self.persist_folder_preference()
        self._data_path = path
        self.source_fingerprint = identity

        self._raw_df = raw_df
        self._zscore_recipe = None
        self._zscore_steps = []
        self.time_unit_combo.blockSignals(True)
        self.time_unit_combo.setCurrentText(unit)
        self.time_unit_combo.blockSignals(False)
        self.time_column_combo.blockSignals(True)
        self.time_column_combo.clear()
        self.time_column_combo.addItems(
            [c for c in raw_df if pd.api.types.is_numeric_dtype(raw_df[c])]
        )
        self.time_column_combo.setCurrentText(time_column or find_time_column(raw_df) or "")
        self.time_column_combo.blockSignals(False)
        self._commit_dataframe(df, detected)
        return True

    def persist_folder_preference(self) -> None:
        self._settings.setValue("folders/data", self._folder_path)
        try:
            write_folder_path(self._folder_path)
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def _prepare(raw, column, unit):
        if raw.empty:
            raise ValueError("The file contains no data rows.")
        df, time = prepare_dataframe(raw, column, unit)
        if time is None:
            raise ValueError("Could not detect time. Choose a numeric timestamp column.")
        if len(df.columns) < 2:
            raise ValueError("The file needs a time column and at least one numeric signal.")
        return df, time

    def _apply_and_reload(self) -> None:
        """Process _raw_df with the current time unit setting and refresh everything."""
        if self._raw_df.empty:
            return

        try:
            df, time_col = self._prepare(
                self._raw_df,
                self.time_column_combo.currentText() or None,
                self.time_unit_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid time column", str(exc))
            self.time_column_combo.blockSignals(True)
            self.time_column_combo.setCurrentText(self._applied_time_column)
            self.time_column_combo.blockSignals(False)
            self.time_unit_combo.blockSignals(True)
            self.time_unit_combo.setCurrentText(self._applied_time_unit)
            self.time_unit_combo.blockSignals(False)
            return
        self._zscore_recipe = None
        self._zscore_steps = []
        self._commit_dataframe(df, time_col)

    def _commit_dataframe(self, df, time_col) -> None:
        self._applied_time_column = self.time_column_combo.currentText()
        self._applied_time_unit = self.time_unit_combo.currentText()
        self._df = df
        self._time_col = time_col
        self._update_column_combo()
        self._replot()
        self._update_time_info()
        self.data_loaded.emit(self._df, self._time_col or "")

    def _on_time_unit_changed(self, _: str) -> None:
        self._apply_and_reload()
        if not self._suspend_preferences:
            self._settings.setValue("data/timeUnit", self.time_unit_combo.currentText())

    def _on_time_column_changed(self, _: str) -> None:
        self._apply_and_reload()

    def _on_offset_changed(self, _: str) -> None:
        self._emit_if_loaded()

    def _on_transform_changed(self, _: str) -> None:
        if not self._suspend_preferences:
            self._settings.setValue("data/transform", self.trace_transform)
        self._emit_if_loaded()

    def _update_time_info(self) -> None:
        if self._df.empty or self._time_col is None:
            self.lbl_time_info.setText("")
            return
        t = self._df[self._time_col]
        t_min, t_max = float(t.min()), float(t.max())
        n = len(self._df)
        self.lbl_time_info.setText(f"Time: {t_min:.1f} – {t_max:.1f} s\n({n:,} rows)")

    def _populate_combo(self, combo: QComboBox, default: str | None) -> None:
        """Fill a column combo: preferred signals first, then a separator and the rest."""
        preferred, other = signal_columns_grouped(self._df, self._time_col)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(preferred)
        if preferred and other:
            combo.insertSeparator(combo.count())
        combo.addItems(other)
        combo.blockSignals(False)
        if default and combo.findText(default) != -1:
            combo.setCurrentText(default)
        elif combo.count():
            combo.setCurrentIndex(0)

    def _update_column_combo(self) -> None:
        defaults = preferred_signal_columns(self._df, self._time_col, n=2)
        fallback1 = defaults[0] if len(defaults) > 0 else None
        fallback2 = defaults[1] if len(defaults) > 1 else fallback1
        saved1 = str(self._settings.value("data/preferredColumn1", ""))
        saved2 = str(self._settings.value("data/preferredColumn2", ""))
        default1 = saved1 if saved1 in self._df.columns else fallback1
        default2 = saved2 if saved2 in self._df.columns else fallback2
        self._populate_combo(self.column_combo, default1)
        self._populate_combo(self.column_combo2, default2)
        self._refresh_legend_label_inputs()

    def _on_column_changed(self, _: str) -> None:
        if not self._suspend_preferences:
            if self.column_combo.currentText():
                self._settings.setValue("data/preferredColumn1", self.column_combo.currentText())
            if self.column_combo2.currentText():
                self._settings.setValue("data/preferredColumn2", self.column_combo2.currentText())
            self._apply_mapped_column_colors()
        self._refresh_legend_label_inputs()
        self._replot()
        self._emit_if_loaded()

    def _load_legend_labels(self) -> dict[str, str]:
        saved = self._settings.value("data/legendLabels", "{}")
        try:
            values = json.loads(str(saved))
        except (TypeError, ValueError):
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(column): str(label).strip()
            for column, label in values.items()
            if isinstance(column, str)
            and isinstance(label, str)
            and label.strip()
            and len(label) <= 128
        }

    def _save_legend_labels(self) -> None:
        self._settings.setValue("data/legendLabels", json.dumps(self._legend_labels))

    def _refresh_legend_label_inputs(self) -> None:
        for combo, editor in (
            (self.column_combo, self.legend_label_input),
            (self.column_combo2, self.legend_label_input2),
        ):
            editor.blockSignals(True)
            editor.setText(self._legend_labels.get(combo.currentText(), ""))
            editor.blockSignals(False)

    def _on_legend_label_changed(self, *, secondary: bool) -> None:
        combo = self.column_combo2 if secondary else self.column_combo
        editor = self.legend_label_input2 if secondary else self.legend_label_input
        column = combo.currentText()
        if not column:
            return
        label = editor.text().strip()[:128]
        editor.setText(label)
        if label:
            self._legend_labels[column] = label
        else:
            self._legend_labels.pop(column, None)
        self._save_legend_labels()
        self._replot()
        self.project_changed.emit()
        self._emit_if_loaded()

    def _legend_label_for(self, column: str) -> str:
        return self._legend_labels.get(column, column)

    def _saved_line_width_preference(self) -> float:
        return _saved_line_width(self._settings.value("data/lineWidth", DEFAULT_LINE_WIDTH))

    def _restore_line_width_preference(self) -> None:
        # Recovery and spin-box clamping must not rewrite the saved preference.
        suspended = self._suspend_preferences
        self._suspend_preferences = True
        try:
            self.lw_spinner.setValue(self._saved_line_width_preference())
        finally:
            self._suspend_preferences = suspended

    def _load_column_colors(self) -> dict[str, str]:
        saved = self._settings.value("data/colorsByColumn", "{}")
        try:
            values = json.loads(str(saved))
        except (TypeError, ValueError):
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(column): str(color)
            for column, color in values.items()
            if isinstance(column, str) and QColor(str(color)).isValid()
        }

    def _save_column_colors(self) -> None:
        self._settings.setValue("data/colorsByColumn", json.dumps(self._column_colors))

    def _apply_mapped_column_colors(self) -> None:
        primary = self.column_combo.currentText()
        secondary = self.column_combo2.currentText()
        if primary in self._column_colors:
            self._trace_color = self._column_colors[primary]
        if secondary in self._column_colors:
            self._trace_color2 = self._column_colors[secondary]
        self._colour_preview.setStyleSheet(
            f"background: {self._trace_color}; border: 1px solid #888;"
        )
        self._colour_preview2.setStyleSheet(
            f"background: {self._trace_color2}; border: 1px solid #888;"
        )

    def _on_linewidth_changed(self, _) -> None:
        if not self._suspend_preferences:
            self._settings.setValue("data/lineWidth", self.lw_spinner.value())
        self._replot()
        self._emit_if_loaded()

    def _emit_if_loaded(self) -> None:
        """Re-emit data_loaded so other tabs pick up style changes."""
        if not self._df.empty and self._time_col:
            self.data_loaded.emit(self._df, self._time_col)

    def _on_choose_colour(self) -> None:
        colour = QColorDialog.getColor(QColor(self._trace_color), self, "Choose Trace Colour")
        if colour.isValid():
            self._trace_color = colour.name()
            self._settings.setValue("data/traceColor", self._trace_color)
            if self.column_combo.currentText():
                self._column_colors[self.column_combo.currentText()] = self._trace_color
                self._save_column_colors()
            self._colour_preview.setStyleSheet(
                f"background: {self._trace_color}; border: 1px solid #888;"
            )
            self._replot()
            self._emit_if_loaded()

    def _on_choose_colour2(self) -> None:
        colour = QColorDialog.getColor(QColor(self._trace_color2), self, "Choose 2nd Trace Colour")
        if colour.isValid():
            self._trace_color2 = colour.name()
            self._settings.setValue("data/traceColor2", self._trace_color2)
            if self.column_combo2.currentText():
                self._column_colors[self.column_combo2.currentText()] = self._trace_color2
                self._save_column_colors()
            self._colour_preview2.setStyleSheet(
                f"background: {self._trace_color2}; border: 1px solid #888;"
            )
            self._replot()
            self._emit_if_loaded()

    def _on_overlay_toggled(self, checked: bool) -> None:
        if not self._suspend_preferences:
            self._settings.setValue("data/overlay", checked)
        self.column_combo2.setEnabled(checked)
        self.btn_colour2.setEnabled(checked)
        self.legend_label_input2.setEnabled(checked)
        self._replot()
        self.project_changed.emit()
        self._emit_if_loaded()

    def _on_y_mode_changed(self) -> None:
        fixed = self.radio_fixed.isChecked()
        self.y_min_input.setEnabled(fixed)
        self.y_max_input.setEnabled(fixed)
        self._replot()
        self.project_changed.emit()
        self._emit_if_loaded()

    def _y_limits(self) -> tuple[float | None, float | None]:
        if self.radio_auto.isChecked():
            return None, None
        try:
            return float(self.y_min_input.text()), float(self.y_max_input.text())
        except ValueError:
            return None, None

    def _on_zscore(self) -> None:
        if self._df.empty or self._time_col is None:
            QMessageBox.warning(self, "No data", "Load a data file first.")
            return
        col = self.column_combo.currentText()
        if not col:
            return
        try:
            start_s = float(self.z_start_input.text())
            end_s = float(self.z_end_input.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Enter valid baseline start and end times.")
            return
        try:
            df, result = calculate_z_score(self._df, self._time_col, col, start_s, end_s)
        except ValueError as e:
            QMessageBox.critical(self, "Z-Score Error", str(e))
            return

        self._df = df
        self._zscore_recipe = {
            "source_column": col,
            "baseline_start_s": start_s,
            "baseline_end_s": end_s,
            "result_column": result.z_col,
            "baseline_mean": float(result.baseline_mean),
            "baseline_std": float(result.baseline_std),
        }
        # Add new column to both combos if not already there
        self._zscore_steps.append(dict(self._zscore_recipe))
        for combo in (self.column_combo, self.column_combo2):
            if combo.findText(result.z_col) == -1:
                combo.addItem(result.z_col)
        self.column_combo.setCurrentText(result.z_col)
        self._replot()
        self.data_loaded.emit(self._df, self._time_col)

    def _replot(self) -> None:
        col = self.column_combo.currentText()
        if self._df.empty or not col or self._time_col is None:
            return
        if col not in self._df.columns:
            return

        overlay = self.chk_overlay.isChecked()
        col2 = self.column_combo2.currentText()
        # Both traces drawn semi-transparent so neither hides the other.
        alpha = 0.5 if (overlay and col2 and col2 in self._df.columns) else 1.0
        lw = self.lw_spinner.value()
        time = self._df[self._time_col].to_numpy()

        traces = [
            Trace(
                time,
                self._df[col].to_numpy(),
                label=self._legend_label_for(col),
                color=self._trace_color,
                linewidth=lw,
                alpha=alpha,
                source_column=col,
            )
        ]
        if overlay and col2 and col2 in self._df.columns:
            traces.append(
                Trace(
                    time,
                    self._df[col2].to_numpy(),
                    label=self._legend_label_for(col2),
                    color=self._trace_color2,
                    linewidth=lw,
                    alpha=alpha,
                    source_column=col2,
                )
            )

        y_min, y_max = self._y_limits()
        self.plot.plot_traces(traces, y_min=y_min, y_max=y_max)

    # ------------------------------------------------------------------
    # Public accessors (used by main_window to pass state to other tabs)
    # ------------------------------------------------------------------

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._df

    @property
    def time_col(self) -> str | None:
        return self._time_col

    @property
    def selected_column(self) -> str:
        return self.column_combo.currentText()

    @property
    def trace_color(self) -> str:
        return self._trace_color

    @property
    def overlay_enabled(self) -> bool:
        """True when the 2nd signal overlay is on and points at a real column."""
        col2 = self.column_combo2.currentText()
        return self.chk_overlay.isChecked() and bool(col2) and col2 in self._df.columns

    @property
    def selected_column2(self) -> str:
        return self.column_combo2.currentText() if self.overlay_enabled else ""

    @property
    def trace_color2(self) -> str:
        return self._trace_color2

    @property
    def column_colors(self) -> dict[str, str]:
        return dict(self._column_colors)

    @property
    def legend_labels(self) -> dict[str, str]:
        return dict(self._legend_labels)

    @property
    def linewidth(self) -> float:
        return self.lw_spinner.value()

    def y_limits(self) -> tuple[float | None, float | None]:
        return self._y_limits()

    @property
    def offset_s(self) -> float:
        try:
            return float(self.offset_input.text())
        except ValueError:
            return 0.0

    @property
    def data_path(self) -> str | None:
        return self._data_path

    @property
    def trace_transform(self) -> str:
        return "raw" if self.transform_combo.currentText() == "Raw values" else "subtract_first"

    @property
    def zscore_recipe(self) -> dict[str, object] | None:
        if not self._zscore_steps:
            return None
        return {**deepcopy(self._zscore_steps[-1]), "steps": deepcopy(self._zscore_steps)}

    def apply_project_style(self, settings: dict) -> None:
        self._suspend_preferences = True
        try:
            self.time_unit_combo.setCurrentText(str(settings.get("time_unit", "Auto-detect")))
            self.lw_spinner.setValue(float(settings.get("linewidth", 1.5)))
            self.chk_overlay.setChecked(bool(settings.get("overlay", False)))
            transform = settings.get("transform", "subtract_first")
            self.transform_combo.setCurrentText(
                "Raw values" if transform == "raw" else "Subtract clip-start value"
            )
            self._trace_color = str(settings.get("color", self._trace_color))
            self._trace_color2 = str(settings.get("color2", self._trace_color2))
            labels = settings.get("legend_labels")
            if isinstance(labels, dict):
                self._legend_labels = {
                    str(column): str(label).strip()[:128]
                    for column, label in labels.items()
                    if isinstance(column, str) and isinstance(label, str) and label.strip()
                }
            self._colour_preview.setStyleSheet(
                f"background: {self._trace_color}; border: 1px solid #888;"
            )
            self._colour_preview2.setStyleSheet(
                f"background: {self._trace_color2}; border: 1px solid #888;"
            )
            limits = settings.get("y_limits", [None, None])
            fixed = settings.get("y_mode") == "fixed"
            self.radio_fixed.setChecked(fixed)
            self.radio_auto.setChecked(not fixed)
            self.y_min_input.setText("" if limits[0] is None else str(limits[0]))
            self.y_max_input.setText("" if limits[1] is None else str(limits[1]))
        finally:
            self._suspend_preferences = False
        self._refresh_legend_label_inputs()
        self._replot()

    def select_project_columns(self, column: str, column2: str = "") -> None:
        """Select project channels without replacing the user's reusable defaults."""
        self._suspend_preferences = True
        try:
            if column and self.column_combo.findText(column) >= 0:
                self.column_combo.setCurrentText(column)
            if column2 and self.column_combo2.findText(column2) >= 0:
                self.column_combo2.setCurrentText(column2)
        finally:
            self._suspend_preferences = False
        self._refresh_legend_label_inputs()
        self._replot()

    def apply_appearance_settings(self, settings: dict) -> None:
        """Apply and persist recording-independent trace appearance settings."""
        channels = [str(value) for value in settings.get("preferred_channels", [])]
        colors = {str(key): str(value) for key, value in settings["colors_by_column"].items()}
        self._column_colors = colors
        self._legend_labels = {
            str(key): str(value).strip()
            for key, value in settings.get("legend_labels", {}).items()
            if str(value).strip()
        }
        self._save_column_colors()
        self._save_legend_labels()
        if channels:
            self._settings.setValue("data/preferredColumn1", channels[0])
        if len(channels) > 1:
            self._settings.setValue("data/preferredColumn2", channels[1])
        primary = (
            channels[0]
            if channels and self.column_combo.findText(channels[0]) >= 0
            else self.selected_column
        )
        secondary = (
            channels[1]
            if len(channels) > 1 and self.column_combo2.findText(channels[1]) >= 0
            else self.column_combo2.currentText()
        )
        color = colors.get(primary, self._trace_color)
        color2 = colors.get(secondary, self._trace_color2)
        transform = str(settings["transform"])
        linewidth = float(settings["line_width"])
        overlay = bool(settings["overlay"])
        self._settings.setValue("data/traceColor", color)
        self._settings.setValue("data/traceColor2", color2)
        self._settings.setValue("data/lineWidth", linewidth)
        self._settings.setValue("data/overlay", overlay)
        self._settings.setValue("data/transform", transform)
        self.apply_project_style(
            {
                "color": color,
                "color2": color2,
                "time_unit": self.time_unit_combo.currentText(),
                "y_mode": "fixed" if self.radio_fixed.isChecked() else "auto",
                "y_limits": list(self.y_limits()),
                "linewidth": linewidth,
                "overlay": overlay,
                "transform": transform,
            }
        )
        if channels:
            self.select_project_columns(channels[0], channels[1] if len(channels) > 1 else "")
        self._replot()
        self._emit_if_loaded()
        self.project_changed.emit()

    def recreate_zscore(self, recipe: dict[str, object]) -> None:
        steps = recipe.get("steps")
        if steps is not None:
            if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
                raise ValueError("Invalid z-score recipe steps.")
            for step in steps:
                self.recreate_zscore(step)
            return
        source = str(recipe["source_column"])
        if source not in self._df.columns or self._time_col is None:
            raise ValueError(f"Z-score source column is missing: {source}")
        start = float(str(recipe["baseline_start_s"]))
        end = float(str(recipe["baseline_end_s"]))
        self._df, result = calculate_z_score(self._df, self._time_col, source, start, end)
        self._zscore_recipe = {
            **recipe,
            "baseline_mean": float(result.baseline_mean),
            "baseline_std": float(result.baseline_std),
        }
        self._zscore_steps.append(dict(self._zscore_recipe))
        for combo in (self.column_combo, self.column_combo2):
            if combo.findText(result.z_col) < 0:
                combo.addItem(result.z_col)

    def reset_to_preferences(self) -> None:
        self._settings.sync()
        self._df = pd.DataFrame()
        self._raw_df = pd.DataFrame()
        self._time_col = None
        self._data_path = None
        self.source_fingerprint = None
        self._zscore_recipe = None
        self._zscore_steps = []
        self.time_column_combo.clear()
        self.column_combo.clear()
        self.column_combo2.clear()
        self.plot.clear()
        self.offset_input.setText("0.0")
        self._trace_color = str(self._settings.value("data/traceColor", "#000000"))
        self._trace_color2 = str(self._settings.value("data/traceColor2", "#d62728"))
        self._column_colors = self._load_column_colors()
        self._legend_labels = self._load_legend_labels()
        self._refresh_legend_label_inputs()
        self._colour_preview.setStyleSheet(
            f"background: {self._trace_color}; border: 1px solid #888;"
        )
        self._colour_preview2.setStyleSheet(
            f"background: {self._trace_color2}; border: 1px solid #888;"
        )
        self.time_unit_combo.setCurrentText(
            str(self._settings.value("data/timeUnit", "Auto-detect"))
        )
        self._restore_line_width_preference()
        self.chk_overlay.setChecked(
            str(self._settings.value("data/overlay", "false")).lower() == "true"
        )
        transform = str(self._settings.value("data/transform", "subtract_first"))
        self.transform_combo.setCurrentText(
            "Raw values" if transform == "raw" else "Subtract clip-start value"
        )
        self.radio_auto.setChecked(True)
        self.y_min_input.clear()
        self.y_max_input.clear()
