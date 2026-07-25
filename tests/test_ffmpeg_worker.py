from pathlib import Path

import pytest

from clipforge.tools import FFMPEG, FFPROBE
from clipforge.workers import FFmpegWorker


pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="FFmpeg and ffprobe are required for integration coverage",
)


def run_worker(worker):
    results = []
    worker.finished_signal.connect(lambda ok, message: results.append((ok, message)))
    worker.run()
    assert results
    return results[-1]


def test_ffmpeg_worker_atomically_creates_valid_media(tmp_path):
    output = tmp_path / "quote's sample video.mp4"
    command = [
        FFMPEG,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=160x90:r=24",
        "-t",
        "0.25",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    ok, message = run_worker(
        FFmpegWorker(command, 0.25, output_path=str(output))
    )
    assert ok, message
    assert output.stat().st_size > 0
    assert not list(tmp_path.glob(".*.clipforge-*"))


def test_ffmpeg_worker_preserves_existing_output_on_failure(tmp_path):
    output = tmp_path / "existing.mp4"
    original = b"do-not-replace"
    output.write_bytes(original)
    command = [
        FFMPEG,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=16x16",
        "-vf",
        "filter_that_does_not_exist",
        str(output),
    ]
    ok, _ = run_worker(
        FFmpegWorker(
            command,
            1,
            output_path=str(output),
            overwrite=True,
            timeout=10,
        )
    )
    assert not ok
    assert output.read_bytes() == original
    assert not list(tmp_path.glob(".*.clipforge-*"))


def test_ffmpeg_worker_refuses_unconfirmed_overwrite(tmp_path):
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"original")
    command = [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black", str(output)]
    ok, message = run_worker(
        FFmpegWorker(command, 1, output_path=str(output), overwrite=False)
    )
    assert not ok
    assert message == "Output already exists"
    assert output.read_bytes() == b"original"
