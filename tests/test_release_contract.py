import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.verify_browser_runtime import verify_inventory


ROOT = Path(__file__).resolve().parents[1]


def test_version_surfaces_are_synchronized():
    result = subprocess.run(
        [sys.executable, "scripts/sync_version.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_browser_runtime_inventory_and_policy_are_synchronized():
    verify_inventory()


def test_manifest_and_runtime_lock_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.10"
    assert project["project"]["dependencies"] == ["PyQt6>=6.7,<7"]
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "PyQt6==" in lock
    assert "PyQt6-Qt6==" in lock
    assert "PyQt6-sip==" in lock


def test_launchers_never_install_dependencies():
    launchers = [
        (ROOT / "clipforge.py").read_text(encoding="utf-8"),
        (ROOT / "clipforge" / "__main__.py").read_text(encoding="utf-8"),
    ]
    for launcher in launchers:
        assert "subprocess" not in launcher
        assert "_bootstrap" not in launcher
        assert "check_call" not in launcher


def test_pyinstaller_spec_has_no_machine_specific_paths():
    spec = (ROOT / "ClipForge.spec").read_text(encoding="utf-8")
    assert "C:\\Users\\" not in spec
    assert "runtime_hooks=[]" in spec
    assert "windows-version.txt" in spec
