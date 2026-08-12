"""Bounded subprocess execution and atomic output helpers."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
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
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    full_log_path: str | None = None


@dataclass(frozen=True)
class WorkerOutcome:
    """Stable terminal result shared by desktop media workers and the queue."""

    state: str
    reason_code: str
    message: str
    output_path: str | None = None
    returncode: int | None = None
    output_valid: bool | None = None
    cancelled: bool = False
    timed_out: bool = False
    full_log_path: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def succeeded(self):
        return self.state == "succeeded"

    def as_dict(self):
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "message": self.message,
            "output_path": self.output_path,
            "returncode": self.returncode,
            "output_valid": self.output_valid,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
            "full_log_path": self.full_log_path,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class OutputValidationContract:
    """Semantic requirements that must hold before an output is committed."""

    expected_duration: float | None = None
    duration_tolerance: float = 1.0
    stream_counts: tuple[tuple[str, int, int | None], ...] = ()
    allowed_formats: tuple[str, ...] = ()
    allowed_codecs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    required_sidecars: tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamSelectionPolicy:
    """Declare which input streams and timing metadata a generated output uses."""

    video: str = "first"
    audio: str = "all"
    subtitles: str = "drop"
    data: str = "drop"
    metadata: str = "copy"
    chapters: str = "copy"
    timestamps: str = "preserve"

    def ffmpeg_args(self, input_index=0):
        """Return output-side FFmpeg arguments for this selection policy."""
        prefix = str(input_index)
        args = []
        if self.video == "first":
            args += ["-map", f"{prefix}:v:0"]
        elif self.video == "all":
            args += ["-map", f"{prefix}:v?"]
        elif self.video == "none":
            args.append("-vn")
        else:
            raise ValueError(f"Unsupported video selection policy: {self.video}")

        if self.audio == "all":
            args += ["-map", f"{prefix}:a?"]
        elif self.audio == "first":
            args += ["-map", f"{prefix}:a:0?"]
        elif self.audio == "none":
            args.append("-an")
        else:
            raise ValueError(f"Unsupported audio selection policy: {self.audio}")

        if self.subtitles == "drop":
            args.append("-sn")
        elif self.subtitles != "copy":
            raise ValueError(
                f"Unsupported subtitle selection policy: {self.subtitles}"
            )
        if self.data == "drop":
            args.append("-dn")
        elif self.data != "copy":
            raise ValueError(f"Unsupported data selection policy: {self.data}")
        if self.metadata == "copy":
            args += ["-map_metadata", prefix]
        elif self.metadata == "drop":
            args += ["-map_metadata", "-1"]
        else:
            raise ValueError(f"Unsupported metadata policy: {self.metadata}")
        if self.chapters == "copy":
            args += ["-map_chapters", prefix]
        elif self.chapters == "drop":
            args += ["-map_chapters", "-1"]
        else:
            raise ValueError(f"Unsupported chapter policy: {self.chapters}")
        if self.timestamps == "preserve":
            args += ["-fps_mode", "passthrough"]
        elif self.timestamps == "reset":
            args += ["-avoid_negative_ts", "make_zero"]
        else:
            raise ValueError(f"Unsupported timestamp policy: {self.timestamps}")
        return args


TRANSCODE_STREAM_POLICY = StreamSelectionPolicy()
VIDEO_ONLY_STREAM_POLICY = StreamSelectionPolicy(audio="none")
AUDIO_ONLY_STREAM_POLICY = StreamSelectionPolicy(
    video="none",
    audio="first",
    metadata="copy",
    chapters="copy",
)


def output_contract_for_streams(
    output_path,
    *,
    expected_duration=0,
    video_count=1,
    audio_count=None,
    subtitle_count=0,
    allowed_codecs=(),
):
    """Build a semantic contract from an explicit stream-selection decision."""
    suffix = Path(output_path).suffix.lower()
    if suffix in _AUDIO_ONLY_SUFFIXES:
        video_count = 0
        audio_count = 1 if audio_count is None else audio_count
    stream_counts = [("video", video_count, video_count)]
    if audio_count is not None:
        stream_counts.append(("audio", audio_count, audio_count))
    if subtitle_count is not None:
        stream_counts.append(("subtitle", subtitle_count, subtitle_count))
    duration = float(expected_duration or 0)
    return OutputValidationContract(
        expected_duration=duration if duration > 0 else None,
        duration_tolerance=max(1.0, duration * 0.03),
        stream_counts=tuple(stream_counts),
        allowed_formats=_OUTPUT_FORMAT_POLICIES.get(suffix, ()),
        allowed_codecs=tuple(allowed_codecs),
    )


_OUTPUT_FORMAT_POLICIES = {
    ".mp4": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"),
    ".m4v": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"),
    ".mov": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"),
    ".mkv": ("matroska", "webm"),
    ".webm": ("matroska", "webm"),
    ".avi": ("avi",),
    ".gif": ("gif",),
    ".wav": ("wav",),
    ".mp3": ("mp3",),
    ".flac": ("flac",),
    ".ogg": ("ogg",),
    ".opus": ("ogg",),
    ".aac": ("aac",),
    ".jpg": ("image2",),
    ".jpeg": ("image2",),
    ".png": ("image2",),
    ".webp": ("webp", "image2"),
}

_AUDIO_ONLY_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".aac"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def default_output_contract(
    output_path: str | os.PathLike[str],
    *,
    expected_duration: float = 0,
) -> OutputValidationContract:
    """Declare conservative semantic checks for a normal generated output."""
    suffix = Path(output_path).suffix.lower()
    if suffix in _AUDIO_ONLY_SUFFIXES:
        streams = (("audio", 1, 1),)
    elif suffix in _IMAGE_SUFFIXES:
        streams = (("video", 1, 1),)
    else:
        streams = (("video", 1, None),)
    duration = float(expected_duration or 0)
    return OutputValidationContract(
        expected_duration=duration if duration > 0 else None,
        duration_tolerance=max(1.0, duration * 0.03),
        stream_counts=streams,
        allowed_formats=_OUTPUT_FORMAT_POLICIES.get(suffix, ()),
        required_sidecars=(),
    )


def output_contract_to_dict(contract):
    """Serialize an output contract into bounded JSON-compatible values."""
    if contract is None:
        return None
    return {
        "expected_duration": contract.expected_duration,
        "duration_tolerance": contract.duration_tolerance,
        "stream_counts": [list(item) for item in contract.stream_counts],
        "allowed_formats": list(contract.allowed_formats),
        "allowed_codecs": [
            [stream_type, list(codecs)]
            for stream_type, codecs in contract.allowed_codecs
        ],
        "required_sidecars": list(contract.required_sidecars),
    }


def output_contract_from_dict(value):
    """Parse a persisted output contract, rejecting malformed queue data."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("output contract must be an object")
    stream_counts = value.get("stream_counts", [])
    allowed_codecs = value.get("allowed_codecs", [])
    if not isinstance(stream_counts, list) or not isinstance(allowed_codecs, list):
        raise ValueError("output contract stream policies must be lists")
    parsed_counts = []
    for item in stream_counts:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("output contract stream count is invalid")
        parsed_counts.append((str(item[0]), int(item[1]), item[2]))
    parsed_codecs = []
    for item in allowed_codecs:
        if not isinstance(item, list) or len(item) != 2 or not isinstance(item[1], list):
            raise ValueError("output contract codec policy is invalid")
        parsed_codecs.append((str(item[0]), tuple(str(codec) for codec in item[1])))
    formats = value.get("allowed_formats", [])
    sidecars = value.get("required_sidecars", [])
    if not isinstance(formats, list) or not isinstance(sidecars, list):
        raise ValueError("output contract formats or sidecars are invalid")
    return OutputValidationContract(
        expected_duration=value.get("expected_duration"),
        duration_tolerance=value.get("duration_tolerance", 1.0),
        stream_counts=tuple(parsed_counts),
        allowed_formats=tuple(str(item) for item in formats),
        allowed_codecs=tuple(parsed_codecs),
        required_sidecars=tuple(str(item) for item in sidecars),
    )


