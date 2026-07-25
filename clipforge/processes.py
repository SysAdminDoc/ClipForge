"""Bounded subprocess execution and atomic output helpers."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


LineCallback = Callable[[str], None]


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False


def _popen_group_options() -> dict:
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        }
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen, grace_seconds: float = 2.0) -> None:
    """Terminate a child process tree, escalating to a hard kill."""
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=max(grace_seconds, 1.0),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=grace_seconds)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def run_managed_process(
    command: Iterable[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout: float = 3600,
    stdout_callback: LineCallback | None = None,
    stderr_callback: LineCallback | None = None,
) -> ProcessOutcome:
    """Run a process while draining both pipes and enforcing cancel/timeout."""
    process = subprocess.Popen(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        **_popen_group_options(),
    )
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def drain(name: str, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((name, line))
        finally:
            events.put((name, None))
            stream.close()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    open_streams = len(readers)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    cancelled = False
    timed_out = False

    try:
        while open_streams or process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                terminate_process_tree(process)
            elif timeout > 0 and time.monotonic() - started >= timeout:
                timed_out = True
                terminate_process_tree(process)

            try:
                stream_name, line = events.get(timeout=0.05)
            except queue.Empty:
                if cancelled or timed_out:
                    break
                continue
            if line is None:
                open_streams -= 1
                continue
            if stream_name == "stdout":
                stdout_lines.append(line)
                if stdout_callback:
                    stdout_callback(line)
            else:
                stderr_lines.append(line)
                if stderr_callback:
                    stderr_callback(line)
            if cancelled or timed_out:
                break
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        for reader in readers:
            reader.join(timeout=1)

    return ProcessOutcome(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        cancelled=cancelled,
        timed_out=timed_out,
    )


def staging_output_path(output_path: str | os.PathLike[str]) -> Path:
    final_path = Path(output_path)
    token = uuid.uuid4().hex
    return final_path.with_name(
        f".{final_path.stem}.clipforge-{token}{final_path.suffix}"
    )


def command_with_staging_output(
    command: Iterable[str],
    output_path: str | os.PathLike[str],
    staging_path: str | os.PathLike[str],
) -> list[str]:
    final_text = os.fspath(output_path)
    staged_text = os.fspath(staging_path)
    parts = [str(part) for part in command]
    indexes = [index for index, part in enumerate(parts) if part == final_text]
    if not indexes:
        raise ValueError("Output path is not present in the command")
    parts[indexes[-1]] = staged_text
    return parts


def validate_output(
    output_path: str | os.PathLike[str],
    *,
    ffprobe_path: str | None = None,
) -> tuple[bool, str]:
    path = Path(output_path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False, "Output file was not created or is empty"
    except OSError as exc:
        return False, f"Could not inspect output: {exc}"
    if not ffprobe_path:
        return True, ""
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=format_name",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Could not validate output: {exc}"
    if result.returncode != 0 or not result.stdout.strip():
        return False, "Output could not be parsed by ffprobe"
    return True, ""
