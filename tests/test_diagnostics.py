import json
import sys

from clipforge.diagnostics import (
    DIAGNOSTICS,
    DiagnosticsStore,
    classify_severity,
    redact_text,
)
from clipforge.workers import FFmpegWorker
from clipforge.runtime_policy import evaluate_ffmpeg_runtime


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
        "url_credentials_redacted": True,
        "url_tokens_redacted": True,
        "secret_options_redacted": True,
        "private_media_metadata_included": False,
        "media_contents_included": False,
    }


def test_diagnostics_snapshot_includes_runtime_policy_and_qt_identity():
    store = DiagnosticsStore()
    banner = "ffmpeg version 8.1.2-full_build"
    store.record_runtime_policy(
        "ffmpeg",
        evaluate_ffmpeg_runtime(banner),
        identity={"banner": banner, "executable": "C:\\ffmpeg\\bin\\ffmpeg.exe"},
    )
    snapshot = store.snapshot(redact=False)
    runtime = snapshot["runtime"]
    assert runtime["policy"]["schema"] == "clipforge.runtime-policy"
    assert runtime["components"]["qt"]["component"] == "qt"
    assert "identity" in runtime["components"]["qt"]
    assert runtime["components"]["ffmpeg"]["identity"]["banner"] == banner
    assert runtime["components"]["ffmpeg"]["status"] == "supported"


def test_url_is_not_mistaken_for_local_path():
    url = "https://ffmpeg.org/download.html"
    assert redact_text(url) == url


def test_support_redaction_removes_url_secrets_options_and_private_metadata(tmp_path):
    store = DiagnosticsStore()
    job_id = store.start_job(
        "download",
        [
            "ffmpeg",
            "-headers",
            "Authorization: Bearer command-secret",
            "-i",
            "https://user:password@example.com/input.mp4?token=url-secret&keep=visible",
        ],
        context={
            "source_url": "https://example.com/source?api_key=context-secret",
            "private_metadata": {"title": "private-title"},
        },
    )
    store.log(job_id, "Fetched https://user:password@example.com/file?access_token=log-secret")
    output = tmp_path / "support.json"

    store.export(output)
    payload_text = output.read_text(encoding="utf-8")

    for secret in (
        "command-secret",
        "password",
        "url-secret",
        "context-secret",
        "private-title",
        "log-secret",
    ):
        assert secret not in payload_text
    assert "<redacted-secret>" in payload_text
    assert "visible" in payload_text


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
    assert jobs[-2]["context"]["runtime_identities"]["ffmpeg"]["sha256"]
    assert jobs[-2]["context"]["runtime_identities"]["ffprobe"]["status"] in {
        "missing",
        "unavailable",
        "available",
        "unusable",
    }
    assert jobs[-1]["state"] == "failed"
    assert jobs[-1]["result"]["exit_code"] == 3
    assert any("fatal detail" in entry["message"] for entry in jobs[-1]["logs"])
