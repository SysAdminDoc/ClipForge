import subprocess
from pathlib import Path

import pytest

from clipforge.processes import OutputValidationContract, validate_output
from clipforge.tools import FFMPEG, FFPROBE, probe_video
from clipforge.workers import _ai_output_contract, _ai_reassembly_command


pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="FFmpeg and ffprobe are required for semantic media coverage",
)


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def _make_source(path, audio_codec=None, audio_tracks=0):
    command = [
        FFMPEG,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=24:duration=0.5",
    ]
    for index in range(audio_tracks):
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={440 + index * 110}:duration=0.5",
        ]
    command += ["-map", "0:v:0"]
    for index in range(audio_tracks):
        command += ["-map", f"{index + 1}:a:0"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_codec:
        command += ["-c:a", audio_codec]
    command.append(str(path))
    _run(command)


@pytest.mark.parametrize(
    ("source_suffix", "source_codec", "audio_tracks"),
    [
        (".mkv", "libopus", 2),
        (".mov", "pcm_s16le", 1),
        (".mp4", None, 0),
    ],
)
def test_ai_reassembly_preserves_source_audio_cardinality(
    tmp_path,
    source_suffix,
    source_codec,
    audio_tracks,
):
    source = tmp_path / f"source{source_suffix}"
    _make_source(source, source_codec, audio_tracks)
    frames = tmp_path / "frames"
    frames.mkdir()
    _run([
        FFMPEG,
        "-y",
        "-i",
        str(source),
        "-an",
        str(frames / "frame_%06d.png"),
    ])
    output = tmp_path / "result.mkv"
    reassembly = _ai_reassembly_command(
        frames / "frame_%06d.png",
        source,
        output,
        24,
    )
    assert "-map" in reassembly and "1:a?" in reassembly
    assert reassembly[reassembly.index("-fps_mode") + 1] == "cfr"
    assert "-sn" in reassembly and "-dn" in reassembly
    _run(reassembly)

    source_info = probe_video(str(source))
    ok, reason = validate_output(
        output,
        ffprobe_path=FFPROBE,
        contract=_ai_output_contract(output, source_info),
    )

    assert ok, reason
    output_info = probe_video(str(output))
    output_audio = [
        stream
        for stream in output_info["streams"]
        if stream["codec_type"] == "audio"
    ]
    assert len(output_audio) == audio_tracks
    assert all(stream["codec_name"] == "aac" for stream in output_audio)


def test_semantic_validation_rejects_missing_stream_and_sidecar(tmp_path):
    output = tmp_path / "video.mp4"
    _make_source(output)
    sidecar = tmp_path / "video.json"
    contract = OutputValidationContract(
        expected_duration=0.5,
        duration_tolerance=0.1,
        stream_counts=(("video", 1, 1), ("audio", 1, 1)),
        allowed_formats=("mov", "mp4"),
        allowed_codecs=(("video", ("h264",)),),
        required_sidecars=(str(sidecar),),
    )

    ok, reason = validate_output(output, ffprobe_path=FFPROBE, contract=contract)
    assert not ok
    assert "sidecar" in reason.lower()

    sidecar.write_text("{}", encoding="utf-8")
    ok, reason = validate_output(output, ffprobe_path=FFPROBE, contract=contract)
    assert not ok
    assert "audio stream" in reason.lower()


def test_semantic_validation_rejects_duration_and_codec_mismatch(tmp_path):
    output = tmp_path / "video.mp4"
    _make_source(output)
    contract = OutputValidationContract(
        expected_duration=10,
        duration_tolerance=0.1,
        stream_counts=(("video", 1, 1),),
        allowed_formats=("mov", "mp4"),
        allowed_codecs=(("video", ("av1",)),),
    )
    ok, reason = validate_output(output, ffprobe_path=FFPROBE, contract=contract)
    assert not ok
    assert "duration" in reason.lower()

    contract = OutputValidationContract(
        expected_duration=0.5,
        duration_tolerance=0.1,
        stream_counts=(("video", 1, 1),),
        allowed_formats=("mov", "mp4"),
        allowed_codecs=(("video", ("av1",)),),
    )
    ok, reason = validate_output(output, ffprobe_path=FFPROBE, contract=contract)
    assert not ok
    assert "codec" in reason.lower()
