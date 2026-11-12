"""
Data processing: time-column detection, unit conversion, z-score calculation.

All functions are pure (no GUI imports, no side effects beyond returning values).
"""

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Time column detection
# ---------------------------------------------------------------------------

_TIME_UNIT_FACTORS: dict[str, float] = {
    "s": 1 / 60,
    "second": 1 / 60,
    "sec": 1 / 60,
    "minute": 1.0,
    "min": 1.0,
    "millisecond": 1 / 60_000,
    "msec": 1 / 60_000,
    "ms": 1 / 60_000,
    "hour": 60.0,
    "hr": 60.0,
    "h": 60.0,
}

# Explicit time-unit overrides and their factor to seconds ("Auto-detect" reads the header).
TIME_UNIT_SECONDS: dict[str, float] = {
    "Seconds": 1.0,
    "Minutes": 60.0,
    "Milliseconds": 0.001,
    "Hours": 3600.0,
}


def _column_series(df: pd.DataFrame, column: object) -> pd.Series:
    """Return one named column as a Series; reject duplicate-name ambiguity."""
    values = df[column]
    if not isinstance(values, pd.Series):
        raise ValueError("Column names must be unique.")
    return values


def _has_unit_token(lower: str, unit: str) -> bool:
    # Any non-letter is a boundary, so "_" counts too (e.g. "t_min", "time_sec").
    return re.search(r"(?<![a-z])" + unit + r"s?(?![a-z])", lower) is not None


def _detect_time_unit_factor(column_name: str) -> float | None:
    """Return conversion factor to minutes if a time unit is found in the column name."""
    lower = column_name.lower()
    for unit, factor in _TIME_UNIT_FACTORS.items():
        if _has_unit_token(lower, unit):
            return factor
    return None


# Time-column *selection* by unit is stricter than conversion: short tokens such as
# "h", "hr" and "ms" often name signals (e.g. "HR" heart rate), so they only select a
# column as an explicit bracketed unit at the end of the header.
_LEGACY_SELECTION_UNITS = ("s", "second", "sec", "minute", "min")
_WORD_SELECTION_UNITS = ("millisecond", "msec", "hour")
_BRACKETED_SHORT_UNIT = re.compile(r"[(\[]\s*(?:ms|hrs?|h)\s*[)\]]\s*$")


def _selects_by_legacy_unit(column_name: str) -> bool:
    lower = column_name.lower()
    return any(_has_unit_token(lower, unit) for unit in _LEGACY_SELECTION_UNITS)


def _selects_by_added_unit(column_name: str) -> bool:
    lower = column_name.lower()
    if any(_has_unit_token(lower, unit) for unit in _WORD_SELECTION_UNITS):
        return True
    return _BRACKETED_SHORT_UNIT.search(lower) is not None


def _is_valid_time_series(series: pd.Series) -> bool:
    try:
        validate_time_series(series)
    except ValueError:
        return False
    return True


def _is_evenly_spaced(series: pd.Series, tolerance: float = 0.05, sample: int = 50) -> bool:
    """Return True if series values are approximately evenly spaced."""
    diffs = series.dropna().diff().dropna()
    if diffs.empty:
        return False
    subset = diffs.iloc[:sample]
    modal_diff = subset.mode().iloc[0]
    if modal_diff == 0:
        return False
    return (abs(subset - modal_diff) < abs(modal_diff) * tolerance).mean() >= 0.9


def find_time_column(df: pd.DataFrame) -> str | None:
    """
    Return the name of the time column, or None if one cannot be identified.

    Strategy:
    1. First numeric column with 'time' in its name (case-insensitive).
    2. First numeric, strictly increasing column with a seconds/minutes unit in its
       name; then the same for milliseconds/hours, which count only as full words
       ("millisecond(s)", "msec", "hour(s)") or as a bracketed unit at the end of the
       name ("(ms)", "[h]", "(hr)"). Columns that are not valid times are skipped.
    3. First numeric, evenly-spaced column (fallback).
    """
    for col in df.columns:
        series = _column_series(df, col)
        if "time" in str(col).lower() and pd.api.types.is_numeric_dtype(series):
            return str(col)

    for selects in (_selects_by_legacy_unit, _selects_by_added_unit):
        for col in df.columns:
            series = _column_series(df, col)
            if (
                pd.api.types.is_numeric_dtype(series)
                and selects(str(col))
                and _is_valid_time_series(series)
            ):
                return str(col)

    for col in df.columns:
        series = _column_series(df, col)
        if pd.api.types.is_numeric_dtype(series) and _is_evenly_spaced(series):
            return str(col)

    return None


