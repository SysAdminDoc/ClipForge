import json

import pytest

from clipforge import settings


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    config = tmp_path / ".clipforge"
    monkeypatch.setattr(settings, "CONFIG_DIR", config)
    monkeypatch.setattr(settings, "STATE_FILE", config / "state.json")
    monkeypatch.setattr(settings, "SETTINGS_FILE", config / "settings.json")
    monkeypatch.setattr(settings, "RECENT_FILE", config / "recent.json")
    monkeypatch.setattr(settings, "PRESETS_DIR", config / "presets")
    monkeypatch.setattr(settings, "_writes_blocked_reason", None)
    settings.consume_persistence_notices()
    return config


def test_legacy_files_migrate_into_one_schema_store(isolated_store):
    isolated_store.mkdir()
    settings.SETTINGS_FILE.write_text(
        json.dumps({"high_contrast": True, "window_width": 1200}),
        encoding="utf-8",
    )
    settings.RECENT_FILE.write_text(
        json.dumps(["C:/media/one.mp4"]),
        encoding="utf-8",
    )
    settings.PRESETS_DIR.mkdir()
    (settings.PRESETS_DIR / "Web.json").write_text(
        json.dumps({"container": "MP4", "crf": 21, "speed": 1.0}),
        encoding="utf-8",
    )

    assert settings.load_settings()["high_contrast"] is True
    state = json.loads(settings.STATE_FILE.read_text(encoding="utf-8"))
    assert state["schema_version"] == settings.STATE_SCHEMA_VERSION
    assert state["recents"] == ["C:/media/one.mp4"]
    assert state["presets"]["Web"]["crf"] == 21
    assert not settings.SETTINGS_FILE.exists()
    assert not settings.RECENT_FILE.exists()
    assert not (settings.PRESETS_DIR / "Web.json").exists()


def test_corrupt_primary_recovers_bounded_last_known_good(isolated_store):
    assert settings.save_settings({"window_width": 1200})
    assert settings.save_settings({"window_width": 1400})
    settings.STATE_FILE.write_text("{truncated", encoding="utf-8")

    assert settings.load_settings()["window_width"] == 1200
    quarantines = list(isolated_store.glob("state.corrupt-*.json"))
    assert len(quarantines) == 1
    recovered = json.loads(settings.STATE_FILE.read_text(encoding="utf-8"))
    assert recovered["settings"]["window_width"] == 1200
    notices = settings.consume_persistence_notices()
    assert any("last-known-good backup" in item["message"] for item in notices)


def test_failed_atomic_replace_preserves_previous_state(
    isolated_store,
    monkeypatch,
):
    assert settings.save_settings({"window_width": 1200})
    original = settings.STATE_FILE.read_bytes()
    real_replace = settings.os.replace

    def fail_primary_replace(source, destination):
        if destination == settings.STATE_FILE:
            raise PermissionError("read-only configuration directory")
        return real_replace(source, destination)

    monkeypatch.setattr(settings.os, "replace", fail_primary_replace)
    assert not settings.save_settings({"window_width": 1400})
    assert settings.STATE_FILE.read_bytes() == original
    notices = settings.consume_persistence_notices()
    assert any("previous preferences remain intact" in item["message"] for item in notices)


def test_newer_schema_is_never_overwritten(isolated_store):
    isolated_store.mkdir()
    newer = {
        "schema_version": 99,
        "settings": {"window_width": 900},
        "recents": [],
        "presets": {},
    }
    settings.STATE_FILE.write_text(json.dumps(newer), encoding="utf-8")

    assert settings.load_settings() == {}
    assert not settings.save_settings({"window_width": 1200})
    assert json.loads(settings.STATE_FILE.read_text(encoding="utf-8")) == newer


def test_preset_import_is_validated_before_mutating_store(
    isolated_store,
    tmp_path,
):
    bundle = tmp_path / "presets.json"
    bundle.write_text(
        json.dumps(
            {
                "Good": {"container": "MP4", "crf": 20},
                "Bad": {"container": ["not", "text"]},
            }
        ),
        encoding="utf-8",
    )

    assert settings.import_presets(bundle) == []
    assert settings.load_user_presets() == {}
    assert any(
        "preset field container" in item["message"]
        for item in settings.consume_persistence_notices()
    )
