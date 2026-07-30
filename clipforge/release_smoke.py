"""Packaged-artifact media smoke used by the local release gate."""

from __future__ import annotations

import argparse
from pathlib import Path

from .processes import run_managed_process, validate_output
from .tools import FFMPEG, FFPROBE, probe_video


def transcode_smoke(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if not source.is_file() or not probe_video(str(source)):
        raise RuntimeError(f"Could not open release-smoke media: {source}")
    if not FFMPEG or not FFPROBE:
        raise RuntimeError("FFmpeg and ffprobe are required for release smoke")
    outcome = run_managed_process(
        [
            FFMPEG,
            "-y",
            "-ss",
            "0.1",
            "-i",
            source,
            "-t",
            "0.25",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            output,
        ],
        timeout=30,
    )
    if outcome.returncode != 0:
        raise RuntimeError(
            f"Release-smoke transcode failed ({outcome.returncode}): "
            f"{outcome.stderr[-1000:]}"
        )
    valid, reason = validate_output(output, ffprobe_path=FFPROBE)
    if not valid:
        raise RuntimeError(f"Release-smoke output is invalid: {reason}")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args(argv)
    transcode_smoke(args.source, args.output)
    return 0
