#!/usr/bin/env python3
"""Local release gate with deterministic FFmpeg fixtures and optional build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge.processes import terminate_process_tree, validate_output
from clipforge.tools import FFMPEG, FFPROBE, probe_video, write_concat_manifest
from clipforge.version import APP_VERSION


def run(command, *, timeout=120, env=None):
    result = subprocess.run(
        [str(part) for part in command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-8:]
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(map(str, command))}\n"
            + "\n".join(detail)
        )
    return result


def require_valid(path):
    valid, reason = validate_output(path, ffprobe_path=FFPROBE)
    if not valid:
        raise RuntimeError(f"{path.name}: {reason}")
    return path


def build_media_fixtures(workspace):
    if not FFMPEG or not FFPROBE:
        raise RuntimeError("FFmpeg and ffprobe are required")
    workspace = Path(workspace)
    odd_source = workspace / "vidéo ' source.mp4"
    video_only = workspace / "video-only.mp4"
    subtitles = workspace / "captions.srt"
    subtitle_media = workspace / "subtitled.mkv"
    chapter_meta = workspace / "chapters.ffmeta"
    chapter_media = workspace / "chapters.mkv"
    rotated = workspace / "rotated.mp4"
    vfr = workspace / "variable-rate.mkv"

    run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", odd_source,
    ])
    run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "color=c=blue:size=160x90:rate=24",
        "-t", "1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        video_only,
    ])
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\nClipForge fixture\n",
        encoding="utf-8",
    )
    run([
        FFMPEG, "-y", "-i", odd_source, "-f", "srt", "-i", subtitles,
        "-map", "0", "-map", "1", "-c", "copy", "-c:s", "srt", subtitle_media,
    ])
    chapter_meta.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=Intro\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\ntitle=End\n",
        encoding="utf-8",
    )
    run([
        FFMPEG, "-y", "-i", odd_source, "-i", chapter_meta,
        "-map_metadata", "1", "-c", "copy", chapter_media,
    ])
    run([
        FFMPEG, "-y", "-display_rotation:v:0", "90", "-i", odd_source,
        "-c", "copy", rotated,
    ])
    run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=0.5",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=0.5",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-fps_mode", "vfr", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", vfr,
    ])

    fixtures = {
        "audio_video": odd_source,
        "video_only": video_only,
        "subtitles": subtitle_media,
        "chapters": chapter_media,
        "rotation": rotated,
        "vfr": vfr,
    }
    for fixture in fixtures.values():
        require_valid(fixture)
        if not probe_video(str(fixture)):
            raise RuntimeError(f"ClipForge probe failed for {fixture.name}")

    rotation_probe = run([
        FFPROBE, "-v", "error", "-show_streams", "-of", "json", rotated,
    ])
    rotation_data = json.loads(rotation_probe.stdout)
    if "rotation" not in json.dumps(rotation_data).lower():
        raise RuntimeError("Rotation metadata was not retained in the fixture")
    chapter_probe = run([
        FFPROBE, "-v", "error", "-show_chapters", "-of", "json", chapter_media,
    ])
    if len(json.loads(chapter_probe.stdout).get("chapters", [])) != 2:
        raise RuntimeError("Chapter fixture did not retain both chapters")
    return fixtures


def exercise_media_operations(workspace, fixtures):
    workspace = Path(workspace)
    trimmed = workspace / "trim ' output.mp4"
    cropped = workspace / "cropped.mp4"
    converted = workspace / "converted.webm"
    audio = workspace / "audio.m4a"
    concat_output = workspace / "concat output.mp4"
    concat_manifest = workspace / "concat manifest.txt"

    run([
        FFMPEG, "-y", "-ss", "0.1", "-i", fixtures["audio_video"],
        "-t", "0.5", "-c:v", "libx264", "-c:a", "aac", trimmed,
    ])
    run([
        FFMPEG, "-y", "-i", fixtures["video_only"],
        "-vf", "crop=120:80:0:0", "-c:v", "libx264", "-an", cropped,
    ])
    run([
        FFMPEG, "-y", "-i", fixtures["video_only"],
        "-c:v", "libvpx-vp9", "-crf", "35", "-b:v", "0", converted,
    ])
    run([
        FFMPEG, "-y", "-i", fixtures["audio_video"],
        "-vn", "-c:a", "aac", audio,
    ])
    write_concat_manifest([trimmed, trimmed], concat_manifest)
    run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0",
        "-i", concat_manifest, "-c", "copy", concat_output,
    ])
    for output in (trimmed, cropped, converted, audio, concat_output):
        require_valid(output)


def run_media_gate():
    with tempfile.TemporaryDirectory(prefix="clipforge-release-media-") as temp:
        fixtures = build_media_fixtures(temp)
        exercise_media_operations(temp, fixtures)


def run_gui_smoke():
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    code = (
        "from PyQt6.QtCore import QTimer;"
        "from PyQt6.QtWidgets import QApplication;"
        "from clipforge.app import MainWindow;"
        "app=QApplication([]);window=MainWindow();window.show();"
        "QTimer.singleShot(400,window.close);QTimer.singleShot(700,app.quit);"
        "raise SystemExit(app.exec())"
    )
    with tempfile.TemporaryDirectory(prefix="clipforge-gui-config-") as config:
        env["CLIPFORGE_CONFIG_DIR"] = config
        run([sys.executable, "-c", code], timeout=30, env=env)


def run_provenance_gate():
    """Build a disposable inventory from the runtimes used by this checkout."""
    with tempfile.TemporaryDirectory(prefix="clipforge-release-provenance-") as temp:
        env = os.environ.copy()
        env["CLIPFORGE_CONFIG_DIR"] = str(Path(temp) / "config")
        run(
            [
                sys.executable,
                "scripts/verify_provenance.py",
                "--output",
                Path(temp) / "provenance.json",
            ],
            timeout=120,
            env=env,
        )


def remove_tree_with_retries(path, *, attempts=20):
    path = Path(path)
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_platform():
    system = {
        "Windows": "windows",
        "Darwin": "macos",
        "Linux": "linux",
    }.get(platform.system(), platform.system().lower())
    machine = platform.machine().lower()
    arch = "x64" if machine in {"amd64", "x86_64"} else machine
    return f"{system}-{arch}"


def publish_build_artifacts(executable, provenance, output_dir):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    stem = f"ClipForge-v{APP_VERSION}-{artifact_platform()}"
    published_executable = output_dir / f"{stem}{suffix}"
    published_provenance = output_dir / f"{stem}-provenance.json"
    shutil.copy2(executable, published_executable)
    shutil.copy2(provenance, published_provenance)
    published = [published_executable, published_provenance]
    checksums = output_dir / "SHA256SUMS.txt"
    checksums.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in published),
        encoding="utf-8",
    )
    return [*published, checksums]


def run_build_smoke(output_dir=None):
    if output_dir is not None:
        output_dir = Path(output_dir).resolve()
        remove_tree_with_retries(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    temp_path = Path(tempfile.mkdtemp(prefix="clipforge-release-build-"))
    try:
        fixture_dir = temp_path / "fixtures"
        fixture_dir.mkdir()
        fixtures = build_media_fixtures(fixture_dir)
        environment = temp_path / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        environment_python = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        run(
            [
                environment_python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "-r",
                ROOT / "requirements-dev.lock",
            ],
            timeout=300,
        )
        build_env = os.environ.copy()
        build_env["CLIPFORGE_CONFIG_DIR"] = str(temp_path / "build-config")
        provenance = temp_path / "build-provenance.json"
        run(
            [
                environment_python,
                ROOT / "scripts" / "verify_provenance.py",
                "--strict-lock",
                ROOT / "requirements-dev.lock",
                "--output",
                provenance,
            ],
            timeout=120,
            env=build_env,
        )
        run([
            environment_python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", temp_path / "dist",
            "--workpath", temp_path / "build",
            ROOT / "ClipForge.spec",
        ], timeout=300)
        executable = temp_path / "dist" / (
            "ClipForge.exe" if sys.platform == "win32" else "ClipForge"
        )
        if not executable.is_file():
            raise RuntimeError("PyInstaller artifact was not created")
        packaged_output = temp_path / "packaged-smoke.mp4"
        run(
            [
                executable,
                "--release-smoke",
                fixtures["audio_video"],
                packaged_output,
            ],
            timeout=45,
        )
        require_valid(packaged_output)
        packaged_env = os.environ.copy()
        packaged_env["CLIPFORGE_CONFIG_DIR"] = str(temp_path / "packaged-config")
        packaged_env["QT_QPA_PLATFORM"] = "offscreen"
        process = subprocess.Popen(
            [str(executable), str(fixtures["audio_video"])],
            cwd=ROOT,
            env=packaged_env,
        )
        try:
            time.sleep(4)
            if process.poll() is not None:
                raise RuntimeError(
                    f"Packaged application exited early with {process.returncode}"
                )
        finally:
            terminate_process_tree(process)
            time.sleep(0.5)
        if output_dir is not None:
            publish_build_artifacts(executable, provenance, output_dir)
    finally:
        remove_tree_with_retries(temp_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--media-only", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    if sys.version_info < (3, 11):  # noqa: UP036 - keep a clear CLI failure
        raise RuntimeError("ClipForge release checks require Python 3.11 or newer")
    from scripts.media_contract_matrix import run_matrix

    run_matrix(include_browser=not args.media_only)
    run_media_gate()
    if not args.media_only:
        run([sys.executable, "scripts/sync_version.py", "--check"])
        run([sys.executable, "scripts/verify_browser_runtime.py"])
        run_provenance_gate()
        run([sys.executable, "-m", "pytest", "-q"])
        run([sys.executable, "-m", "compileall", "-q", "clipforge", "scripts", "tests"])
        run(["node", "--check", "editor.js"])
        run(["node", "--check", "bootstrap.js"])
        run(["node", "--check", "coi-serviceworker.js"])
        run_gui_smoke()
    if args.build:
        run_build_smoke(args.artifact_dir)
    print("ClipForge release check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
