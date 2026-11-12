"""Tests for recording-independent JSON appearance presets."""

import json
from pathlib import Path

import pytest

from video_data_sync.settings.presets import (
    PresetValidationError,
    default_preset,
    load_preset,
    save_preset,
    validate_preset,
)


def test_default_preset_is_complete_and_detached():
    first = default_preset()
    second = default_preset()

    first["traces"]["colors_by_column"]["dFF"] = "#123456"

    assert validate_preset(first)["traces"]["colors_by_column"] == {"dFF": "#123456"}
    assert second["traces"]["colors_by_column"] == {}
    assert first["traces"]["legend_labels"] == {}
    assert first["composition"]["advanced"]["time_scale_label"] == ""
    assert first["composition"]["advanced"]["max_change_enabled"] is False
    assert first["composition"]["advanced"]["max_change_percent"] == 70.0


def test_legacy_preset_defaults_to_auto_max_change():
    legacy = default_preset()
    advanced = legacy["composition"]["advanced"]
    legacy["traces"].pop("legend_labels")
    advanced.pop("max_change_enabled")
    advanced.pop("max_change_percent")

    loaded = validate_preset(legacy)

    assert loaded["composition"]["advanced"]["max_change_enabled"] is False
    assert loaded["composition"]["advanced"]["max_change_percent"] == 70.0


@pytest.mark.parametrize("value", [0, -1, 101, float("inf")])
def test_invalid_max_change_percent_is_rejected(value):
    preset = default_preset()
    preset["composition"]["advanced"]["max_change_percent"] = value

    with pytest.raises(PresetValidationError, match="max_change"):
        validate_preset(preset)


def test_legacy_absolute_max_change_remains_available_for_migration():
    preset = default_preset()
    advanced = preset["composition"]["advanced"]
    advanced["max_change_enabled"] = True
    advanced["max_change"] = 2.5
    advanced.pop("max_change_percent")

    normalized = validate_preset(preset)["composition"]["advanced"]

    assert normalized["max_change_enabled"] is True
    assert normalized["max_change"] == 2.5
    assert "max_change_percent" not in normalized


def test_round_trip_preserves_generic_settings(tmp_path):
    preset = default_preset()
    preset["traces"].update(
        {
            "colors_by_column": {"465 nm": "#2299cc", "405 nm": "orange"},
            "legend_labels": {"465 nm": "Signal 465", "405 nm": "Control"},
            "preferred_channels": ["465 nm", "405 nm"],
            "line_width": 2.25,
            "transform": "raw",
            "overlay": True,
        }
    )
    preset["composition"].update({"layout": "stacked", "panel_proportions": [7, 3]})
    preset["composition"]["advanced"].update(
        {
            "time_scale_label": "10 s",
            "signal_scale_label": "0.5 ΔF/F",
            "max_change_enabled": True,
            "max_change_percent": 50.0,
        }
    )
    preset["export"] = {"speeds": [0.5, 1, 4], "deflicker": True}
    preset["video_colour"].update({"brightness": -12, "gamma": 1.4, "r_gain": 0.8})
    path = tmp_path / "lab-style.vdsp-preset.json"

    save_preset(path, preset)

    loaded = load_preset(path)
    assert loaded["traces"]["colors_by_column"]["465 nm"] == "#2299cc"
    assert loaded["traces"]["legend_labels"] == {
        "465 nm": "Signal 465",
        "405 nm": "Control",
    }
    assert loaded["composition"]["panel_proportions"] == [7, 3]
    assert loaded["composition"]["advanced"]["signal_scale_label"] == "0.5 ΔF/F"
    assert loaded["composition"]["advanced"]["max_change_enabled"] is True
    assert loaded["composition"]["advanced"]["max_change_percent"] == 50.0
    assert loaded["export"]["speeds"] == [0.5, 1.0, 4.0]
    assert loaded["video_colour"]["brightness"] == -12
    assert json.loads(path.read_text(encoding="utf-8"))["kind"].endswith("preset")


def test_legacy_custom_background_is_normalized_to_white():
    preset = default_preset()
    preset["composition"]["advanced"]["background"] = "#123456"

    loaded = validate_preset(preset)

    assert loaded["composition"]["advanced"]["background"] == "#ffffff"


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("traces", "line_width", float("nan"), "line_width"),
        ("traces", "transform", "zscore", "transform"),
        ("traces", "colors_by_column", {"signal": "not-a-colour"}, "valid colour"),
        ("composition", "layout", "diagonal", "layout"),
        ("export", "speeds", [], "speeds"),
        ("video_colour", "gamma", 0.0, "gamma"),
    ],
)
def test_invalid_values_are_rejected(section, field, value, message):
    preset = default_preset()
    preset[section][field] = value

    with pytest.raises(PresetValidationError, match=message):
        validate_preset(preset)


def test_recording_specific_and_unknown_fields_are_rejected():
    preset = default_preset()
    preset["video_path"] = "/private/session.mp4"

    with pytest.raises(PresetValidationError, match="unknown fields: video_path"):
        validate_preset(preset)

    preset = default_preset()
    preset["traces"]["offset_s"] = 2.5
    with pytest.raises(PresetValidationError, match="unknown fields: offset_s"):
        validate_preset(preset)


def test_bad_json_has_a_helpful_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(PresetValidationError, match=r"line 1, column 19"):
        load_preset(path)


def test_save_is_atomic_and_leaves_existing_file_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "preset.json"
    path.write_text("old contents", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("video_data_sync.settings.presets.os.replace", fail_replace)

    with pytest.raises(OSError, match="replacement failure"):
        save_preset(path, default_preset())

    assert path.read_text(encoding="utf-8") == "old contents"
    assert list(tmp_path.iterdir()) == [path]


def test_all_documented_example_presets_are_importable():
    examples = Path(__file__).parents[1] / "examples" / "presets"
    paths = sorted(examples.glob("*.vdsp-preset.json"))

    assert [path.name for path in paths] == [
        "fiber-photometry.vdsp-preset.json",
        "generic-clean.vdsp-preset.json",
    ]
    loaded = {path.name: load_preset(path) for path in paths}
    photometry = loaded["fiber-photometry.vdsp-preset.json"]
    generic = loaded["generic-clean.vdsp-preset.json"]

    assert photometry["traces"]["preferred_channels"] == [
        "dFoF_470",
        "Control_405",
    ]
    assert photometry["traces"]["transform"] == "raw"
    assert photometry["composition"]["advanced"]["signal_scale_label"] == "dF/F"
    assert generic == default_preset()
