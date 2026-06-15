"""Settings, presets, and recent file persistence."""

import json

from .constants import CONFIG_DIR, SETTINGS_FILE, RECENT_FILE, PRESETS_DIR
from clipforge_utils import _sanitize_preset_name

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


def load_settings():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def save_settings(settings):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Preset persistence
# ---------------------------------------------------------------------------


def load_user_presets():
    try:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        presets = {}
        for f in PRESETS_DIR.glob("*.json"):
            try:
                presets[f.stem] = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        return presets
    except OSError:
        return {}


def save_user_preset(name, data):
    try:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_preset_name(name)
        (PRESETS_DIR / f"{safe_name}.json").write_text(json.dumps(data, indent=2))
        return safe_name
    except OSError:
        return None


def delete_user_preset(name):
    try:
        p = PRESETS_DIR / f"{name}.json"
        if p.exists():
            p.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Recent files
# ---------------------------------------------------------------------------


def load_recent():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if RECENT_FILE.exists():
            return json.loads(RECENT_FILE.read_text())[:10]
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return []


def save_recent(paths):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps(paths[:10]))
    except OSError:
        pass


def add_recent(filepath):
    recent = load_recent()
    if filepath in recent:
        recent.remove(filepath)
    recent.insert(0, filepath)
    save_recent(recent[:10])
