"""Bounded, structured diagnostics and privacy-safe support export."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path

from . import APP_NAME, APP_VERSION


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def classify_severity(message):
    text = message.lower()
    if re.search(r"\b(error|failed|fatal)\b", text):
        return "error"
    if re.search(r"\b(warn(?:ing)?|timed out)\b", text):
        return "warning"
    return "info"


_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\|\\\\)[^\r\n\"']+")
_POSIX_PATH = re.compile(r"(?<![\w:/])/(?:[^/\r\n\"']+/)+[^/\r\n\"']*")


def redact_text(value):
    text = str(value)
    text = _WINDOWS_PATH.sub("<redacted-path>", text)
    return _POSIX_PATH.sub("<redacted-path>", text)


def _redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


class DiagnosticsStore:
    def __init__(
        self,
        *,
        max_jobs=100,
        max_job_logs=500,
        max_events=2000,
        max_log_chars=16 * 1024,
    ):
        self.max_jobs = max_jobs
        self.max_job_logs = max_job_logs
        self.max_log_chars = max_log_chars
        self._jobs = OrderedDict()
        self._events = deque(maxlen=max_events)
        self._tool_versions = {}
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._jobs.clear()
            self._events.clear()

    def tool_version(self, name, executable):
        key = (name, str(executable or ""))
        with self._lock:
            if key in self._tool_versions:
                return self._tool_versions[key]
        if not executable:
            version = "unavailable"
        else:
            try:
                result = subprocess.run(
                    [str(executable), "-version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    ),
                )
                lines = (result.stdout or result.stderr).splitlines()
                version = lines[0] if lines else f"exit {result.returncode}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                version = f"unavailable: {exc}"
        with self._lock:
            self._tool_versions[key] = version
        return version

    def start_job(self, kind, command, *, tools=None, context=None):
        job_id = uuid.uuid4().hex[:12]
        record = {
            "id": job_id,
            "kind": kind,
            "state": "running",
            "started_at": _utc_now(),
            "ended_at": None,
            "command": [str(part) for part in command],
            "tools": dict(tools or {}),
            "context": dict(context or {}),
            "result": {},
            "logs": deque(maxlen=self.max_job_logs),
        }
        with self._lock:
            self._jobs[job_id] = record
            while len(self._jobs) > self.max_jobs:
                self._jobs.popitem(last=False)
        return job_id

    def log(self, job_id, message, severity=None):
        message_text = str(message).rstrip()
        if len(message_text) > self.max_log_chars:
            message_text = (
                "…[earlier output truncated] "
                + message_text[-self.max_log_chars:]
            )
        entry = {
            "at": _utc_now(),
            "severity": severity or classify_severity(message_text),
            "message": message_text,
        }
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record["logs"].append(entry)

    def update(self, job_id, **result):
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record["result"].update(result)

    def finish(self, job_id, ok, message):
        message_text = str(message)
        if ok:
            state = "succeeded"
        elif "cancel" in message_text.lower():
            state = "cancelled"
        elif "timed out" in message_text.lower():
            state = "timed_out"
        else:
            state = "failed"
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record["state"] = state
                record["ended_at"] = _utc_now()
                record["result"]["message"] = message_text

    def event(self, severity, message, *, context=None):
        with self._lock:
            self._events.append(
                {
                    "at": _utc_now(),
                    "severity": severity,
                    "message": str(message).rstrip(),
                    "context": dict(context or {}),
                }
            )

    def snapshot(self, *, redact=True):
        with self._lock:
            jobs = []
            for record in self._jobs.values():
                copied = {
                    key: list(value) if key == "logs" else value
                    for key, value in record.items()
                }
                jobs.append(copied)
            payload = {
                "schema_version": 1,
                "generated_at": _utc_now(),
                "application": {"name": APP_NAME, "version": APP_VERSION},
                "runtime": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "frozen": bool(getattr(sys, "frozen", False)),
                },
                "privacy": {
                    "paths_redacted": redact,
                    "media_contents_included": False,
                },
                "jobs": jobs,
                "events": list(self._events),
            }
        return _redact_value(payload) if redact else payload

    def export(self, output_path, *, redact=True):
        final_path = Path(output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        staged = final_path.with_name(
            f".{final_path.stem}.clipforge-{uuid.uuid4().hex}{final_path.suffix}"
        )
        try:
            staged.write_text(
                json.dumps(self.snapshot(redact=redact), indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            os.replace(staged, final_path)
        except Exception:
            staged.unlink(missing_ok=True)
            raise


DIAGNOSTICS = DiagnosticsStore()
