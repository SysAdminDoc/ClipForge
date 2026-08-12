import os
import sys
import threading
import time
from pathlib import Path

from clipforge.processes import (
    AUDIO_ONLY_STREAM_POLICY,
    TRANSCODE_STREAM_POLICY,
    VIDEO_ONLY_STREAM_POLICY,
    command_with_staging_output,
    output_contract_for_streams,
    output_contract_from_dict,
    output_contract_to_dict,
    run_managed_process,
    staging_output_path,
    validate_output,
    WorkerOutcome,
)
from clipforge.tools import (
    _cleanup_temp_dirs,
    _register_temp_dir,
    _unregister_temp_dir,
    write_concat_manifest,
)


def test_managed_process_drains_stdout_and_stderr():
    script = (
        "import sys\n"
        "for i in range(2000):\n"
        " print(f'out-{i}')\n"
        " print(f'err-{i}', file=sys.stderr)\n"
    )
    outcome = run_managed_process([sys.executable, "-c", script], timeout=10)
    assert outcome.returncode == 0
    assert "out-1999" in outcome.stdout
    assert "err-1999" in outcome.stderr


def test_managed_process_bounds_tails_but_callbacks_receive_every_byte():
    counts = {"stdout": 0, "stderr": 0}
    script = (
        "import sys\n"
        "for i in range(4096):\n"
        " print(f'out-{i:04d}-' + 'x' * 128)\n"
        " print(f'err-{i:04d}-' + 'y' * 128, file=sys.stderr)\n"
    )
    outcome = run_managed_process(
        [sys.executable, "-c", script],
        timeout=20,
        max_output_chars=4096,
        stdout_callback=lambda text: counts.__setitem__(
            "stdout", counts["stdout"] + len(text)
        ),
        stderr_callback=lambda text: counts.__setitem__(
            "stderr", counts["stderr"] + len(text)
        ),
    )
    assert outcome.returncode == 0
    assert len(outcome.stdout) <= 4096
    assert len(outcome.stderr) <= 4096
    assert outcome.stdout_truncated
    assert outcome.stderr_truncated
    assert "out-4095-" in outcome.stdout
    assert "err-4095-" in outcome.stderr
    assert counts["stdout"] > len(outcome.stdout)
    assert counts["stderr"] > len(outcome.stderr)


def test_managed_process_bounds_a_single_unterminated_line():
    seen = 0

    def count_output(text):
        nonlocal seen
        seen += len(text)

    outcome = run_managed_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('z' * 2000000)"],
        timeout=20,
        max_output_chars=1024,
        stdout_callback=count_output,
    )
    assert outcome.returncode == 0
    assert outcome.stdout == "z" * 1024
    assert outcome.stdout_truncated
    assert seen == 2_000_000


def test_managed_process_can_spool_full_tagged_output(tmp_path):
    outcome = run_managed_process(
        [
            sys.executable,
            "-c",
            "import sys; print('first-out'); print('first-err', file=sys.stderr); "
            "print('last-out'); print('last-err', file=sys.stderr)",
        ],
        timeout=10,
        max_output_chars=8,
        spool_full_output=True,
        spool_directory=tmp_path,
    )
    assert outcome.full_log_path
    full_log = Path(outcome.full_log_path)
    assert full_log.parent == tmp_path
    content = full_log.read_text(encoding="utf-8")
    assert "[stdout] first-out" in content
    assert "[stdout] last-out" in content
    assert "[stderr] first-err" in content
    assert "[stderr] last-err" in content
    full_log.unlink()


def test_managed_process_cancels_quiet_child_quickly():
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    started = time.monotonic()
    outcome = run_managed_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cancel_event=cancel,
        timeout=10,
    )
    timer.cancel()
    assert outcome.cancelled
    assert time.monotonic() - started < 5


def test_managed_process_times_out():
    outcome = run_managed_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.2,
    )
    assert outcome.timed_out


