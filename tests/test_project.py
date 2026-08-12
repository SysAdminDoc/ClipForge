import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from clipforge.project import (
    PROJECT_SCHEMA,
    ProjectError,
    build_project,
    load_project,
    normalize_project,
    resolve_project_input,
    save_project,
)


_QT_APP = QApplication.instance() or QApplication([])


def test_project_round_trip_preserves_external_media_and_editing_state(tmp_path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"media")
    project_path = tmp_path / "edit.cfproj"
    payload = build_project(
        [media],
        project_path=project_path,
        trim={"start": 1.25, "end": 9.5},
        filters={"sliders": {"contrast": 20}, "silence_segments": [[2, 3]]},
        preset={"preset": "[Built-in] YouTube 1080p"},
        active_panel=4,
    )
    saved = save_project(project_path, payload)
    loaded = load_project(saved)

    assert loaded["schema"] == PROJECT_SCHEMA
    assert loaded["trim"]["start"] == 1.25
    assert loaded["filters"]["silence_segments"] == [[2, 3]]
    assert resolve_project_input(loaded, saved) == media.resolve()

    media.unlink()
    moved = tmp_path / "moved"
    moved.mkdir()
    relocated = moved / media.name
    relocated.write_bytes(b"media")
    moved_project = tmp_path / "moved" / "edit.cfproj"
    save_project(moved_project, payload)
    reloaded = load_project(moved_project)
    assert resolve_project_input(reloaded, moved_project) == relocated.resolve()


def test_project_save_keeps_one_backup_and_rejects_future_schema(tmp_path):
    path = tmp_path / "session.cfproj"
    payload = normalize_project({"schema": PROJECT_SCHEMA, "version": 1, "inputs": []})
    save_project(path, payload)
    payload["name"] = "changed"
    save_project(path, payload)

    backup = path.with_suffix(".cfproj.bak")
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["name"] == "Untitled Project"

    with pytest.raises(ProjectError, match="newer"):
        normalize_project({"schema": PROJECT_SCHEMA, "version": 99, "inputs": []})


def test_project_normalization_does_not_execute_or_accept_non_json_values():
    with pytest.raises(ProjectError, match="unsupported values"):
        normalize_project({
            "schema": PROJECT_SCHEMA,
            "version": 1,
            "inputs": [],
            "filters": {"bad": float("nan")},
        })


def test_main_window_exposes_project_actions_and_payload(monkeypatch, tmp_path):
    from clipforge.app import MainWindow

    monkeypatch.setattr("clipforge.app.load_settings", lambda: {})
    monkeypatch.setattr(MainWindow, "_check_deps", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_capability_probe", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_recent", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_persistence_notices", lambda self: None)
    window = MainWindow()
    try:
        actions = {action.text() for action in window.menuBar().actions()}
        assert "Project" in actions
        payload = window._project_payload()
        target = tmp_path / "empty.cfproj"
        save_project(target, payload)
        assert load_project(target)["schema"] == PROJECT_SCHEMA
    finally:
        window.close()
        _QT_APP.processEvents()


def test_main_window_can_detach_and_reattach_preview(monkeypatch):
    from clipforge.app import MainWindow

    monkeypatch.setattr("clipforge.app.load_settings", lambda: {})
    monkeypatch.setattr(MainWindow, "_check_deps", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_capability_probe", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_recent", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_persistence_notices", lambda self: None)
    window = MainWindow()
    try:
        window._set_preview_detached(True)
        _QT_APP.processEvents()
        assert window._preview_dock is not None
        assert window._preview_dock.widget() is window.player
        assert window.preview_detach_action.isChecked()

        window._set_preview_detached(False)
        _QT_APP.processEvents()
        assert window._preview_dock is None
        assert window.top_splitter.indexOf(window.player) == 0
        assert not window.preview_detach_action.isChecked()
    finally:
        window.close()
        _QT_APP.processEvents()
