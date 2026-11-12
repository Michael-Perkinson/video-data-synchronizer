import pandas as pd
import pytest

from video_data_sync.data.loader import read_file
from video_data_sync.data.processor import (
    _detect_time_unit_factor,
    calculate_z_score,
    find_time_column,
    is_isosbestic,
    preferred_column,
    preferred_signal_columns,
    prepare_dataframe,
    signal_columns_grouped,
    validate_data_columns,
    window_data,
)


def test_prepare_dataframe_converts_minutes_and_drops_text():
    frame = pd.DataFrame(
        {"Time (min)": [0.0, 0.5, 1.0], "signal": [1, 2, 3], "note": ["a", "b", "c"]}
    )
    prepared, time_col = prepare_dataframe(frame)
    assert time_col == "Time (s)"
    assert prepared["Time (s)"].tolist() == [0.0, 30.0, 60.0]
    assert "note" not in prepared


@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
@pytest.mark.parametrize(
    ("time_column", "timestamps", "expected_seconds"),
    [
        ("Elapsed (min)", [0.0, 5.0, 10.0, 15.0], [0.0, 300.0, 600.0, 900.0]),
        ("Elapsed (s)", [0.0, 0.1, 0.2, 0.3, 0.4], [0.0, 0.1, 0.2, 0.3, 0.4]),
        ("Elapsed (s)", [0.0, 0.1, 0.25, 0.5], [0.0, 0.1, 0.25, 0.5]),
    ],
)
def test_read_file_prepares_arbitrary_numeric_signal_at_any_sample_rate(
    tmp_path, extension, time_column, timestamps, expected_seconds
):
    signal = "arbitrary measurement channel"
    frame = pd.DataFrame(
        {
            time_column: timestamps,
            signal: list(range(1, len(timestamps) + 1)),
            "notes": ["metadata"] * len(timestamps),
        }
    )
    path = tmp_path / f"samples{extension}"
    if extension == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_excel(path, index=False)

    prepared, time_col = prepare_dataframe(read_file(str(path)))

    assert time_col == "Time (s)"
    assert prepared["Time (s)"].tolist() == pytest.approx(expected_seconds)
    assert signal in prepared.columns
    assert "notes" not in prepared.columns
    preferred, other = signal_columns_grouped(prepared, time_col)
    assert signal in preferred + other


def test_prepare_dataframe_rejects_unsupported_time_unit():
    frame = pd.DataFrame({"elapsed_ms": [0.0, 1000.0, 2000.0], "signal": [1.0, 2.0, 3.0]})
    with pytest.raises(ValueError, match="Unsupported time unit"):
        prepare_dataframe(frame, "elapsed_ms", "Fortnights")


@pytest.mark.parametrize(
    ("header", "expected_factor"),
    [
        ("Time (ms)", 0.001),
        ("elapsed_ms", 0.001),
        ("Time (msec)", 0.001),
        ("Time (millisecond)", 0.001),
        ("Elapsed milliseconds", 0.001),
        ("Time (h)", 3600.0),
        ("Time (hr)", 3600.0),
        ("Elapsed hrs", 3600.0),
        ("Time (hour)", 3600.0),
        ("Elapsed hours", 3600.0),
        ("Time (min)", 60.0),
        ("Time (s)", 1.0),
        ("timestamp", 1.0),
    ],
)
def test_header_time_units_convert_to_seconds(header, expected_factor):
    frame = pd.DataFrame({header: [0.0, 1.0, 2.0], "signal": [1.0, 2.0, 3.0]})
    prepared, time_col = prepare_dataframe(frame, header)
    assert time_col == "Time (s)"
    assert prepared["Time (s)"].tolist() == pytest.approx(
        [0.0, expected_factor, 2 * expected_factor]
    )


