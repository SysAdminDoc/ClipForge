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


def export_presets(names, filepath):
    """Export selected presets to a single JSON file."""
    try:
        user = load_user_presets()
        from .constants import BUILTIN_PRESETS
        bundle = {}
        for name in names:
            if name in user:
                bundle[name] = user[name]
            elif name in BUILTIN_PRESETS:
                bundle[name] = BUILTIN_PRESETS[name]
        if not bundle:
            return False
        import json as _json
        from pathlib import Path as _Path
        _Path(filepath).write_text(_json.dumps(bundle, indent=2))
        return True
    except OSError:
        return False


def import_presets(filepath):
    """Import presets from a JSON file. Returns list of imported names."""
    try:
        import json as _json
        from pathlib import Path as _Path
        data = _json.loads(_Path(filepath).read_text())
        if not isinstance(data, dict):
            return []
        imported = []
        for name, preset_data in data.items():
            if isinstance(preset_data, dict):
                saved = save_user_preset(name, preset_data)
                if saved:
                    imported.append(saved)
        return imported
    except (OSError, json.JSONDecodeError, ValueError):
        return []


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
