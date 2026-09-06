import subprocess
import sys
import tomllib
from pathlib import Path

from clipforge.branding import application_icon_path
from scripts.verify_browser_runtime import verify_inventory

ROOT = Path(__file__).resolve().parents[1]


def test_version_surfaces_are_synchronized():
    result = subprocess.run(
        [sys.executable, "scripts/sync_version.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_browser_runtime_inventory_and_policy_are_synchronized():
    verify_inventory()


def test_manifest_and_runtime_lock_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"
    assert project["project"]["dependencies"] == ["PyQt6>=6.11,<7"]
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8").lower()
    assert "pyqt6==" in lock
    assert "pyqt6-qt6==6.11.1" in lock
    assert "pyqt6-sip==" in lock


def test_all_dependency_environments_are_complete_hash_locks():
    expected = {
        "requirements.lock": ("pyqt6==",),
        "requirements-dev.lock": (
            "pyqt6==",
            "pytest==",
            "pyinstaller==",
            "playwright==",
        ),
        "requirements-mpv.lock": ("pyqt6==", "mpv=="),
    }
    for filename, packages in expected.items():
        lock = (ROOT / filename).read_text(encoding="utf-8").lower()
        requirement_lines = [
            line.strip()
            for line in lock.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not any(line.startswith(("-r ", "--requirement ")) for line in requirement_lines)
        assert "--hash=sha256:" in lock
        for package in packages:
            assert package in lock


def test_release_build_uses_isolated_python_and_packaged_media_smoke():
    release_check = (ROOT / "scripts" / "release_check.py").read_text(
        encoding="utf-8"
    )
    assert "venv.EnvBuilder" in release_check
    assert '"--require-hashes"' in release_check
    assert 'environment_python, "-m", "PyInstaller"' in release_check
    assert '"--release-smoke"' in release_check
    assert "publish_build_artifacts" in release_check
    assert '"SHA256SUMS.txt"' in release_check
    assert 'packaged_env["QT_QPA_PLATFORM"] = "offscreen"' in release_check


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
    assert '"packaging" / "clipforge.ico"' in spec
    assert "clipforge-mark-256.png" in spec
    assert '{"icuuc.dll", "icudt78.dll"}' in spec
    assert "a.binaries = [" in spec


def test_brand_assets_are_wired_into_desktop_and_browser():
    icon = application_icon_path()
    assert icon.is_file()
    assert icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    browser = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "clipforge-mark-128.png" in browser
    assert "clipforge-mark-32.png" in browser