@pytest.mark.parametrize("header", ["items", "timestamp", "samples", "threshold", "rms"])
def test_loose_substrings_are_not_read_as_ms_or_hours(header):
    assert _detect_time_unit_factor(header) is None


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("Seconds", [0.0, 1.0, 2.0]),
        ("Minutes", [0.0, 60.0, 120.0]),
        ("Milliseconds", [0.0, 0.001, 0.002]),
        ("Hours", [0.0, 3600.0, 7200.0]),
    ],
)
def test_explicit_time_unit_override_ignores_header(unit, expected):
    frame = pd.DataFrame({"Time (min)": [0.0, 1.0, 2.0], "signal": [1.0, 2.0, 3.0]})
    prepared, _ = prepare_dataframe(frame, "Time (min)", unit)
    assert prepared["Time (s)"].tolist() == pytest.approx(expected)


@pytest.mark.parametrize("signal_name", ["HR", "signal_h", "H1", "H2O", "Ch1 ms"])
def test_short_unit_like_signal_names_are_not_selected_as_time(signal_name):
    sample = [0.0, 1.0, 2.0, 3.0]
    frame = pd.DataFrame({"sample": sample, signal_name: [72.0, 70.0, 75.0, 71.0], "x": sample})
    assert find_time_column(frame) == "sample"
    prepared, time_col = prepare_dataframe(frame)
    assert time_col == "Time (s)"
    assert prepared["Time (s)"].tolist() == [0.0, 1.0, 2.0, 3.0]


def test_increasing_hr_column_is_not_auto_selected_as_time():
    frame = pd.DataFrame({"HR": [60.0, 61.0, 63.0, 66.0], "sample": [0.0, 1.0, 2.0, 3.0]})
    assert find_time_column(frame) == "sample"


@pytest.mark.parametrize(
    ("header", "factor"),
    [("Elapsed (ms)", 0.001), ("Elapsed [h]", 3600.0), ("Duration hours", 3600.0)],
)
def test_explicit_ms_and_hour_headers_are_selected_and_converted(header, factor):
    frame = pd.DataFrame({"count": [0.0, 1.0, 2.0], header: [0.0, 2.0, 5.0]})
    assert find_time_column(frame) == header
    prepared, _ = prepare_dataframe(frame)
    assert prepared["Time (s)"].tolist() == pytest.approx([0.0, 2 * factor, 5 * factor])


def test_time_named_column_converts_short_unit_token():
    frame = pd.DataFrame({"Time ms": [0.0, 500.0, 1000.0], "signal": [1.0, 2.0, 3.0]})
    assert find_time_column(frame) == "Time ms"
    prepared, _ = prepare_dataframe(frame)
    assert prepared["Time (s)"].tolist() == pytest.approx([0.0, 0.5, 1.0])


def test_seconds_and_minutes_selection_is_unchanged_and_skips_invalid_candidates():
    valid = pd.DataFrame({"count": [0.0, 1.0, 2.0], "Elapsed (s)": [0.0, 0.4, 1.0]})
    assert find_time_column(valid) == "Elapsed (s)"
    # A unit-labelled column that cannot be a time axis no longer blocks loading.
    invalid_first = pd.DataFrame(
        {"Lag (s)": [3.0, 1.0, 2.0], "Elapsed (min)": [0.0, 0.5, 2.0], "signal": [1.0, 2.0, 4.0]}
    )
    assert find_time_column(invalid_first) == "Elapsed (min)"
    # Seconds/minutes headers keep priority over the added millisecond/hour units.
    mixed = pd.DataFrame({"Duration hours": [0.0, 1.0, 3.0], "Elapsed (s)": [0.0, 0.4, 1.0]})
    assert find_time_column(mixed) == "Elapsed (s)"


def test_find_time_column_and_z_score_validation():
    frame = pd.DataFrame({"Time (s)": [0.0, 1.0, 2.0], "signal": [1.0, 2.0, 3.0]})
    assert find_time_column(frame) == "Time (s)"
    with pytest.raises(ValueError):
        calculate_z_score(frame, "Time (s)", "signal", 1.0, 1.0)