def test_worker_outcome_serializes_terminal_state_for_queue_adapters():
    outcome = WorkerOutcome(
        "validation_failed",
        "output_invalid",
        "Output failed semantic validation",
        output_path="C:/exports/clip.mp4",
        returncode=0,
        output_valid=False,
        details={"validator": "ffprobe"},
    )
    assert outcome.succeeded is False
    assert outcome.as_dict() == {
        "state": "validation_failed",
        "reason_code": "output_invalid",
        "message": "Output failed semantic validation",
        "output_path": "C:/exports/clip.mp4",
        "returncode": 0,
        "output_valid": False,
        "cancelled": False,
        "timed_out": False,
        "full_log_path": None,
        "details": {"validator": "ffprobe"},
    }


def test_stream_selection_policies_emit_explicit_maps_and_timing_rules():
    transcode = TRANSCODE_STREAM_POLICY.ffmpeg_args()
    assert transcode[:4] == ["-map", "0:v:0", "-map", "0:a?"]
    assert "-sn" in transcode
    assert "-dn" in transcode
    assert ["-map_metadata", "0"] == transcode[transcode.index("-map_metadata"):transcode.index("-map_metadata") + 2]
    assert "-fps_mode" in transcode

    video_only = VIDEO_ONLY_STREAM_POLICY.ffmpeg_args()
    assert "-map" in video_only
    assert "-an" in video_only
    assert "-fps_mode" in video_only

    audio_only = AUDIO_ONLY_STREAM_POLICY.ffmpeg_args()
    assert "-vn" in audio_only
    audio_map = audio_only.index("-map")
    assert audio_only[audio_map:audio_map + 2] == ["-map", "0:a:0?"]


def test_output_contract_round_trips_explicit_stream_cardinality(tmp_path):
    contract = output_contract_for_streams(
        tmp_path / "output.mkv",
        expected_duration=2.5,
        video_count=1,
        audio_count=2,
        subtitle_count=0,
        allowed_codecs=(("video", ("h264",)),),
    )
    restored = output_contract_from_dict(output_contract_to_dict(contract))
    assert restored == contract


def test_managed_process_uses_requested_working_directory(tmp_path):
    outcome = run_managed_process(
        [sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"],
        cwd=tmp_path,
        timeout=10,
    )
    assert outcome.returncode == 0
    assert Path(outcome.stdout.strip()).resolve() == tmp_path.resolve()


def test_staging_command_replaces_only_last_output(tmp_path):
    output = tmp_path / "video with spaces.mp4"
    staged = staging_output_path(output)
    command = ["ffmpeg", "-i", str(output), str(output)]
    result = command_with_staging_output(command, output, staged)
    assert result[-2] == str(output)
    assert result[-1] == str(staged)
    assert staged.suffix == ".mp4"


def test_validate_output_rejects_empty_file(tmp_path):
    output = tmp_path / "empty.mp4"
    output.touch()
    ok, reason = validate_output(output)
    assert not ok
    assert "empty" in reason.lower()


def test_cleanup_removes_only_registered_temp_dirs(tmp_path):
    owned = tmp_path / "owned"
    foreign = tmp_path / "foreign"
    owned.mkdir()
    foreign.mkdir()
    _register_temp_dir(owned)
    _cleanup_temp_dirs()
    assert not owned.exists()
    assert foreign.exists()
    _unregister_temp_dir(owned)


def test_concat_manifest_escapes_quote_and_uses_absolute_paths(tmp_path):
    media = tmp_path / "clip's sample.mp4"
    media.touch()
    manifest = tmp_path / "concat.txt"
    write_concat_manifest([media], manifest)
    content = manifest.read_text(encoding="utf-8")
    assert content.startswith("file '")
    assert "clip'\\''s sample.mp4" in content
    assert Path(media).resolve().as_posix().replace("'", r"'\''") in content
