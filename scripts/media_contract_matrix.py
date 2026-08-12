"""Deterministic desktop/media contracts shared by the release gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clipforge.job_queue import JobQueue, JobRecord
from clipforge.processes import (
    TRANSCODE_STREAM_POLICY,
    VIDEO_ONLY_STREAM_POLICY,
    output_contract_for_streams,
    run_managed_process,
    validate_output,
)
from clipforge.tools import FFMPEG, FFPROBE, probe_video
from clipforge.workers import _ai_output_contract, _ai_reassembly_command


def _run(command, *, timeout=120):
    result = subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Media contract command failed ({result.returncode}): "
            f"{' '.join(map(str, command))}\n{result.stderr[-2000:]}"
        )
    return result


def _make_fixtures(root: Path):
    subtitle = root / "matrix.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,800\nMatrix subtitle\n",
        encoding="utf-8",
    )
    chapters = root / "matrix.ffmeta"
    chapters.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=Intro\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\ntitle=Outro\n",
        encoding="utf-8",
    )
    multistream = root / "multistream.mkv"
    _run([
        FFMPEG,
        "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        "-f", "srt", "-i", subtitle,
        "-i", chapters,
        "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-map", "3:0",
        "-map_metadata", "4", "-map_chapters", "4",
        "-metadata", "title=ClipForge Matrix Source",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:1", "language=spa",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-c:s", "srt", "-shortest", multistream,
    ])
    vfr = root / "vfr.mkv"
    _run([
        FFMPEG,
        "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=0.5",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=0.5",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-fps_mode", "vfr", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", vfr,
    ])
    browser_source = root / "browser-source.mp4"
    _run([
        FFMPEG, "-y", "-i", multistream,
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        browser_source,
    ])
    return multistream, vfr, browser_source


def _require_contract(path, contract):
    valid, reason = validate_output(
        path,
        ffprobe_path=FFPROBE,
        contract=contract,
    )
    if not valid:
        raise RuntimeError(f"{path.name} violated its media contract: {reason}")
    info = probe_video(str(path))
    if not info:
        raise RuntimeError(f"Could not probe matrix output: {path.name}")
    return info


def _assert_timing(source_info, output_info, *, tolerance=0.2):
    source_duration = float(source_info.get("duration") or 0)
    output_duration = float(output_info.get("duration") or 0)
    if source_duration <= 0 or output_duration <= 0:
        raise RuntimeError("Media contract output has no usable duration")
    if abs(source_duration - output_duration) > tolerance:
        raise RuntimeError(
            f"Duration drift exceeded {tolerance}s: "
            f"{source_duration:.3f}s -> {output_duration:.3f}s"
        )
    for stream in output_info.get("streams", []):
        start_time = float(stream.get("start_time") or 0)
        if abs(start_time) > tolerance:
            raise RuntimeError("Output stream timestamps are not normalized")


def _assert_metadata(source_info, output_info):
    source_title = (source_info.get("tags") or {}).get("title")
    output_title = (output_info.get("tags") or {}).get("title")
    if source_title and output_title != source_title:
        raise RuntimeError("Output metadata title was not preserved")
    if len(output_info.get("chapters", [])) != len(source_info.get("chapters", [])):
        raise RuntimeError("Output chapter count changed unexpectedly")


def _run_desktop_matrix(root, multistream, vfr):
    source_info = probe_video(str(multistream))
    vfr_info = probe_video(str(vfr))
    if not source_info or not vfr_info:
        raise RuntimeError("Could not probe media contract fixtures")
    source_audio_count = sum(
        stream.get("codec_type") == "audio"
        for stream in source_info.get("streams", [])
    )

    converted = root / "desktop-converted.mkv"
    _run([
        FFMPEG, "-y", "-i", multistream,
        *TRANSCODE_STREAM_POLICY.ffmpeg_args(),
        "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
        converted,
    ])
    converted_info = _require_contract(
        converted,
        output_contract_for_streams(
            converted,
            expected_duration=source_info["duration"],
            video_count=1,
            audio_count=source_audio_count,
            subtitle_count=0,
        ),
    )
    _assert_timing(source_info, converted_info)
    _assert_metadata(source_info, converted_info)

    vfr_output = root / "desktop-vfr.mkv"
    _run([
        FFMPEG, "-y", "-i", vfr,
        *VIDEO_ONLY_STREAM_POLICY.ffmpeg_args(),
        "-c:v", "libx264", "-crf", "18", vfr_output,
    ])
    vfr_output_info = _require_contract(
        vfr_output,
        output_contract_for_streams(
            vfr_output,
            expected_duration=vfr_info["duration"],
            video_count=1,
            audio_count=0,
            subtitle_count=0,
        ),
    )
    _assert_timing(vfr_info, vfr_output_info, tolerance=0.25)

    frame_dir = root / "ai-frames"
    frame_dir.mkdir()
    _run([
        FFMPEG, "-y", "-i", multistream,
        "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
        frame_dir / "frame_%06d.png",
    ])
    ai_output = root / "ai-reassembled.mkv"
    _run(_ai_reassembly_command(
        frame_dir / "frame_%06d.png",
        multistream,
        ai_output,
        source_info["fps"] or 24,
    ))
    ai_info = _require_contract(ai_output, _ai_output_contract(ai_output, source_info))
    if sum(stream.get("codec_type") == "audio" for stream in ai_info["streams"]) != source_audio_count:
        raise RuntimeError("AI contract did not preserve source audio cardinality")
    _assert_timing(source_info, ai_info, tolerance=0.25)
    _assert_metadata(source_info, ai_info)


def _run_reliability_matrix(root, source):
    cancel_event = threading.Event()
    timer = threading.Timer(0.15, cancel_event.set)
    timer.start()
    try:
        outcome = run_managed_process(
            [
                FFMPEG, "-re", "-f", "lavfi",
                "-i", "testsrc2=size=160x90:rate=24",
                "-t", "30", "-f", "null", "-",
            ],
            cancel_event=cancel_event,
            timeout=40,
        )
    finally:
        timer.cancel()
    if not outcome.cancelled:
        raise RuntimeError("Cancellation matrix did not cancel the managed process")

    queue_path = root / "interrupted-queue.json"
    queue_output = root / "queue-output.mkv"
    queue = JobQueue(queue_path)
    queued = JobRecord.create(
        source,
        queue_output,
        "Matrix conversion",
        [FFMPEG, "-i", str(source), str(queue_output)],
        output_contract=output_contract_for_streams(
            queue_output,
            expected_duration=1,
            video_count=1,
            audio_count=2,
            subtitle_count=0,
        ),
    )
    queue.add([queued])
    queue.activate()
    if queue.claim_next() is None:
        raise RuntimeError("Queue matrix could not claim its test job")
    restored = JobQueue(queue_path)
    if restored.jobs[0].state != "interrupted":
        raise RuntimeError("Running queue jobs were not recovered as interrupted")

    invalid_queue = JobQueue(root / "invalid-output-queue.json")
    invalid_output = root / "missing-output.mkv"
    invalid_job = JobRecord.create(
        source,
        invalid_output,
        "Invalid output",
        [FFMPEG, "-i", str(source), str(invalid_output)],
        output_contract=output_contract_for_streams(
            invalid_output,
            expected_duration=1,
            video_count=1,
            audio_count=2,
            subtitle_count=0,
        ),
    )
    invalid_queue.add([invalid_job])
    invalid_queue.activate()
    if invalid_queue.claim_next() is None:
        raise RuntimeError("Invalid-output matrix could not claim its test job")
    failed = invalid_queue.complete(invalid_job.job_id, True)
    if failed.state != "failed" or "not created" not in failed.error.lower():
        raise RuntimeError("Invalid output was not rejected by the queue contract")


def _run_browser_matrix(root: Path, source: Path, source_info, desktop_output: Path):
    """Export one supported contiguous project and compare it with desktop output."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the browser media matrix") from exc

    class _IsolatedHandler(SimpleHTTPRequestHandler):
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".mjs": "application/javascript",
        }

        def end_headers(self):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            super().end_headers()

        def log_message(self, _format, *_args):
            pass

        def copyfile(self, source_file, output_file):
            try:
                super().copyfile(source_file, output_file)
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_IsolatedHandler, directory=str(Path(__file__).resolve().parents[1])),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    project = {
        "schema": "clipforge.project",
        "version": 1,
        "name": "Release media matrix",
        "media": [{
            "id": "media-source",
            "name": source.name,
            "type": "video",
            "duration": float(source_info.get("duration") or 0),
            "width": int(source_info.get("width") or 160),
            "height": int(source_info.get("height") or 90),
            "reference": {
                "name": source.name,
                "size": source.stat().st_size,
                "lastModified": 0,
                "mime": "video/mp4",
            },
        }],
        "clips": [{
            "id": "video-clip",
            "mediaId": "media-source",
            "track": "video",
            "startTime": 0,
            "duration": float(source_info.get("duration") or 0),
            "inPoint": 0,
            "outPoint": float(source_info.get("duration") or 0),
            "name": "Matrix source",
            "type": "video",
            "linkedTo": "audio-clip",
        }, {
            "id": "audio-clip",
            "mediaId": "media-source",
            "track": "audio",
            "startTime": 0,
            "duration": float(source_info.get("duration") or 0),
            "inPoint": 0,
            "outPoint": float(source_info.get("duration") or 0),
            "name": "Matrix source audio",
            "type": "audio",
            "linkedTo": "video-clip",
        }],
        "transitions": [],
        "timeline": {"pixelsPerSecond": 50, "trackStates": {}},
    }
    browser_output = root / "browser-export.mp4"
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                accept_downloads=True,
                viewport={"width": 900, "height": 700},
            )
            page = context.new_page()
            page.goto(
                f"http://127.0.0.1:{server.server_port}/index.html",
                wait_until="domcontentloaded",
            )
            page.wait_for_function(
                "() => window.clipforgeEditorReady === true",
                timeout=30_000,
            )
            page.wait_for_function(
                "() => window.getBrowserFfmpegJobState?.().engineReady === true",
                timeout=120_000,
            )
            page.locator("#projectFileInput").set_input_files({
                "name": "matrix.clipforge",
                "mimeType": "application/json",
                "buffer": json.dumps(project).encode(),
            })
            page.wait_for_function(
                "() => document.querySelector('.project-name')?.textContent "
                "=== 'Release media matrix'"
            )
            preflight = page.evaluate("() => window.buildExportPreflight()")
            if preflight["supported"]:
                raise RuntimeError("Missing browser source was not blocked by preflight")
            if not any("relink" in reason.lower() for reason in preflight["reasons"]):
                raise RuntimeError("Browser preflight did not explain missing media")
            page.locator("#relinkFileInput").set_input_files(str(source))
            page.wait_for_function(
                "() => document.querySelector('.media-item:not(.missing)')"
            )
            preflight = page.evaluate("() => window.buildExportPreflight()")
            if not preflight["supported"]:
                raise RuntimeError(
                    "Relinked browser project remained blocked: "
                    + "; ".join(preflight["reasons"])
                )
            page.locator("#exportButton").click()
            page.locator("#confirmExportButton").wait_for()
            if not page.locator("#confirmExportButton").is_enabled():
                raise RuntimeError("Supported browser project could not export")
            with page.expect_download(timeout=120_000) as download_info:
                page.locator("#confirmExportButton").click()
            download_info.value.save_as(browser_output)
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    browser_info = _require_contract(
        browser_output,
        output_contract_for_streams(
            browser_output,
            expected_duration=source_info["duration"],
            video_count=1,
            audio_count=1,
            subtitle_count=0,
        ),
    )
    desktop_info = probe_video(str(desktop_output))
    if not desktop_info:
        raise RuntimeError("Desktop comparison output could not be probed")
    _assert_timing(source_info, browser_info, tolerance=0.3)
    if abs(
        float(browser_info.get("duration") or 0)
        - float(desktop_info.get("duration") or 0)
    ) > 0.3:
        raise RuntimeError("Browser and desktop durations diverged")
    return browser_info


def run_matrix(*, include_browser=True):
    """Run the cross-surface desktop contract and recovery matrix."""
    if not FFMPEG or not FFPROBE:
        raise RuntimeError("FFmpeg and ffprobe are required for the media matrix")
    with tempfile.TemporaryDirectory(prefix="clipforge-contract-matrix-") as temp:
        root = Path(temp)
        multistream, vfr, browser_source = _make_fixtures(root)
        _run_desktop_matrix(root, multistream, vfr)
        _run_reliability_matrix(root, multistream)
        if include_browser:
            desktop_output = root / "desktop-browser-comparison.mkv"
            source_info = probe_video(str(browser_source))
            _run([
                FFMPEG, "-y", "-i", browser_source,
                *TRANSCODE_STREAM_POLICY.ffmpeg_args(),
                "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                desktop_output,
            ])
            _run_browser_matrix(root, browser_source, source_info, desktop_output)


if __name__ == "__main__":
    run_matrix()
    print("ClipForge media contract matrix passed")