def test_read_file_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        read_file(str(path))


def test_read_file_loads_real_csv_and_xlsx_case_insensitively(tmp_path):
    expected = pd.DataFrame({"Time (s)": [0.0, 1.0], "signal": [2.0, 3.0]})
    csv_path = tmp_path / "trace.CSV"
    xlsx_path = tmp_path / "trace.XLSX"
    expected.to_csv(csv_path, index=False)
    expected.to_excel(xlsx_path, index=False)
    pd.testing.assert_frame_equal(read_file(str(csv_path)), expected)
    pd.testing.assert_frame_equal(read_file(str(xlsx_path)), expected, check_dtype=False)


def test_read_file_reports_corrupt_supported_file(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_text("not an Excel workbook", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not read"):
        read_file(str(path))


@pytest.mark.parametrize(
    "times, message",
    [([0.0, float("nan"), 2.0], "non-finite"), ([0.0, 1.0, 1.0], "strictly increasing")],
)
def test_prepare_dataframe_rejects_invalid_time(times, message):
    with pytest.raises(ValueError, match=message):
        prepare_dataframe(pd.DataFrame({"Time (s)": times, "signal": [1.0, 2.0, 3.0]}))


def test_selected_data_must_have_finite_numeric_values():
    frame = pd.DataFrame({"empty": [float("nan")], "text": ["x"]})
    with pytest.raises(ValueError, match="no finite"):
        validate_data_columns(frame, ["empty"])
    with pytest.raises(ValueError, match="numeric"):
        validate_data_columns(frame, ["text"])


def test_window_data_returns_empty_when_data_does_not_overlap():
    frame = pd.DataFrame({"Time (s)": [10.0, 11.0], "signal": [1.0, 2.0]})
    assert window_data(frame, "Time (s)", 0.0, 1.0).empty


def test_generic_primary_series_are_ranked_ahead_of_neutral_and_control_columns():
    frame = pd.DataFrame(
        {
            "Time (s)": [0.0, 1.0],
            "Reference": [3.0, 4.0],
            "temperature": [20.0, 21.0],
            "Primary response": [1.0, 2.0],
            "Sensor 2": [5.0, 6.0],
        }
    )

    preferred, other = signal_columns_grouped(frame, "Time (s)")
    assert preferred == ["Primary response", "Sensor 2", "temperature"]
    assert other == ["Reference"]
    assert preferred_signal_columns(frame, "Time (s)") == ["Primary response", "Sensor 2"]


def test_derived_series_keep_priority_and_existing_channel_names_remain_supported():
    frame = pd.DataFrame(
        {
            "Time (s)": [0.0, 1.0],
            "control 405": [1.0, 1.0],
            "signal 470": [2.0, 3.0],
            "dFoF_470": [0.1, 0.2],
            "speed": [4.0, 5.0],
        }
    )

    assert preferred_column(frame, "Time (s)") == "dFoF_470"
    assert preferred_signal_columns(frame, "Time (s)", n=4) == [
        "dFoF_470",
        "signal 470",
        "speed",
        "control 405",
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("reference", True),
        ("baseline sensor", True),
        ("control 405", True),
        ("reference_speed", True),
        ("sensor 4050", False),
    ],
)
def test_control_detection_uses_words_and_complete_wavelengths(name, expected):
    assert is_isosbestic(name) is expected


def test_separator_delimited_and_common_measurement_names_are_preferred():
    frame = pd.DataFrame(
        {
            "time": [0.0, 1.0],
            "notes_index": [1.0, 2.0],
            "pressure_kpa": [100.0, 101.0],
            "primary_response": [4.0, 5.0],
            "velocity-mm-s": [2.0, 3.0],
            "reference_speed": [1.0, 1.0],
        }
    )

    preferred, other = signal_columns_grouped(frame, "time")
    assert preferred == ["primary_response", "pressure_kpa", "velocity-mm-s"]
    assert other == ["notes_index", "reference_speed"]