def to_seconds(df: pd.DataFrame, time_col: str) -> pd.Series:
    """
    Return the time column converted to seconds.

    Converts from whatever unit is detected in the column name.
    If no unit is detected the values are assumed to already be in seconds.
    """
    series = _column_series(df, time_col)
    factor = _detect_time_unit_factor(time_col)
    if factor is None:
        return series.copy()
    # factor is "to minutes", so multiply by 60 to get seconds
    return series * (factor * 60)


# ---------------------------------------------------------------------------
# DataFrame preparation
# ---------------------------------------------------------------------------


def prepare_dataframe(
    df: pd.DataFrame, time_column: str | None = None, time_unit: str = "Auto-detect"
) -> tuple[pd.DataFrame, str | None]:
    """
    Normalise the dataframe for use in the app.

    Returns (cleaned_df, time_col_name).
    - time_col is the name of the time column in the returned df (in seconds).
    - Non-numeric columns other than the time column are dropped.
    """
    if time_unit != "Auto-detect" and time_unit not in TIME_UNIT_SECONDS:
        raise ValueError(f"Unsupported time unit: {time_unit!r}")
    df = df.copy()
    df.columns = df.columns.map(str)
    if not df.columns.is_unique:
        raise ValueError("Column names must be unique.")
    time_col = time_column or find_time_column(df)

    if time_col is not None:
        df = df.copy()
        if time_col not in df:
            raise ValueError(f"Time column not found: {time_col}")
        time_values = _column_series(df, time_col)
        validate_time_series(time_values)
        df[time_col] = (
            to_seconds(df, time_col)
            if time_unit == "Auto-detect"
            else time_values * TIME_UNIT_SECONDS[time_unit]
        )
        if time_col != "Time (s)" and "Time (s)" in df:
            preserved = "Time (s) [original]"
            while preserved in df:
                preserved += " [original]"
            df = df.rename(columns={"Time (s)": preserved})
        # Rename to a standard name so the rest of the app doesn't need to know the original
        df = df.rename(columns={time_col: "Time (s)"})
        time_col = "Time (s)"
        validate_time_series(_column_series(df, time_col))

    # Keep only numeric columns (time col is already numeric)
    numeric_cols: list[str] = []
    for column in df.columns:
        name = str(column)
        if pd.api.types.is_numeric_dtype(_column_series(df, name)):
            numeric_cols.append(name)
    numeric_df = df.loc[:, numeric_cols]
    if not isinstance(numeric_df, pd.DataFrame):
        raise ValueError("Numeric columns could not be selected as a data table.")
    df = numeric_df.copy()

    return df, time_col


def validate_time_series(time: pd.Series) -> None:
    """Require a numeric, finite, strictly increasing time series."""
    if not pd.api.types.is_numeric_dtype(time):
        raise ValueError("The selected time column must be numeric.")
    values = time.to_numpy(dtype=float)
    if values.size < 2:
        raise ValueError("The time column must contain at least two values.")
    if not np.all(np.isfinite(values)):
        raise ValueError("The time column contains missing or non-finite values.")
    if not np.all(np.diff(values) > 0):
        raise ValueError("The time column must be strictly increasing without duplicates.")


def validate_data_columns(df: pd.DataFrame, columns: list[str]) -> None:
    """Require selected trace columns to exist, be numeric, and contain finite data."""
    for column in columns:
        if column not in df.columns:
            raise ValueError(f"Selected data column not found: {column}")
        series = _column_series(df, column)
        if not pd.api.types.is_numeric_dtype(series):
            raise ValueError(f"Selected data column must be numeric: {column}")
        values = series.to_numpy(dtype=float)
        if values.size == 0 or not np.any(np.isfinite(values)):
            raise ValueError(f"Selected data column has no finite values: {column}")


def data_columns(df: pd.DataFrame, time_col: str | None) -> list[str]:
    """Return all columns except the time column."""
    return [c for c in df.columns if c != time_col]


