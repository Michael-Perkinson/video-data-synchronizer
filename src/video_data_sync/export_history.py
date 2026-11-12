"""Durable per-export records, independent of the current project and preferences."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import platform
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import QStandardPaths

from video_data_sync import __version__


def history_directory() -> Path:
    return (
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        / "export-history"
    )


def write_record(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fingerprint(path: str, cancel=None) -> dict:
    from video_data_sync.video_processing.exporter import ExportCancelled

    source = Path(path).resolve()
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if cancel and cancel():
                raise ExportCancelled("Export cancelled while identifying source files.")
            digest.update(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"Source changed while being read: {source.name}")
    return {
        "path": str(source),
        "name": source.name,
        "sha256": digest.hexdigest(),
        "size_bytes": after.st_size,
        "modified_ns": after.st_mtime_ns,
    }


def _json_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(v) for v in value]
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return value


def record_export(function):
    """Capture effective arguments before work and retain failed/cancelled attempts too."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def recorded(*args, **kwargs):
        from video_data_sync.video_processing.exporter import ExportCancelled

        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        run_id = uuid4().hex
        output = Path(values["output_path"]).resolve()
        record_path = history_directory() / f"{run_id}.run.json"
        sidecar = output.with_name(f"{output.stem}.{run_id}.run.json")
        ignored = {"df", "progress_callback", "cancel_callback", "audit_context", "run_details"}
        document = {
            "schema_version": 1,
            "kind": "video-data-sync-export-run",
            "run_id": run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "status": "running",
            "application_version": __version__,
            "opencv_version": cv2.__version__,
            "platform": platform.platform(),
            "output_path": str(output),
            "record_path": str(record_path),
            "sidecar_path": str(sidecar),
            "settings": _json_value({k: v for k, v in values.items() if k not in ignored}),
            "context": _json_value(values.get("audit_context") or {}),
            "sources": {},
            "resolved": {},
        }
        # A durable initial record is mandatory: do not silently export without a log.
        write_record(record_path, document)
        try:
            cancel = values.get("cancel_callback")
            document["sources"]["video"] = fingerprint(values["video_path"], cancel)
            data_path = document["context"].get("data_path")
            if data_path:
                document["sources"]["data"] = fingerprint(data_path, cancel)
                loaded = document["context"].get("loaded_data_fingerprint")
                if loaded and loaded["sha256"] != document["sources"]["data"]["sha256"]:
                    raise ValueError(
                        "The data file changed since loading. Reload it before export."
                    )
            df = values["df"]
            document["data_snapshot"] = {
                "columns": list(df.columns),
                "rows": len(df),
                "sha256": hashlib.sha256(
                    df.to_csv(index=True, lineterminator="\n", float_format="%.17g").encode("utf-8")
                ).hexdigest(),
            }
            values["run_details"] = document["resolved"]
            document["resolved"]["sources"] = document["sources"]
            write_record(record_path, document)
            result = function(*bound.args, **bound.kwargs)
            document["output_fingerprint"] = fingerprint(str(output))
            document["status"] = "succeeded"
        except BaseException as exc:
            document["status"] = "cancelled" if isinstance(exc, ExportCancelled) else "failed"
            document["error"] = str(exc)
            document["finished_at"] = datetime.now(UTC).isoformat()
            try:
                write_record(record_path, _json_value(document))
            except OSError as log_error:
                exc.add_note(f"Could not finalize export history: {log_error}")
            raise
        document["finished_at"] = datetime.now(UTC).isoformat()
        history_error = None
        try:
            write_record(record_path, _json_value(document))
        except OSError as exc:
            history_error = exc
        try:
            write_record(sidecar, _json_value(document))
        except OSError as exc:
            document["sidecar_error"] = str(exc)
            if history_error is None:
                try:
                    write_record(record_path, _json_value(document))
                except OSError:
                    pass
            raise OSError(
                f"Video saved to {output}, but its adjacent settings record could not be saved. "
                + (
                    f"The complete record is in Export History: {record_path}"
                    if history_error is None
                    else f"The history update also failed: {history_error}. "
                    f"The initial run record is at {record_path}."
                )
            ) from exc
        if history_error is not None:
            raise OSError(
                f"Video saved to {output}. Settings saved to {sidecar}, but the local "
                f"Export History could not be updated: {history_error}"
            ) from history_error
        return result

    return recorded


def load_records(directory: Path | None = None) -> list[dict]:
    records = []
    for path in (directory or history_directory()).glob("*.run.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if _valid_record(doc):
                records.append(doc)
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda record: record.get("started_at", ""), reverse=True)


def _valid_record(doc: object) -> bool:
    """Ignore malformed or unsupported records without breaking the history screen."""
    if not isinstance(doc, dict) or doc.get("kind") != "video-data-sync-export-run":
        return False
    if doc.get("schema_version") != 1:
        return False
    if not all(
        isinstance(doc.get(key), str)
        for key in ("run_id", "started_at", "status", "output_path", "record_path")
    ):
        return False
    if not all(
        isinstance(doc.get(key), dict) for key in ("settings", "context", "sources", "resolved")
    ):
        return False
    if not all(
        isinstance(source, dict) and isinstance(source.get("path"), str)
        for source in doc["sources"].values()
    ):
        return False
    project = doc["context"].get("project", {})
    if not isinstance(project, dict):
        return False
    return (
        isinstance(project.get("video", {}), dict)
        and isinstance(project.get("data", {}), dict)
        and isinstance(project.get("video", {}).get("colour", {}), dict)
    )
