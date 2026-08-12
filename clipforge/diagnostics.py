"""Bounded, structured diagnostics and privacy-safe support export."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import threading
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import APP_NAME, APP_VERSION
from .provenance import (
    PROVENANCE_SCHEMA,
    PROVENANCE_SCHEMA_VERSION,
    REVIEWED_DATE,
    executable_identity,
)
from .runtime_policy import (
    evaluate_qt_runtime,
    policy_manifest,
    qt_runtime_identity,
)


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
_URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s\"'<>]+")
_SECRET_KEY = re.compile(
    r"(?i)(?:password|passwd|token|secret|api[_-]?key|authorization|credential|cookie|signature|session)"
)
_PRIVATE_KEY = re.compile(
    r"(?i)^(?:file(?:name|path)?|local_path|media(?:_name|_path)?|private_metadata|"
    r"title|artist|album|comment|location|tags)$"
)
_SECRET_OPTION = re.compile(
    r"(?i)(?:password|passwd|token|secret|api[_-]?key|authorization|credential|cookie|signature|headers?)"
)


def _redact_url_match(match):
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,;:)]}>":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parts = urlsplit(raw)
        netloc = parts.netloc
        if parts.username is not None or parts.password is not None:
            host = parts.hostname or "<redacted-host>"
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parts.port:
                host += f":{parts.port}"
            netloc = f"<redacted-secret>@{host}"
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, "<redacted-secret>" if _SECRET_KEY.search(key) else value))
        fragment = "<redacted-secret>" if parts.fragment else ""
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), fragment)) + trailing
    except (TypeError, ValueError):
        return "<redacted-url>" + trailing


def redact_text(value):
    text = str(value)
    text = _URL.sub(_redact_url_match, text)
    text = _WINDOWS_PATH.sub("<redacted-path>", text)
    return _POSIX_PATH.sub("<redacted-path>", text)


def _redact_command(value):
    redacted = []
    redact_next = False
    for part in value:
        text = str(part)
        if redact_next:
            redacted.append("<redacted-secret>")
            redact_next = False
            continue
        if text.startswith("-") and _SECRET_OPTION.search(text):
            if "=" in text:
                option, _secret = text.split("=", 1)
                redacted.append(f"{option}=<redacted-secret>")
            else:
                redacted.append(text)
                redact_next = True
            continue
        redacted.append(redact_text(text))
    return redacted


def _redact_value(value, *, key=None):
    key_text = str(key or "")
    if key_text.endswith("_redacted") or key_text.endswith("_included"):
        pass
    elif _SECRET_KEY.search(key_text):
        return "<redacted-secret>"
    elif _PRIVATE_KEY.search(key_text):
        return "<redacted-private-metadata>"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        if key_text == "command":
            return _redact_command(value)
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {item_key: _redact_value(item, key=item_key) for item_key, item in value.items()}
    return value


def _storage_snapshot():
    """Return bounded storage facts without exposing configuration paths."""
    try:
        from .ai_tools import AIFrameCache
        from .constants import CONFIG_DIR
        from .proxy import ProxyCache

        usage = shutil.disk_usage(CONFIG_DIR.parent)
        return {
            "disk": {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            },
            "caches": {
                "ai_frame": AIFrameCache().stats(),
                "preview_proxy": ProxyCache().stats(),
            },
        }
    except (OSError, ValueError, TypeError):
        return {"available": False}


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
        self._tool_identities = {}
        self._runtime_policy = {}
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._jobs.clear()
            self._events.clear()
            self._tool_identities.clear()
            self._runtime_policy.clear()

    def record_runtime_policy(self, component, decision, *, identity=None):
        """Store a bounded runtime-policy decision for support diagnostics."""
        payload = decision.as_dict()
        if identity:
            payload["identity"] = dict(identity)
        with self._lock:
            self._runtime_policy[str(component)] = payload

    def tool_version(self, name, executable):
        identity = self.tool_identity(name, executable)
        return identity.get("version") or identity.get("status", "unavailable")

    def tool_identity(self, name, executable, *, license=None):
        key = (name, str(executable or ""), license)
        with self._lock:
            if key in self._tool_identities:
                return dict(self._tool_identities[key])
        identity = executable_identity(executable, name=name, license=license)
        with self._lock:
            self._tool_identities[key] = dict(identity)
            self._tool_versions[(name, str(executable or ""))] = (
                identity.get("version") or identity.get("status", "unavailable")
            )
        return dict(identity)

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

    def finish(self, job_id, ok=None, message="", *, outcome=None):
        if outcome is not None:
            message_text = str(outcome.message)
            state = outcome.state
            outcome_payload = outcome.as_dict()
        else:
            message_text = str(message)
            if ok:
                state = "succeeded"
            elif "cancel" in message_text.lower():
                state = "cancelled"
            elif "timed out" in message_text.lower():
                state = "timed_out"
            else:
                state = "failed"
            outcome_payload = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record["state"] = state
                record["ended_at"] = _utc_now()
                record["result"]["message"] = message_text
                if outcome_payload is not None:
                    record["result"]["outcome"] = outcome_payload

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
        qt_identity = qt_runtime_identity()
        qt_policy = evaluate_qt_runtime(qt_identity.get("qt"))
        python_identity = executable_identity(sys.executable, name="python")
        with self._lock:
            jobs = []
            for record in self._jobs.values():
                copied = {
                    key: list(value) if key == "logs" else value
                    for key, value in record.items()
                }
                jobs.append(copied)
            runtime_policy = dict(self._runtime_policy)
            runtime_policy.setdefault(
                "qt",
                {
                    **qt_policy.as_dict(),
                    "identity": qt_identity,
                },
            )
            payload = {
                "schema_version": 2,
                "generated_at": _utc_now(),
                "application": {"name": APP_NAME, "version": APP_VERSION},
                "runtime": {
                    "python": platform.python_version(),
                    "python_identity": python_identity,
                    "platform": platform.platform(),
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "policy": policy_manifest(),
                    "provenance": {
                        "schema": PROVENANCE_SCHEMA,
                        "schema_version": PROVENANCE_SCHEMA_VERSION,
                        "reviewed_date": REVIEWED_DATE,
                        "job_identities_included": True,
                    },
                    "components": runtime_policy,
                },
                "privacy": {
                    "paths_redacted": redact,
                    "url_credentials_redacted": redact,
                    "url_tokens_redacted": redact,
                    "secret_options_redacted": redact,
                    "private_media_metadata_included": False,
                    "media_contents_included": False,
                },
                "storage": _storage_snapshot(),
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
