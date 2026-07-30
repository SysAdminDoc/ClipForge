#!/usr/bin/env python3
"""Local release gate with deterministic FFmpeg fixtures and optional build."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge.processes import terminate_process_tree, validate_output
from clipforge.tools import FFMPEG, FFPROBE, probe_video, write_concat_manifest


def run(command, *, timeout=120, env=None):
    result = subprocess.run(
        [str(part) for part in command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
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
    run([sys.executable, "-c", code], timeout=30, env=env)


def run_build_smoke():
    with tempfile.TemporaryDirectory(prefix="clipforge-release-build-") as temp:
        temp_path = Path(temp)
        run([
            "pyinstaller", "--noconfirm", "--clean",
            "--distpath", temp_path / "dist",
            "--workpath", temp_path / "build",
            ROOT / "ClipForge.spec",
        ], timeout=300)
        executable = temp_path / "dist" / (
            "ClipForge.exe" if sys.platform == "win32" else "ClipForge"
        )
        if not executable.is_file():
            raise RuntimeError("PyInstaller artifact was not created")
        process = subprocess.Popen([str(executable)], cwd=ROOT)
        try:
            time.sleep(4)
            if process.poll() is not None:
                raise RuntimeError(
                    f"Packaged application exited early with {process.returncode}"
                )
        finally:
            terminate_process_tree(process)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--media-only", action="store_true")
    args = parser.parse_args()

    run_media_gate()
    if not args.media_only:
        run([sys.executable, "scripts/sync_version.py", "--check"])
        run([sys.executable, "scripts/verify_browser_runtime.py"])
        run([sys.executable, "-m", "pytest", "-q"])
        run([sys.executable, "-m", "compileall", "-q", "clipforge", "scripts", "tests"])
        run(["node", "--check", "editor.js"])
        run(["node", "--check", "bootstrap.js"])
        run(["node", "--check", "coi-serviceworker.js"])
        run_gui_smoke()
    if args.build:
        run_build_smoke()
    print("ClipForge release check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
