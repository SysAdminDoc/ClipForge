import json

import pytest

from clipforge.job_queue import (
    JobQueue,
    JobRecord,
    QueueBusyError,
)


def _job(tmp_path, name, *, priority=0, overwrite=False):
    source = tmp_path / f"{name}.mp4"
    source.write_bytes(b"source")
    output = tmp_path / f"{name}-out.mp4"
    return JobRecord.create(
        source,
        output,
        "Convert",
        ["ffmpeg", "-i", str(source), str(output)],
        priority=priority,
        overwrite=overwrite,
        snapshot={"template": "{name}-out{ext}"},
    )


def test_queue_persists_snapshot_and_priority_order(tmp_path):
    queue_path = tmp_path / "job-queue.json"
    queue = JobQueue(queue_path)
    low = _job(tmp_path, "low")
    high = _job(tmp_path, "high", priority=5)

    queue.add([low, high])
    queue.activate()
    claimed = queue.claim_next()

    assert claimed is not None
    assert claimed.job_id == high.job_id
    assert claimed.state == "running"
    output = tmp_path / "high-out.mp4"
    output.write_bytes(b"valid output")
    completed = queue.complete(high.job_id, True, output_valid=True)
    assert completed.state == "succeeded"

    queue.deactivate()
    restored = JobQueue(queue_path)
    restored_high = next(job for job in restored.jobs if job.job_id == high.job_id)
    assert restored_high.state == "succeeded"
    assert restored_high.attempts == 1
    assert restored_high.snapshot["template"] == "{name}-out{ext}"
    assert json.loads(queue_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_queue_rejects_mutations_while_active_and_supports_pause_resume(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    first = _job(tmp_path, "first")
    second = _job(tmp_path, "second")
    queue.add([first, second])
    queue.activate()

    with pytest.raises(QueueBusyError):
        queue.move(second.job_id, -1)
    with pytest.raises(QueueBusyError):
        queue.set_priority(first.job_id, 4)

    queue.pause()
    assert queue.claim_next() is None
    assert queue.paused
    queue.resume()
    assert queue.claim_next().job_id == first.job_id


def test_changed_source_is_failed_before_execution_and_can_be_retried(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    job = _job(tmp_path, "source")
    queue.add([job])
    job_source = tmp_path / "source.mp4"
    job_source.write_bytes(b"changed")
    queue.activate()

    assert queue.claim_next() is None
    failed = queue.jobs[0]
    assert failed.state == "failed"
    assert "changed" in failed.error

    queue.deactivate()
    assert queue.retry_failed() == (job.job_id,)
    assert queue.jobs[0].state == "queued"


def test_cancel_pending_jobs_become_interrupted_and_retryable(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    jobs = [_job(tmp_path, "one"), _job(tmp_path, "two")]
    queue.add(jobs)
    queue.activate()
    claimed = queue.claim_next()
    assert claimed is not None
    queue.cancel(claimed.job_id)
    assert queue.cancel_pending() == 1
    assert {job.state for job in queue.jobs} == {"cancelling", "interrupted"}

    queue.complete(claimed.job_id, False, "Cancelled", cancelled=True)
    assert {job.state for job in queue.jobs} == {"interrupted"}
    queue.deactivate()
    assert set(queue.retry_failed()) == {job.job_id for job in jobs}


def test_running_jobs_are_recovered_as_interrupted_after_restart(tmp_path):
    queue_path = tmp_path / "queue.json"
    queue = JobQueue(queue_path)
    job = _job(tmp_path, "restart")
    queue.add([job])
    queue.activate()
    assert queue.claim_next().job_id == job.job_id

    restored = JobQueue(queue_path)
    recovered = restored.jobs[0]
    assert restored.recovered_job_ids == (job.job_id,)
    assert recovered.state == "interrupted"
    assert "exited" in recovered.error


def test_malformed_queue_is_quarantined(tmp_path):
    queue_path = tmp_path / "queue.json"
    queue_path.write_text("{not-json", encoding="utf-8")

    queue = JobQueue(queue_path)

    assert not queue.jobs
    assert queue.load_warning
    assert list(tmp_path.glob("queue.corrupt-*.json"))