class _TextTail:
    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.chunks = deque()
        self.length = 0
        self.truncated = False

    def append(self, text):
        if not text:
            return
        if self.limit == 0:
            self.truncated = True
            return
        if len(text) >= self.limit:
            self.truncated = self.truncated or self.length > 0 or len(text) > self.limit
            self.chunks.clear()
            tail = text[-self.limit:]
            self.chunks.append(tail)
            self.length = len(tail)
            return
        while self.length + len(text) > self.limit:
            excess = self.length + len(text) - self.limit
            first = self.chunks[0]
            self.truncated = True
            if len(first) <= excess:
                self.chunks.popleft()
                self.length -= len(first)
            else:
                self.chunks[0] = first[excess:]
                self.length -= excess
        self.chunks.append(text)
        self.length += len(text)

    def text(self):
        return "".join(self.chunks)


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
    cwd: str | os.PathLike[str] | None = None,
    max_output_chars: int = 256 * 1024,
    spool_full_output: bool = False,
    spool_directory: str | os.PathLike[str] | None = None,
) -> ProcessOutcome:
    """Run a process with bounded tails, optional full spooling, and cancellation."""
    if max_output_chars < 0:
        raise ValueError("max_output_chars must be zero or greater")
    process = subprocess.Popen(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        cwd=os.fspath(cwd) if cwd else None,
        **_popen_group_options(),
    )
    events: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=256)

    def drain(name: str, stream) -> None:
        try:
            while True:
                chunk = stream.readline(64 * 1024)
                if chunk == "":
                    break
                events.put((name, chunk))
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
    stdout_tail = _TextTail(max_output_chars)
    stderr_tail = _TextTail(max_output_chars)
    cancelled = False
    timed_out = False
    termination_requested = False
    spool_handle = None
    spool_path = None
    if spool_full_output:
        spool_handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="replace",
            prefix="clipforge-process-",
            suffix=".log",
            dir=os.fspath(spool_directory) if spool_directory else None,
            delete=False,
        )
        spool_path = spool_handle.name

    try:
        while open_streams or process.poll() is None:
            if (
                not termination_requested
                and cancel_event is not None
                and cancel_event.is_set()
            ):
                cancelled = True
                termination_requested = True
                terminate_process_tree(process)
            elif (
                not termination_requested
                and timeout > 0
                and time.monotonic() - started >= timeout
            ):
                timed_out = True
                termination_requested = True
                terminate_process_tree(process)

            try:
                stream_name, line = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            if spool_handle:
                spool_handle.write(f"[{stream_name}] {line}")
            if stream_name == "stdout":
                stdout_tail.append(line)
                if stdout_callback:
                    stdout_callback(line)
            else:
                stderr_tail.append(line)
                if stderr_callback:
                    stderr_callback(line)
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        for reader in readers:
            reader.join(timeout=1)
        if spool_handle:
            spool_handle.close()

    return ProcessOutcome(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout_tail.text(),
        stderr=stderr_tail.text(),
        cancelled=cancelled,
        timed_out=timed_out,
        stdout_truncated=stdout_tail.truncated,
        stderr_truncated=stderr_tail.truncated,
        full_log_path=spool_path,
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
    contract: OutputValidationContract | None = None,
) -> tuple[bool, str]:
    path = Path(output_path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False, "Output file was not created or is empty"
    except OSError as exc:
        return False, f"Could not inspect output: {exc}"
    if contract:
        for sidecar in contract.required_sidecars:
            sidecar_path = Path(sidecar)
            try:
                if not sidecar_path.is_file() or sidecar_path.stat().st_size <= 0:
                    return False, f"Required sidecar was not created: {sidecar_path.name}"
            except OSError as exc:
                return False, f"Could not inspect sidecar {sidecar_path.name}: {exc}"
    if not ffprobe_path:
        if contract:
            return False, "FFprobe is required for semantic output validation"
        return True, ""
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
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
    try:
        probe = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return False, "Output metadata returned by ffprobe was invalid"
    if not probe.get("format") or not probe.get("streams"):
        return False, "Output contains no readable media streams"
    if not contract:
        return True, ""

    format_names = {
        name.strip().lower()
        for name in str(probe["format"].get("format_name", "")).split(",")
        if name.strip()
    }
    allowed_formats = {name.lower() for name in contract.allowed_formats}
    if allowed_formats and not format_names.intersection(allowed_formats):
        actual = ", ".join(sorted(format_names)) or "unknown"
        return False, f"Output container {actual} violates the declared format policy"

    if contract.expected_duration is not None:
        try:
            actual_duration = float(probe["format"].get("duration") or 0)
        except (TypeError, ValueError):
            actual_duration = 0
        delta = abs(actual_duration - contract.expected_duration)
        if actual_duration <= 0 or delta > contract.duration_tolerance:
            return (
                False,
                "Output duration "
                f"{actual_duration:.3f}s differs from expected "
                f"{contract.expected_duration:.3f}s by more than "
                f"{contract.duration_tolerance:.3f}s",
            )

    streams = probe.get("streams", [])
    for stream_type, minimum, maximum in contract.stream_counts:
        count = sum(1 for stream in streams if stream.get("codec_type") == stream_type)
        if count < minimum or (maximum is not None and count > maximum):
            expected = (
                str(minimum)
                if maximum == minimum
                else f"{minimum}..{maximum if maximum is not None else 'many'}"
            )
            return (
                False,
                f"Output has {count} {stream_type} stream(s); expected {expected}",
            )

    for stream_type, codecs in contract.allowed_codecs:
        allowed = {codec.lower() for codec in codecs}
        offenders = sorted({
            str(stream.get("codec_name") or "unknown")
            for stream in streams
            if stream.get("codec_type") == stream_type
            and str(stream.get("codec_name") or "unknown").lower() not in allowed
        })
        if offenders:
            return (
                False,
                f"Output {stream_type} codec(s) {', '.join(offenders)} "
                "violate the declared codec policy",
            )
    return True, ""
