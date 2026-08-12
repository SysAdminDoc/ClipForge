"""Transactional settings, presets, and recent-file persistence."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from clipforge_utils import _sanitize_preset_name

from .constants import (
    CONFIG_DIR,
    PRESETS_DIR,
    RECENT_FILE,
    SETTINGS_FILE,
    STATE_FILE,
)


STATE_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_PRESETS = 100
MAX_QUARANTINES = 3
_STORE_LOCK = threading.RLock()
_NOTICES = deque(maxlen=20)
_writes_blocked_reason = None


class PresetData(TypedDict, total=False):
    container: str
    vcodec: str
    acodec: str
    crf: int
    preset: str
    resolution: str
    fps: str
    speed: float


class AppState(TypedDict):
    schema_version: int
    settings: dict[str, Any]
    recents: list[str]
    presets: dict[str, PresetData]


class UnsupportedSchemaError(ValueError):
    pass


def _default_state() -> AppState:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "settings": {},
        "recents": [],
        "presets": {},
    }


def _notice(level, message):
    _NOTICES.append({"level": level, "message": message})


def consume_persistence_notices():
    notices = list(_NOTICES)
    _NOTICES.clear()
    return notices


def _validate_json_value(value, *, depth=0):
    if depth > 6:
        raise ValueError("nested setting is too deep")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("setting numbers must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_validate_json_value(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("setting object has too many fields")
        return {
            str(key)[:100]: _validate_json_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    raise ValueError(f"unsupported setting value: {type(value).__name__}")


def _validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")
    settings = {
        str(key)[:100]: _validate_json_value(item)
        for key, item in value.items()
    }
    if "high_contrast" in settings and not isinstance(settings["high_contrast"], bool):
        raise ValueError("high_contrast must be true or false")
    if "updates_enabled" in settings and not isinstance(settings["updates_enabled"], bool):
        raise ValueError("updates_enabled must be true or false")
    for key in ("window_width", "window_height"):
        if key in settings:
            item = settings[key]
            if isinstance(item, bool) or not isinstance(item, int) or not 320 <= item <= 16384:
                raise ValueError(f"{key} is outside the supported range")
    return settings


_PRESET_STRINGS = {
    "container",
    "vcodec",
    "acodec",
    "preset",
    "resolution",
    "fps",
}


def _validate_preset(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("preset must be a nonempty object")
    unknown = set(value) - _PRESET_STRINGS - {"crf", "speed"}
    if unknown:
        raise ValueError(f"preset has unsupported fields: {sorted(unknown)}")
    preset = {}
    for key in _PRESET_STRINGS:
        if key in value:
            item = value[key]
            if not isinstance(item, str) or not item or len(item) > 200:
                raise ValueError(f"preset field {key} must be a short string")
            preset[key] = item
    if "crf" in value:
        crf = value["crf"]
        if isinstance(crf, bool) or not isinstance(crf, int) or not 0 <= crf <= 63:
            raise ValueError("preset crf must be between 0 and 63")
        preset["crf"] = crf
    if "speed" in value:
        speed = value["speed"]
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or not 0.1 <= speed <= 10
        ):
            raise ValueError("preset speed must be between 0.1 and 10")
        preset["speed"] = float(speed)
    return preset


def _validate_recents(value):
    if not isinstance(value, list):
        raise ValueError("recent files must be a list")
    recents = []
    for item in value[:10]:
        if not isinstance(item, str) or not item or len(item) > 32767:
            raise ValueError("recent file entries must be nonempty paths")
        if item not in recents:
            recents.append(item)
    return recents


def _validate_state(value):
    if not isinstance(value, dict):
        raise ValueError("state must be an object")
    version = value.get("schema_version")
    if version != STATE_SCHEMA_VERSION:
        if isinstance(version, int) and version > STATE_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"state schema {version} is newer than supported schema "
                f"{STATE_SCHEMA_VERSION}"
            )
        raise ValueError(f"unsupported state schema: {version!r}")
    presets = value.get("presets")
    if not isinstance(presets, dict) or len(presets) > MAX_PRESETS:
        raise ValueError("presets must be an object with at most 100 entries")
    validated_presets = {}
    for name, preset in presets.items():
        if not isinstance(name, str) or not name or len(name) > 100:
            raise ValueError("preset names must be short nonempty strings")
        validated_presets[name] = _validate_preset(preset)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "settings": _validate_settings(value.get("settings")),
        "recents": _validate_recents(value.get("recents")),
        "presets": validated_presets,
    }


def _read_json(path):
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_STATE_BYTES}-byte limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _quarantine(path):
    path = Path(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantined = path.with_name(
        f"{path.stem}.corrupt-{timestamp}-{uuid.uuid4().hex[:8]}{path.suffix}"
    )
    os.replace(path, quarantined)
    candidates = sorted(
        path.parent.glob(f"{path.stem}.corrupt-*{path.suffix}"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[MAX_QUARANTINES:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return quarantined


def _atomic_write_json(path, value, *, retain_backup=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_STATE_BYTES}-byte limit")
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    staged = Path(staged_name)
    backup_staged = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if retain_backup and path.is_file():
            backup = path.with_suffix(path.suffix + ".bak")
            backup_staged = backup.with_suffix(backup.suffix + ".tmp")
            shutil.copy2(path, backup_staged)
            os.replace(backup_staged, backup)
        os.replace(staged, path)
    finally:
        for temporary in (staged, backup_staged):
            if temporary is None:
                continue
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _migrate_legacy():
    state = _default_state()
    migrated = []
    for path, key, validator in (
        (SETTINGS_FILE, "settings", _validate_settings),
        (RECENT_FILE, "recents", _validate_recents),
    ):
        if not path.is_file():
            continue
        try:
            state[key] = validator(_read_json(path))
            migrated.append(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            try:
                quarantined = _quarantine(path)
                _notice(
                    "warning",
                    f"Malformed legacy {path.name} was quarantined as "
                    f"{quarantined.name}: {exc}",
                )
            except OSError as quarantine_error:
                _notice(
                    "error",
                    f"Could not recover malformed {path.name}: {quarantine_error}",
                )
    if PRESETS_DIR.is_dir():
        for path in sorted(PRESETS_DIR.glob("*.json")):
            try:
                state["presets"][path.stem] = _validate_preset(_read_json(path))
                migrated.append(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                try:
                    quarantined = _quarantine(path)
                    _notice(
                        "warning",
                        f"Malformed preset {path.name} was quarantined as "
                        f"{quarantined.name}: {exc}",
                    )
                except OSError as quarantine_error:
                    _notice(
                        "error",
                        f"Could not recover preset {path.name}: {quarantine_error}",
                    )
    return state, migrated


def _load_state():
    global _writes_blocked_reason
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.is_file():
        state, migrated = _migrate_legacy()
        if migrated:
            try:
                _atomic_write_json(STATE_FILE, state)
            except (OSError, ValueError) as exc:
                _notice(
                    "error",
                    f"Could not migrate ClipForge preferences: {exc}. "
                    f"Check write permissions for {CONFIG_DIR}.",
                )
                return state
            for path in migrated:
                try:
                    path.unlink()
                except OSError:
                    pass
            _notice("info", "Preferences were migrated to the transactional store.")
        return state
    try:
        return _validate_state(_read_json(STATE_FILE))
    except UnsupportedSchemaError as exc:
        _writes_blocked_reason = str(exc)
        _notice(
            "error",
            f"{exc}. This ClipForge version will not overwrite newer preferences.",
        )
        return _default_state()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        try:
            quarantined = _quarantine(STATE_FILE)
        except OSError as quarantine_error:
            _notice(
                "error",
                f"Could not quarantine malformed preferences: {quarantine_error}",
            )
            return _default_state()
        backup = STATE_FILE.with_suffix(STATE_FILE.suffix + ".bak")
        if backup.is_file():
            try:
                state = _validate_state(_read_json(backup))
                _atomic_write_json(STATE_FILE, state)
                _notice(
                    "warning",
                    f"Recovered preferences from the last-known-good backup; "
                    f"malformed data is in {quarantined.name}.",
                )
                return state
            except (OSError, ValueError, json.JSONDecodeError):
                try:
                    _quarantine(backup)
                except OSError:
                    pass
        state = _default_state()
        try:
            _atomic_write_json(STATE_FILE, state)
        except (OSError, ValueError):
            pass
        _notice(
            "warning",
            f"Reset preferences after quarantining malformed data as "
            f"{quarantined.name}: {exc}",
        )
        return state


def _save_state(state, action):
    if _writes_blocked_reason:
        _notice(
            "error",
            f"Could not {action}: {_writes_blocked_reason}. "
            "Use the ClipForge version that created this data.",
        )
        return False
    try:
        validated = _validate_state(state)
        _atomic_write_json(STATE_FILE, validated, retain_backup=True)
        return True
    except (OSError, ValueError) as exc:
        _notice(
            "error",
            f"Could not {action}: {exc}. Check write permissions for {CONFIG_DIR}; "
            "the previous preferences remain intact.",
        )
        return False


def _state_for_mutation(action):
    try:
        return _load_state()
    except OSError as exc:
        _notice(
            "error",
            f"Could not {action}: {exc}. Check access to {CONFIG_DIR}; "
            "the previous preferences remain intact.",
        )
        return None


def load_settings():
    with _STORE_LOCK:
        try:
            return dict(_load_state()["settings"])
        except OSError as exc:
            _notice("error", f"Could not read preferences from {CONFIG_DIR}: {exc}")
            return {}


def save_settings(settings):
    with _STORE_LOCK:
        try:
            validated = _validate_settings(settings)
        except ValueError as exc:
            _notice("error", f"Could not save settings: {exc}")
            return False
        state = _state_for_mutation("save settings")
        if state is None:
            return False
        state["settings"] = validated
        return _save_state(state, "save settings")


def load_user_presets():
    with _STORE_LOCK:
        try:
            return {
                name: dict(data)
                for name, data in _load_state()["presets"].items()
            }
        except OSError as exc:
            _notice("error", f"Could not read presets from {CONFIG_DIR}: {exc}")
            return {}


def save_user_preset(name, data):
    with _STORE_LOCK:
        safe_name = _sanitize_preset_name(name)
        try:
            preset = _validate_preset(data)
        except ValueError as exc:
            _notice("error", f"Could not save preset: {exc}")
            return None
        state = _state_for_mutation(f"save preset '{safe_name}'")
        if state is None:
            return None
        state["presets"][safe_name] = preset
        if len(state["presets"]) > MAX_PRESETS:
            _notice("error", f"Could not save preset: limit is {MAX_PRESETS}.")
            return None
        return safe_name if _save_state(state, f"save preset '{safe_name}'") else None


def delete_user_preset(name):
    with _STORE_LOCK:
        state = _state_for_mutation(f"delete preset '{name}'")
        if state is None:
            return False
        removed = state["presets"].pop(name, None)
        if removed is None:
            return False
        return _save_state(state, f"delete preset '{name}'")


def export_presets(names, filepath):
    """Export selected validated presets to an atomic JSON file."""
    try:
        user = load_user_presets()
        from .constants import BUILTIN_PRESETS

        bundle = {}
        for name in names:
            if name in user:
                bundle[name] = _validate_preset(user[name])
            elif name in BUILTIN_PRESETS:
                bundle[name] = _validate_preset(BUILTIN_PRESETS[name])
        if not bundle:
            return False
        _atomic_write_json(Path(filepath), bundle)
        return True
    except (OSError, ValueError) as exc:
        _notice("error", f"Could not export presets: {exc}")
        return False


def import_presets(filepath):
    """Import a bounded validated preset bundle. Return imported names."""
    try:
        data = _read_json(Path(filepath))
        if not isinstance(data, dict) or not 1 <= len(data) <= MAX_PRESETS:
            raise ValueError("preset bundle must contain 1 to 100 presets")
        incoming = {}
        for name, preset_data in data.items():
            if not isinstance(name, str):
                raise ValueError("preset names must be strings")
            incoming[_sanitize_preset_name(name)] = _validate_preset(preset_data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _notice("error", f"Could not import presets: {exc}")
        return []
    with _STORE_LOCK:
        state = _state_for_mutation("import presets")
        if state is None:
            return []
        if len(set(state["presets"]) | set(incoming)) > MAX_PRESETS:
            _notice("error", f"Could not import presets: limit is {MAX_PRESETS}.")
            return []
        state["presets"].update(incoming)
        return list(incoming) if _save_state(state, "import presets") else []


def load_recent():
    with _STORE_LOCK:
        try:
            return list(_load_state()["recents"])
        except OSError as exc:
            _notice("error", f"Could not read recent files from {CONFIG_DIR}: {exc}")
            return []


def save_recent(paths):
    with _STORE_LOCK:
        try:
            recents = _validate_recents(list(paths)[:10])
        except ValueError as exc:
            _notice("error", f"Could not save recent files: {exc}")
            return False
        state = _state_for_mutation("save recent files")
        if state is None:
            return False
        state["recents"] = recents
        return _save_state(state, "save recent files")


def add_recent(filepath):
    recent = load_recent()
    if filepath in recent:
        recent.remove(filepath)
    recent.insert(0, filepath)
    return save_recent(recent[:10])
