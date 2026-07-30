import json
import sys

from clipforge.diagnostics import (
    DIAGNOSTICS,
    DiagnosticsStore,
    classify_severity,
    redact_text,
)
from clipforge.workers import FFmpegWorker


def test_severity_classification_is_exclusive():
    assert classify_severity("ordinary progress") == "info"
    assert classify_severity("Warning: slow input") == "warning"
    assert classify_severity("[ERROR] encode failed") == "error"


def test_diagnostics_are_bounded_and_failed_jobs_are_retained():
    store = DiagnosticsStore(max_jobs=2, max_job_logs=2, max_events=2)
    first = store.start_job("encode", ["tool", "first"])
    second = store.start_job("encode", ["tool", "second"])
    third = store.start_job("encode", ["tool", "third"])
    for index in range(3):
        store.log(third, f"log {index}")
        store.event("info", f"event {index}")
    store.finish(third, False, "Encode failed")

    snapshot = store.snapshot(redact=False)
    assert [job["id"] for job in snapshot["jobs"]] == [second, third]
    assert first not in [job["id"] for job in snapshot["jobs"]]
    assert [entry["message"] for entry in snapshot["jobs"][-1]["logs"]] == [
        "log 1",
        "log 2",
    ]
    assert snapshot["jobs"][-1]["state"] == "failed"
    assert len(snapshot["events"]) == 2


def test_diagnostics_bound_individual_log_messages():
    store = DiagnosticsStore(max_log_chars=64)
    job_id = store.start_job("encode", ["tool"])
    store.log(job_id, "prefix-" + "x" * 200 + "-tail")
    message = store.snapshot(redact=False)["jobs"][0]["logs"][0]["message"]
    assert message.startswith("…[earlier output truncated] ")
    assert message.endswith("-tail")
    assert len(message) <= 64 + len("…[earlier output truncated] ")


def test_support_export_redacts_paths_and_never_includes_media(tmp_path):
    store = DiagnosticsStore()
    secret = r"C:\Users\Alice\Videos\private source.mp4"
    job_id = store.start_job("encode", ["ffmpeg", "-i", secret])
    store.log(job_id, f"Reading {secret}")
    store.finish(job_id, False, f"Failed to read {secret}")
    output = tmp_path / "support.json"

    store.export(output)
    payload_text = output.read_text(encoding="utf-8")
    payload = json.loads(payload_text)

    assert secret not in payload_text
    assert "<redacted-path>" in payload_text
    assert payload["privacy"] == {
        "paths_redacted": True,
        "media_contents_included": False,
    }


def test_url_is_not_mistaken_for_local_path():
    url = "https://ffmpeg.org/download.html"
    assert redact_text(url) == url


def test_worker_records_success_and_failure_without_losing_failure_logs():
    DIAGNOSTICS.reset()
    success = FFmpegWorker(
        [sys.executable, "-c", "print('worker succeeded')"],
        parse_progress=False,
    )
    failure = FFmpegWorker(
        [
            sys.executable,
            "-c",
            "import sys; print('fatal detail', file=sys.stderr); raise SystemExit(3)",
        ],
        parse_progress=False,
    )

    success.run()
    failure.run()
    jobs = DIAGNOSTICS.snapshot(redact=False)["jobs"]

    assert jobs[-2]["state"] == "succeeded"
    assert jobs[-2]["result"]["exit_code"] == 0
    assert jobs[-1]["state"] == "failed"
    assert jobs[-1]["result"]["exit_code"] == 3
    assert any("fatal detail" in entry["message"] for entry in jobs[-1]["logs"])
