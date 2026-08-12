"""Durable, single-worker media job queue state and persistence."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .constants import CONFIG_DIR
from .processes import default_output_contract, validate_output
from .tools import FFPROBE


QUEUE_SCHEMA_VERSION = 1
MAX_QUEUE_BYTES = 2 * 1024 * 1024
MAX_JOBS = 500
MAX_COMMAND_ARGS = 256
MAX_ARGUMENT_LENGTH = 32767
QUEUE_FILE = CONFIG_DIR / "job-queue.json"
QUEUE_BACKUP_SUFFIX = ".bak"

JOB_STATES = frozenset(
    {"queued", "paused", "running", "cancelling", "succeeded", "failed", "interrupted"}
)
TERMINAL_STATES = frozenset({"succeeded", "failed", "interrupted"})
RETRYABLE_STATES = frozenset({"failed", "interrupted"})
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class QueueError(RuntimeError):
    """Base class for queue state and persistence failures."""


class QueueBusyError(QueueError):
    """Raised when a queue mutation would race with an active worker."""


class QueuePersistenceError(QueueError):
    """Raised when a queue mutation could not be committed atomically."""


class QueueValidationError(QueueError):
    """Raised when durable queue data or a job snapshot is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_path(value: str | os.PathLike[str]) -> str:
    path = Path(value)
    if not str(path):
        raise QueueValidationError("job paths must be nonempty")
    return os.path.abspath(os.fspath(path))


