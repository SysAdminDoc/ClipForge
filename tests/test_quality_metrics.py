import csv
import json

from clipforge.panels.streams import StreamsPanel
from clipforge.processes import ProcessOutcome
from clipforge.workers import QualityMetricsWorker


def _info(width=1280, height=720, duration=12.0):
    return {"width": width, "height": height, "duration": duration}


def test_quality_filter_applies_offset_scale_and_shorter_duration():
    worker = QualityMetricsWorker(
        "reference.mp4",
        "encoded.mp4",
        _info(duration=10),
        _info(width=640, height=360, duration=8),
        sync_offset=1.25,
    )
    graph = worker._filter_for("psnr")
    assert "[0:v]trim=start=1.250000:duration=6.750000" in graph
    assert "scale=1280:720:flags=bicubic" in graph
    assert "[1:v]trim=duration=6.750000" in graph
    assert graph.endswith("[dist][ref]psnr")


def test_quality_worker_reports_complete_metadata(monkeypatch):
    outputs = iter(
        [
            ProcessOutcome(0, "ffmpeg version 8.0-test\n", ""),
            ProcessOutcome(0, "", "VMAF score: 93.5"),
            ProcessOutcome(0, "", "average:40.25"),
            ProcessOutcome(0, "", "All:0.998125"),
        ]
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return next(outputs)

    monkeypatch.setattr("clipforge.workers.run_managed_process", fake_run)
    worker = QualityMetricsWorker(
        "reference.mp4",
        "encoded.mp4",
        _info(),
        _info(),
        metric_timeout=5,
    )
    emitted = []
    worker.finished_signal.connect(
        lambda ok, message, report: emitted.append((ok, message, report))
    )
    worker.run()

    ok, message, report = emitted[0]
    assert ok is True
    assert message == "Quality comparison complete"
    assert report["status"] == "complete"
    assert report["schema_version"] == 1
    assert report["ffmpeg_version"] == "ffmpeg version 8.0-test"
    assert report["metrics"]["vmaf"]["value"] == 93.5
    assert report["metrics"]["psnr"]["value"] == 40.25
    assert report["metrics"]["ssim"]["value"] == 0.998125
    assert report["metrics"]["vmaf"]["command"] == commands[1]


def test_quality_worker_distinguishes_unavailable_and_partial(monkeypatch):
    outputs = iter(
        [
            ProcessOutcome(0, "ffmpeg version test\n", ""),
            ProcessOutcome(1, "", "No such filter: libvmaf"),
            ProcessOutcome(0, "", "average:38.0"),
            ProcessOutcome(1, "", "filter graph failed"),
        ]
    )
    monkeypatch.setattr(
        "clipforge.workers.run_managed_process",
        lambda _command, **_kwargs: next(outputs),
    )
    worker = QualityMetricsWorker(
        "reference.mp4", "encoded.mp4", _info(), _info(), metric_timeout=5
    )
    emitted = []
    worker.finished_signal.connect(
        lambda ok, message, report: emitted.append((ok, message, report))
    )
    worker.run()

    ok, message, report = emitted[0]
    assert ok is True
    assert "partial" in message
    assert report["status"] == "partial"
    assert report["metrics"]["vmaf"]["status"] == "unavailable"
    assert report["metrics"]["psnr"]["status"] == "succeeded"
    assert report["metrics"]["ssim"]["status"] == "failed"


def test_quality_worker_stops_after_cancellation(monkeypatch):
    calls = 0

    def fake_run(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ProcessOutcome(0, "ffmpeg version test\n", "")
        return ProcessOutcome(-1, "", "", cancelled=True)

    monkeypatch.setattr("clipforge.workers.run_managed_process", fake_run)
    worker = QualityMetricsWorker(
        "reference.mp4", "encoded.mp4", _info(), _info(), metric_timeout=5
    )
    emitted = []
    worker.finished_signal.connect(
        lambda ok, message, report: emitted.append((ok, message, report))
    )
    worker.run()

    ok, _, report = emitted[0]
    assert ok is False
    assert report["status"] == "cancelled"
    assert report["metrics"]["vmaf"]["status"] == "cancelled"
    assert report["metrics"]["psnr"]["status"] == "cancelled"
    assert calls == 2


def test_quality_report_exports_json_and_csv(tmp_path):
    report = {
        "schema_version": 1,
        "generated_at": "2026-07-25T00:00:00+00:00",
        "status": "partial",
        "reference": "reference.mp4",
        "encoded": "encoded.mp4",
        "sync_offset_seconds": 0.0,
        "comparison_duration_seconds": 4.0,
        "ffmpeg_version": "ffmpeg version test",
        "metrics": {
            "vmaf": {"status": "unavailable", "message": "No libvmaf"},
            "psnr": {"status": "succeeded", "value": 42.0},
        },
    }
    json_path = tmp_path / "quality.json"
    csv_path = tmp_path / "quality.csv"

    StreamsPanel._write_quality_report(json_path, report)
    StreamsPanel._write_quality_report(csv_path, report)

    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert ["vmaf", "", "unavailable", "No libvmaf"] in rows
    assert ["psnr", "42.0", "succeeded", ""] in rows
