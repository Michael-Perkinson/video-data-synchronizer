import json
import os

import pandas as pd

_SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".video_data_sync", "settings.json")


def _ensure_settings_dir() -> None:
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)


def read_folder_path() -> str:
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return os.path.expanduser("~")
    if isinstance(data, dict):
        folder_path = data.get("folder_path")
        if isinstance(folder_path, str) and folder_path:
            return folder_path
    return os.path.expanduser("~")


def write_folder_path(path: str) -> None:
    _ensure_settings_dir()
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["folder_path"] = path
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read_file(file_path: str) -> pd.DataFrame:
    """Load a CSV or Excel file. Raises ValueError for unsupported formats."""
    extension = os.path.splitext(file_path)[1].lower()
    try:
        if extension == ".csv":
            return pd.read_csv(file_path)
        if extension == ".xlsx":
            return pd.read_excel(file_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {extension or 'data'} file: {exc}") from exc
    raise ValueError(f"Unsupported file type: {extension}")