# Strong derived-data names are ranked first. The wavelength and assay terms
# retain compatibility with existing datasets, while the remaining patterns
# cover common generic time-series exports.
_DERIVED_SIGNAL_PATTERNS = (
    r"df[\s_/]?f|dfof|delta[\s_]?f",
    r"z[\s_-]?score|zscore",
    r"\bnormalized?\b|\bnormalised?\b",
    r"\bcorrected\b|\bprocessed\b",
)
_PRIMARY_SIGNAL_PATTERNS = (
    r"(?<!\d)(?:465|470|490|560)(?!\d)",
    r"\b(?:primary|signal|response|measurement|value|amplitude|intensity|activity|trace)\b",
    r"\b(?:sensor|channel|output)[\s_-]*(?:\d+|[a-z])?\b",
    r"\bgcamp\b|\bfluorescence\b",
)
_MEASUREMENT_SIGNAL_PATTERNS = (
    r"\b(?:temperature|temp|force|pressure|speed|velocity|position|distance|displacement)\b",
    r"\b(?:acceleration|angle|torque|voltage|current|power|frequency|count|rate)\b",
)
_CONTROL_SIGNAL_PATTERNS = (
    r"(?<!\d)(?:405|410|415)(?!\d)",
    r"\b(?:control|reference|ref|baseline|background|blank)\b",
    r"\b(?:isosbestic|isos)\b",
)


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    normalized = re.sub(r"[_-]+", " ", name)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def is_isosbestic(col_name: str) -> bool:
    """Return whether a column is likely to be a reference or control series."""
    return _matches_any(col_name, _CONTROL_SIGNAL_PATTERNS)


def signal_columns_grouped(df: pd.DataFrame, time_col: str | None) -> tuple[list[str], list[str]]:
    """Group likely primary series ahead of neutral and control choices."""
    cols = data_columns(df, time_col)
    if not cols:
        return [], []

    derived, primary, measurement, neutral, control = [], [], [], [], []
    for col in cols:
        if is_isosbestic(col):
            control.append(col)
        elif _matches_any(col, _DERIVED_SIGNAL_PATTERNS):
            derived.append(col)
        elif _matches_any(col, _PRIMARY_SIGNAL_PATTERNS):
            primary.append(col)
        elif _matches_any(col, _MEASUREMENT_SIGNAL_PATTERNS):
            measurement.append(col)
        else:
            neutral.append(col)

    return derived + primary + measurement, neutral + control


def preferred_column(df: pd.DataFrame, time_col: str | None) -> str | None:
    """Return the most likely primary numeric series to plot by default."""
    preferred, other = signal_columns_grouped(df, time_col)
    ranked = preferred + other
    return ranked[0] if ranked else None


def preferred_signal_columns(df: pd.DataFrame, time_col: str | None, n: int = 2) -> list[str]:
    """Return up to n initial series, placing controls after other choices."""
    preferred, other = signal_columns_grouped(df, time_col)
    return (preferred + other)[:n]


# ---------------------------------------------------------------------------
# Z-score
# ---------------------------------------------------------------------------


@dataclass
class ZScoreResult:
    z_col: str  # name of the new z-score column added to the dataframe
    baseline_mean: float
    baseline_std: float


def calculate_z_score(
    df: pd.DataFrame,
    time_col: str,
    data_col: str,
    baseline_start_s: float,
    baseline_end_s: float,
) -> tuple[pd.DataFrame, ZScoreResult]:
    """
    Add a z-score column to df computed relative to the baseline window.

    Returns (updated_df, ZScoreResult).
    Raises ValueError if the baseline window is empty or std is zero.
    """
    if not np.isfinite(baseline_start_s) or not np.isfinite(baseline_end_s):
        raise ValueError("Baseline bounds must be finite.")
    if baseline_start_s >= baseline_end_s:
        raise ValueError("Baseline start must be less than baseline end.")

    mask = (df[time_col] >= baseline_start_s) & (df[time_col] < baseline_end_s)
    baseline = df.loc[mask, data_col]

    if baseline.empty:
        raise ValueError("No data points found in the baseline window.")

    if np.isinf(baseline.to_numpy(dtype=float)).any() or baseline.count() < 2:
        raise ValueError("The baseline needs at least two finite samples and no infinite values.")

    mean = baseline.mean()
    std = baseline.std()

    if not np.isfinite(mean) or not np.isfinite(std) or std <= 0:
        raise ValueError("Baseline standard deviation is zero — cannot calculate z-score.")

    z_col = f"{data_col}_zscore"
    df = df.copy()
    df[z_col] = (df[data_col] - mean) / std

    return df, ZScoreResult(z_col=z_col, baseline_mean=mean, baseline_std=std)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


def window_data(
    df: pd.DataFrame,
    time_col: str,
    start_s: float,
    end_s: float,
    offset_s: float = 0.0,
) -> pd.DataFrame:
    """
    Return rows where time falls within [start_s + offset_s, end_s + offset_s].
    """
    t = _column_series(df, time_col)
    mask = (t >= start_s + offset_s) & (t <= end_s + offset_s)
    filtered = df.loc[mask]
    if not isinstance(filtered, pd.DataFrame):
        raise ValueError("Window selection did not produce a data table.")
    return filtered.copy()
