import os
import sys
import threading
import time
from pathlib import Path

from clipforge.processes import (
    command_with_staging_output,
    run_managed_process,
    staging_output_path,
    validate_output,
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