def _validate_text(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or "\0" in value:
        raise QueueValidationError(f"{field_name} must be a bounded nonempty string")
    return value


def _validate_snapshot(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise QueueValidationError("job snapshot must be a small object")
    validated: dict[str, Any] = {}
    for key, item in value.items():
        key = _validate_text(key, "snapshot key", max_length=100)
        if item is None or isinstance(item, (bool, int, str)):
            validated[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            validated[key] = item
        else:
            raise QueueValidationError("job snapshot values must be scalar JSON values")
    return validated


@dataclass(frozen=True)
class JobRecord:
    """An immutable execution snapshot persisted in the queue journal."""

    job_id: str
    source_path: str
    output_path: str
    operation: str
    command: tuple[str, ...]
    duration: float = 0.0
    overwrite: bool = False
    priority: int = 0
    state: str = "queued"
    attempts: int = 0
    progress: float = 0.0
    error: str = ""
    source_size: int | None = None
    source_mtime_ns: int | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        source_path: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        operation: str,
        command: Iterable[str],
        *,
        duration: float = 0.0,
        overwrite: bool = False,
        priority: int = 0,
        snapshot: dict[str, Any] | None = None,
    ) -> "JobRecord":
        source = _normalise_path(source_path)
        output = _normalise_path(output_path)
        try:
            source_stat = Path(source).stat()
        except OSError:
            source_stat = None
        now = _now()
        return cls(
            job_id=uuid.uuid4().hex,
            source_path=source,
            output_path=output,
            operation=_validate_text(operation, "operation", max_length=200),
            command=tuple(str(part) for part in command),
            duration=float(duration or 0),
            overwrite=bool(overwrite),
            priority=int(priority),
            source_size=source_stat.st_size if source_stat else None,
            source_mtime_ns=source_stat.st_mtime_ns if source_stat else None,
            snapshot=dict(snapshot or {}),
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "operation": self.operation,
            "command": list(self.command),
            "duration": self.duration,
            "overwrite": self.overwrite,
            "priority": self.priority,
            "state": self.state,
            "attempts": self.attempts,
            "progress": self.progress,
            "error": self.error,
            "source_size": self.source_size,
            "source_mtime_ns": self.source_mtime_ns,
            "snapshot": dict(self.snapshot),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "JobRecord":
        if not isinstance(value, dict):
            raise QueueValidationError("each queued job must be an object")
        command = value.get("command")
        if not isinstance(command, list):
            raise QueueValidationError("job command must be an argument list")
        record = cls(
            job_id=value.get("job_id", ""),
            source_path=value.get("source_path", ""),
            output_path=value.get("output_path", ""),
            operation=value.get("operation", ""),
            command=tuple(command),
            duration=value.get("duration", 0),
            overwrite=value.get("overwrite", False),
            priority=value.get("priority", 0),
            state=value.get("state", "queued"),
            attempts=value.get("attempts", 0),
            progress=value.get("progress", 0),
            error=value.get("error", ""),
            source_size=value.get("source_size"),
            source_mtime_ns=value.get("source_mtime_ns"),
            snapshot=value.get("snapshot", {}),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
        )
        _validate_record(record)
        return record


def _validate_record(record: JobRecord) -> None:
    if not isinstance(record.job_id, str) or not _JOB_ID_RE.fullmatch(record.job_id):
        raise QueueValidationError("job id is invalid")
    _validate_text(record.source_path, "source path", max_length=32767)
    _validate_text(record.output_path, "output path", max_length=32767)
    _validate_text(record.operation, "operation", max_length=200)
    if not 1 <= len(record.command) <= MAX_COMMAND_ARGS:
        raise QueueValidationError("job command has an invalid argument count")
    for argument in record.command:
        _validate_text(argument, "command argument", max_length=MAX_ARGUMENT_LENGTH)
    if (
        isinstance(record.duration, bool)
        or not isinstance(record.duration, (int, float))
        or not math.isfinite(float(record.duration))
        or not 0 <= float(record.duration) <= 7 * 24 * 60 * 60
    ):
        raise QueueValidationError("job duration is outside the supported range")
    if not isinstance(record.overwrite, bool):
        raise QueueValidationError("job overwrite flag is invalid")
    if isinstance(record.priority, bool) or not isinstance(record.priority, int) or not -10 <= record.priority <= 10:
        raise QueueValidationError("job priority is outside the supported range")
    if not isinstance(record.state, str) or record.state not in JOB_STATES:
        raise QueueValidationError(f"unknown job state: {record.state!r}")
    if isinstance(record.attempts, bool) or not isinstance(record.attempts, int) or not 0 <= record.attempts <= 100:
        raise QueueValidationError("job attempts are outside the supported range")
    if (
        isinstance(record.progress, bool)
        or not isinstance(record.progress, (int, float))
        or not math.isfinite(float(record.progress))
        or not 0 <= float(record.progress) <= 100
    ):
        raise QueueValidationError("job progress is outside the supported range")
    if not isinstance(record.error, str) or len(record.error) > 2000 or "\0" in record.error:
        raise QueueValidationError("job error is too long")
    for field_name in ("source_size", "source_mtime_ns"):
        value = getattr(record, field_name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise QueueValidationError(f"job {field_name} is invalid")
    _validate_snapshot(record.snapshot)
    _validate_text(record.created_at, "created timestamp", max_length=100)
    _validate_text(record.updated_at, "updated timestamp", max_length=100)


def _job_error(record: JobRecord) -> str | None:
    source = Path(record.source_path)
    try:
        source_stat = source.stat()
    except OSError:
        return "Source file is no longer available"
    if not source.is_file():
        return "Source path is not a file"
    if record.source_size is not None and source_stat.st_size != record.source_size:
        return "Source file changed after it was queued"
    if (
        record.source_mtime_ns is not None
        and source_stat.st_mtime_ns != record.source_mtime_ns
    ):
        return "Source file changed after it was queued"
    if not record.overwrite and Path(record.output_path).exists():
        return "Output already exists"
    return None


class JobQueue:
    """A durable queue whose state changes are committed as one atomic snapshot."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else QUEUE_FILE
        self._lock = threading.RLock()
        self._jobs: list[JobRecord] = []
        self._paused = False
        self._active = False
        self._revision = 0
        self._last_progress_write: dict[str, tuple[float, float]] = {}
        self.recovered_job_ids: tuple[str, ...] = ()
        self.load_warning: str | None = None
        self._load()

    @property
    def jobs(self) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(self._jobs)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def has_pending(self) -> bool:
        return any(job.state == "queued" for job in self.jobs)

    def _load(self) -> None:
        with self._lock:
            if not self.path.is_file():
                backup = Path(str(self.path) + QUEUE_BACKUP_SUFFIX)
                if backup.is_file():
                    try:
                        payload = self._read_payload(backup)
                    except (OSError, ValueError, QueueError, json.JSONDecodeError) as exc:
                        self.load_warning = f"Could not read queue backup: {exc}"
                    else:
                        self._apply_payload(payload)
                        self.load_warning = "Recovered the durable queue from its backup."
                return
            try:
                payload = self._read_payload(self.path)
                self._apply_payload(payload)
            except (OSError, ValueError, QueueError, json.JSONDecodeError) as exc:
                backup = Path(str(self.path) + QUEUE_BACKUP_SUFFIX)
                try:
                    if backup.is_file():
                        self._apply_payload(self._read_payload(backup))
                        self.load_warning = (
                            f"Recovered the queue from its backup after the main journal failed: {exc}"
                        )
                        return
                    quarantined = self.path.with_name(
                        f"{self.path.stem}.corrupt-{uuid.uuid4().hex[:8]}{self.path.suffix}"
                    )
                    os.replace(self.path, quarantined)
                    self.load_warning = f"Malformed queue was quarantined as {quarantined.name}: {exc}"
                except (OSError, ValueError, QueueError, json.JSONDecodeError) as recovery_error:
                    self.load_warning = f"Could not recover the durable queue: {recovery_error}"
                self._jobs = []
                self._paused = False

    @staticmethod
    def _read_payload(path: Path) -> dict[str, Any]:
        if path.stat().st_size > MAX_QUEUE_BYTES:
            raise QueueValidationError(f"{path.name} exceeds the queue size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise QueueValidationError("queue journal must be an object")
        return payload

    def _apply_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != QUEUE_SCHEMA_VERSION:
            raise QueueValidationError("unsupported queue schema")
        jobs_data = payload.get("jobs")
        if not isinstance(jobs_data, list) or len(jobs_data) > MAX_JOBS:
            raise QueueValidationError("queue journal contains too many jobs")
        jobs = [JobRecord.from_dict(item) for item in jobs_data]
        ids = [job.job_id for job in jobs]
        if len(ids) != len(set(ids)):
            raise QueueValidationError("queue journal contains duplicate job ids")
        recovered: list[str] = []
        now = _now()
        updated_jobs = []
        for job in jobs:
            if job.state in {"running", "cancelling", "paused"}:
                updated = replace(
                    job,
                    state="interrupted",
                    progress=0.0,
                    error="Application exited before this job completed",
                    updated_at=now,
                )
                updated_jobs.append(updated)
                if job.state in {"running", "cancelling"}:
                    recovered.append(job.job_id)
            else:
                updated_jobs.append(job)
        self._jobs = updated_jobs
        self._paused = bool(payload.get("paused", False))
        self._revision = int(payload.get("revision", 0) or 0)
        self.recovered_job_ids = tuple(recovered)
        if recovered:
            self._paused = False
            try:
                self._persist_locked()
            except QueuePersistenceError as exc:
                self.load_warning = f"Recovered interrupted jobs but could not persist them: {exc}"

    def _payload_locked(self) -> dict[str, Any]:
        return {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "revision": self._revision + 1,
            "paused": self._paused,
            "jobs": [job.to_dict() for job in self._jobs],
        }

    def _persist_locked(self) -> None:
        payload = self._payload_locked()
        encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if len(encoded.encode("utf-8")) > MAX_QUEUE_BYTES:
            raise QueuePersistenceError("queue journal exceeds its size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        staged = Path(staged_name)
        backup_staged: Path | None = None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.is_file():
                backup = Path(str(self.path) + QUEUE_BACKUP_SUFFIX)
                backup_descriptor, backup_name = tempfile.mkstemp(
                    prefix=f".{backup.name}.", dir=backup.parent
                )
                backup_staged = Path(backup_name)
                os.close(backup_descriptor)
                shutil.copy2(self.path, backup_staged)
                with backup_staged.open("r+b") as handle:
                    os.fsync(handle.fileno())
                os.replace(backup_staged, backup)
                backup_staged = None
            os.replace(staged, self.path)
            self._revision += 1
        except (OSError, ValueError) as exc:
            raise QueuePersistenceError(f"could not persist queue: {exc}") from exc
        finally:
            for temporary in (staged, backup_staged):
                try:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _commit(self, jobs: list[JobRecord], *, paused: bool | None = None) -> None:
        old_jobs = self._jobs
        old_paused = self._paused
        self._jobs = jobs
        if paused is not None:
            self._paused = paused
        try:
            self._persist_locked()
        except QueuePersistenceError:
            self._jobs = old_jobs
            self._paused = old_paused
            raise

    def _require_inactive(self) -> None:
        if self._active:
            raise QueueBusyError("queue mutations are disabled while jobs are running")

    def _find_index(self, job_id: str) -> int:
        for index, job in enumerate(self._jobs):
            if job.job_id == job_id:
                return index
        raise QueueValidationError(f"unknown job id: {job_id}")

    def add(self, jobs: Iterable[JobRecord]) -> tuple[JobRecord, ...]:
        with self._lock:
            self._require_inactive()
            incoming = list(jobs)
            if not incoming or len(self._jobs) + len(incoming) > MAX_JOBS:
                raise QueueValidationError(f"queue must contain 1 to {MAX_JOBS} jobs")
            existing = {job.job_id for job in self._jobs}
            for job in incoming:
                _validate_record(job)
                if job.job_id in existing:
                    raise QueueValidationError(f"duplicate job id: {job.job_id}")
                existing.add(job.job_id)
            self._commit(self._jobs + incoming)
            return tuple(incoming)

    def clear(self) -> None:
        with self._lock:
            self._require_inactive()
            if self._jobs or self._paused:
                self._commit([], paused=False)

    def remove(self, job_ids: Iterable[str]) -> None:
        with self._lock:
            self._require_inactive()
            remove_ids = set(job_ids)
            if not remove_ids:
                return
            self._commit(
                [job for job in self._jobs if job.job_id not in remove_ids]
            )

    def move(self, job_id: str, delta: int) -> JobRecord:
        with self._lock:
            self._require_inactive()
            index = self._find_index(job_id)
            target = max(0, min(len(self._jobs) - 1, index + int(delta)))
            jobs = list(self._jobs)
            job = jobs.pop(index)
            jobs.insert(target, job)
            self._commit(jobs)
            return job

    def set_priority(self, job_id: str, priority: int) -> JobRecord:
        with self._lock:
            self._require_inactive()
            if isinstance(priority, bool) or not isinstance(priority, int) or not -10 <= priority <= 10:
                raise QueueValidationError("job priority is outside the supported range")
            index = self._find_index(job_id)
            job = replace(self._jobs[index], priority=priority, updated_at=_now())
            jobs = list(self._jobs)
            jobs[index] = job
            self._commit(jobs)
            return job

    def activate(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
            self._paused = False

    def deactivate(self) -> None:
        with self._lock:
            if not self._active and not self._paused:
                return
            self._active = False
            self._commit(list(self._jobs), paused=False)

    def pause(self) -> None:
        with self._lock:
            if not self._active:
                raise QueueBusyError("queue is not active")
            if not self._paused:
                self._commit(list(self._jobs), paused=True)

    def resume(self) -> None:
        with self._lock:
            if self._paused:
                self._commit(list(self._jobs), paused=False)

    def claim_next(self) -> JobRecord | None:
        """Claim the highest-priority queued job, skipping invalid snapshots."""
        with self._lock:
            if not self._active or self._paused:
                return None
            jobs = list(self._jobs)
            candidates = sorted(
                (
                    (index, job)
                    for index, job in enumerate(jobs)
                    if job.state == "queued"
                ),
                key=lambda item: (-item[1].priority, item[0]),
            )
            changed = False
            now = _now()
            for index, job in candidates:
                error = _job_error(job)
                if error:
                    jobs[index] = replace(
                        job, state="failed", error=error, updated_at=now
                    )
                    changed = True
                    continue
                claimed = replace(
                    job,
                    state="running",
                    attempts=job.attempts + 1,
                    progress=0.0,
                    error="",
                    updated_at=now,
                )
                jobs[index] = claimed
                self._commit(jobs)
                return claimed
            if changed:
                self._commit(jobs)
            return None

    def update_progress(self, job_id: str, progress: float, *, force: bool = False) -> None:
        with self._lock:
            index = self._find_index(job_id)
            job = self._jobs[index]
            if job.state not in {"running", "cancelling"}:
                return
            value = max(0.0, min(100.0, float(progress)))
            updated = replace(job, progress=value, updated_at=_now())
            now = _monotonic()
            previous_time, previous_value = self._last_progress_write.get(
                job_id, (0.0, -1.0)
            )
            if force or value in {0.0, 100.0} or value - previous_value >= 2 or now - previous_time >= 1:
                jobs = list(self._jobs)
                jobs[index] = updated
                self._commit(jobs)
                self._last_progress_write[job_id] = (now, value)

    def cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            index = self._find_index(job_id)
            job = self._jobs[index]
            if job.state == "running":
                updated = replace(job, state="cancelling", updated_at=_now())
            elif job.state in {"queued", "paused"}:
                updated = replace(
                    job,
                    state="interrupted",
                    progress=0.0,
                    error="Cancelled before the job started",
                    updated_at=_now(),
                )
            else:
                return job
            jobs = list(self._jobs)
            jobs[index] = updated
            self._commit(jobs)
            return updated

    def cancel_pending(self) -> int:
        with self._lock:
            now = _now()
            jobs = list(self._jobs)
            changed = 0
            for index, job in enumerate(jobs):
                if job.state in {"queued", "paused"}:
                    jobs[index] = replace(
                        job,
                        state="interrupted",
                        progress=0.0,
                        error="Cancelled before the job started",
                        updated_at=now,
                    )
                    changed += 1
            if changed:
                self._commit(jobs)
            return changed

    def complete(
        self,
        job_id: str,
        success: bool,
        message: str = "",
        *,
        cancelled: bool = False,
        output_valid: bool | None = None,
    ) -> JobRecord:
        with self._lock:
            index = self._find_index(job_id)
            job = self._jobs[index]
            if job.state not in {"running", "cancelling"}:
                return job
            if cancelled or job.state == "cancelling":
                updated = replace(
                    job,
                    state="interrupted",
                    progress=0.0,
                    error=message or "Cancelled",
                    updated_at=_now(),
                )
            elif success:
                valid, reason = self._validate_output(job, output_valid)
                if not valid:
                    updated = replace(
                        job,
                        state="failed",
                        progress=0.0,
                        error=reason,
                        updated_at=_now(),
                    )
                else:
                    updated = replace(
                        job,
                        state="succeeded",
                        progress=100.0,
                        error="",
                        updated_at=_now(),
                    )
            else:
                updated = replace(
                    job,
                    state="failed",
                    progress=0.0,
                    error=(message or "Job failed")[:2000],
                    updated_at=_now(),
                )
            jobs = list(self._jobs)
            jobs[index] = updated
            self._commit(jobs)
            return updated

    @staticmethod
    def _validate_output(job: JobRecord, output_valid: bool | None) -> tuple[bool, str]:
        if output_valid is False:
            return False, "Output failed semantic validation"
        if output_valid is True:
            return True, ""
        path = Path(job.output_path)
        if FFPROBE:
            return validate_output(
                path,
                ffprobe_path=FFPROBE,
                contract=default_output_contract(path, expected_duration=job.duration),
            )
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True, ""
        except OSError as exc:
            return False, f"Could not inspect output: {exc}"
        return False, "Output file was not created or is empty"

    def retry_failed(self, *, include_interrupted: bool = True) -> tuple[str, ...]:
        with self._lock:
            self._require_inactive()
            retryable = RETRYABLE_STATES if include_interrupted else {"failed"}
            now = _now()
            jobs = list(self._jobs)
            retried = []
            for index, job in enumerate(jobs):
                if job.state in retryable:
                    jobs[index] = replace(
                        job, state="queued", progress=0.0, error="", updated_at=now
                    )
                    retried.append(job.job_id)
            if retried:
                self._commit(jobs)
            return tuple(retried)

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {state: 0 for state in JOB_STATES}
            for job in self._jobs:
                counts[job.state] += 1
            return counts


def _monotonic() -> float:
    # Kept as a tiny seam for deterministic progress persistence tests.
    import time

    return time.monotonic()
