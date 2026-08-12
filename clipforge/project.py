"""Versioned, external-media project/session files.

Project files contain editing intent and media identities, never media bytes.
The format is deliberately small and JSON-only so it can be inspected,
backed up, and migrated without executing project content.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_SCHEMA = "clipforge.project"
PROJECT_VERSION = 1
PROJECT_EXTENSION = ".cfproj"
LEGACY_PROJECT_EXTENSIONS = (".clipforge",)
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_INPUTS = 100


class ProjectError(ValueError):
    """Raised when a project file cannot be safely loaded or written."""


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any, default: str = "", limit: int = 32767) -> str:
    value = str(value if value is not None else default)
    return value[:limit]


def _relative_path(path: Path, project_path: Path | None) -> str:
    if project_path is None:
        return ""
    try:
        return os.path.relpath(path, Path(project_path).resolve().parent)
    except (OSError, ValueError):
        return ""


def media_reference(
    path: str | os.PathLike[str],
    project_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Capture a bounded identity for an external media input."""

    source = Path(path).expanduser().resolve()
    try:
        stat = source.stat()
    except OSError:
        stat = None
    return {
        "path": _text(str(source)),
        "relative_path": _text(
            _relative_path(source, Path(project_path)) if project_path else "",
            limit=1024,
        ),
        "name": _text(source.name, "media", 255),
        "size": max(0, int(stat.st_size)) if stat else 0,
        "mtime_ns": max(0, int(stat.st_mtime_ns)) if stat else 0,
    }


def _normalize_map(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProjectError(f"Project {label} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 512 * 1024:
            raise ProjectError(f"Project {label} is too large")
        return json.loads(encoded)
    except ProjectError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Project {label} contains unsupported values") from exc


def _normalize_input(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectError(f"Project input {index + 1} must be an object")
    path = _text(value.get("path"), limit=32767)
    name = _text(
        value.get("name") or Path(path).name or f"Media {index + 1}",
        limit=255,
    )
    if not path and not name:
        raise ProjectError(f"Project input {index + 1} has no media reference")
    return {
        "path": path,
        "relative_path": _text(value.get("relative_path"), limit=1024),
        "name": name,
        "size": max(0, int(_finite(value.get("size")))),
        "mtime_ns": max(0, int(_finite(value.get("mtime_ns")))),
    }


def normalize_project(raw: Any) -> dict[str, Any]:
    """Validate and canonicalize a project document without filesystem access."""

    if not isinstance(raw, dict) or raw.get("schema") != PROJECT_SCHEMA:
        raise ProjectError("This is not a ClipForge project file")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProjectError("Project version is invalid")
    if version > PROJECT_VERSION:
        raise ProjectError(
            f"Project schema v{version} is newer than supported v{PROJECT_VERSION}"
        )
    inputs = raw.get("inputs")
    if not isinstance(inputs, list) or len(inputs) > MAX_INPUTS:
        raise ProjectError(f"Project inputs must contain at most {MAX_INPUTS} entries")
    normalized_inputs = [
        _normalize_input(item, index) for index, item in enumerate(inputs)
    ]
    return {
        "schema": PROJECT_SCHEMA,
        "version": PROJECT_VERSION,
        "saved_at": _text(raw.get("saved_at"), limit=64),
        "name": _text(raw.get("name") or "Untitled Project", limit=100),
        "inputs": normalized_inputs,
        "active_input": max(
            0,
            min(
                int(_finite(raw.get("active_input"))),
                max(0, len(normalized_inputs) - 1),
            ),
        ),
        "trim": _normalize_map(raw.get("trim"), "trim"),
        "filters": _normalize_map(raw.get("filters"), "filters"),
        "media_tools": _normalize_map(raw.get("media_tools"), "media_tools"),
        "preset": _normalize_map(raw.get("preset"), "preset"),
        "active_panel": max(0, min(int(_finite(raw.get("active_panel"))), 100)),
        "unsupported_features": [
            _text(item, limit=120)
            for item in (raw.get("unsupported_features") or [])[:50]
            if isinstance(item, (str, int, float))
        ],
        "media_info": _normalize_map(raw.get("media_info"), "media_info"),
    }


def build_project(
    inputs: list[str | os.PathLike[str]],
    *,
    project_path: str | os.PathLike[str] | None = None,
    media_info: dict[str, Any] | None = None,
    trim: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    media_tools: dict[str, Any] | None = None,
    preset: dict[str, Any] | None = None,
    active_input: int = 0,
    active_panel: int = 0,
    name: str | None = None,
) -> dict[str, Any]:
    """Build a canonical project document from current desktop state."""

    refs = [media_reference(path, project_path) for path in inputs[:MAX_INPUTS]]
    source_name = refs[0]["name"] if refs else "Untitled Project"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return normalize_project({
        "schema": PROJECT_SCHEMA,
        "version": PROJECT_VERSION,
        "saved_at": now,
        "name": _text(name or Path(source_name).stem or "Untitled Project", limit=100),
        "inputs": refs,
        "active_input": active_input,
        "trim": dict(trim or {}),
        "filters": dict(filters or {}),
        "media_tools": dict(media_tools or {}),
        "preset": dict(preset or {}),
        "active_panel": active_panel,
        "unsupported_features": [
            "multi-source timeline",
            "browser-only transitions",
        ],
        "media_info": dict(media_info or {}),
    })


def resolve_project_input(
    project: dict[str, Any],
    project_path: str | os.PathLike[str],
    index: int = 0,
) -> Path | None:
    """Resolve an input by original path, project-relative path, or basename."""

    normalized = normalize_project(project)
    if not normalized["inputs"] or not 0 <= index < len(normalized["inputs"]):
        return None
    ref = normalized["inputs"][index]
    candidates = [Path(ref["path"])] if ref["path"] else []
    project_dir = Path(project_path).resolve().parent
    if ref["relative_path"]:
        candidates.append(project_dir / ref["relative_path"])
    candidates.append(project_dir / ref["name"])
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return None


def save_project(
    path: str | os.PathLike[str],
    project: dict[str, Any],
    *,
    backup: bool = True,
) -> Path:
    """Atomically write a project, retaining one recoverable `.bak`."""

    target = Path(path).expanduser().resolve()
    if target.suffix.lower() not in (PROJECT_EXTENSION, *LEGACY_PROJECT_EXTENSIONS):
        target = target.with_suffix(PROJECT_EXTENSION)
    normalized = normalize_project(project)
    encoded = json.dumps(normalized, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_PROJECT_BYTES:
        raise ProjectError(f"Project exceeds the {MAX_PROJECT_BYTES}-byte limit")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        staged = Path(staged_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if backup and target.is_file():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        os.replace(staged, target)
    except OSError as exc:
        raise ProjectError(f"Could not save project: {exc}") from exc
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except FileNotFoundError:
                pass
    return target


def load_project(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        if source.stat().st_size > MAX_PROJECT_BYTES:
            raise ProjectError(f"Project exceeds the {MAX_PROJECT_BYTES}-byte limit")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except ProjectError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Could not read project: {exc}") from exc
    return normalize_project(raw)


__all__ = [
    "LEGACY_PROJECT_EXTENSIONS",
    "MAX_PROJECT_BYTES",
    "PROJECT_EXTENSION",
    "PROJECT_SCHEMA",
    "PROJECT_VERSION",
    "ProjectError",
    "build_project",
    "load_project",
    "media_reference",
    "normalize_project",
    "resolve_project_input",
    "save_project",
]
