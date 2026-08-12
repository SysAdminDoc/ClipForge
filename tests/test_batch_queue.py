import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QTextEdit
from PyQt6.QtTest import QTest

from clipforge.job_queue import JobQueue
from clipforge.panels import batch as batch_module
from clipforge.panels.batch import BatchPanel
from clipforge.tools import ProbeError, ProbeResult
from clipforge.workers import BatchProbeWorker


_QT_APP = QApplication.instance() or QApplication([])


def _panel(monkeypatch, tmp_path):
    queue_path = tmp_path / "job-queue.json"
    monkeypatch.setattr(
        batch_module,
        "JobQueue",
        lambda: JobQueue(queue_path),
    )
    panel = BatchPanel(QTextEdit())
    return panel, queue_path


def _wait_for_preflight(panel):
    deadline = time.monotonic() + 3
    while panel._preflight_worker is not None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert panel._preflight_worker is None


def test_batch_panel_persists_snapshot_and_restores_queue(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    panel, queue_path = _panel(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_module, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(
        batch_module,
        "probe_media",
        lambda _path, **_kwargs: ProbeResult(
            info={"duration": 1.0, "width": 320, "height": 180, "streams": []}
        ),
    )
    monkeypatch.setattr(batch_module, "_confirm_overwrite", lambda *args: True)
    monkeypatch.setattr(BatchPanel, "_start_queue", lambda _self: None)

    panel.txt_name_template.setText("{index}-{name}{suffix}{ext}")
    panel.add_paths([source])
    panel._start_batch()
    _wait_for_preflight(panel)

    assert queue_path.exists()
    jobs = panel._queue.jobs
    assert len(jobs) == 1
    assert jobs[0].snapshot["template"] == "{index}-{name}{suffix}{ext}"
    assert jobs[0].state == "queued"
    assert panel._row_job_ids == [jobs[0].job_id]

    restored, _ = _panel(monkeypatch, tmp_path)
    assert restored._items == [str(source)]
    assert restored._row_job_ids == [jobs[0].job_id]
    assert restored.file_list.count() == 1
    assert "source" in restored.file_list.item(0).text()

    panel.deleteLater()
    restored.deleteLater()


def test_batch_panel_reorders_and_prioritizes_saved_jobs(monkeypatch, tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    panel, _ = _panel(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_module, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(
        batch_module,
        "probe_media",
        lambda _path, **_kwargs: ProbeResult(
            info={"duration": 1.0, "width": 320, "height": 180, "streams": []}
        ),
    )
    monkeypatch.setattr(batch_module, "_confirm_overwrite", lambda *args: True)
    monkeypatch.setattr(BatchPanel, "_start_queue", lambda _self: None)

    panel.add_paths([first, second])
    panel._start_batch()
    _wait_for_preflight(panel)
    jobs = panel._queue.jobs
    panel.file_list.setCurrentRow(1)
    panel._move_selected(-1)
    panel._set_selected_priority(7)

    assert panel._items == [str(second), str(first)]
    assert panel._queue.jobs[0].job_id == jobs[1].job_id
    assert panel._queue.jobs[0].priority == 7
    assert panel.file_list.item(0).text().startswith("○")
    assert "priority 7" in panel.file_list.item(0).text()
    panel.deleteLater()


def test_batch_panel_starts_up_to_the_configured_concurrency_cap(monkeypatch, tmp_path):
    class FakeWorker(QObject):
        progress = pyqtSignal(float)
        log_output = pyqtSignal(str)
        outcome_signal = pyqtSignal(object)
        finished_signal = pyqtSignal(bool, str)
        finished = pyqtSignal()

        def __init__(self, *args, **kwargs):
            super().__init__()

        def start(self):
            return None

        def cancel(self):
            return None

    panel, _ = _panel(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_module, "HW_ENCODER_CAPABILITIES", {})
    panel.spn_concurrency.setValue(min(2, panel.spn_concurrency.maximum()))
    sources = []
    jobs = []
    for index in range(3):
        source = tmp_path / f"source-{index}.mp4"
        source.write_bytes(b"source")
        output = tmp_path / f"output-{index}.mp4"
        sources.append(str(source))
        jobs.append(
            batch_module.JobRecord.create(
                source,
                output,
                "Convert",
                ["ffmpeg", "-i", str(source), str(output)],
                overwrite=True,
            )
        )
    panel._queue.add(jobs)
    panel._items = sources
    panel._row_job_ids = [job.job_id for job in jobs]
    panel._row_priorities = [0, 0, 0]
    panel.file_list.addItems([Path(source).name for source in sources])
    monkeypatch.setattr(batch_module, "FFmpegWorker", FakeWorker)

    panel._start_queue()

    assert len(panel._workers) == panel._effective_worker_cap()
    assert len(panel._workers) == 2
    assert sum(job.state == "running" for job in panel._queue.jobs) == 2

    panel._queue.deactivate()
    panel._workers.clear()
    panel._worker = None
    panel.deleteLater()


def test_batch_panel_warns_before_dropping_subtitle_or_data_streams(
    monkeypatch, tmp_path
):
    panel, _ = _panel(monkeypatch, tmp_path)
    notices = []
    panel.requestToast.connect(lambda message, _color: notices.append(message))

    warned = panel._report_stream_policy(
        {
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "subtitle"},
                {"codec_type": "data"},
            ]
        },
        "Convert to MP4 (H.264)",
        tmp_path / "source.mkv",
    )

    assert warned
    assert "subtitle" in panel.console.toPlainText()
    assert notices and "drop" in notices[0]
    panel.deleteLater()


def test_batch_preflight_cancels_without_blocking_the_gui(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    def delayed_probe(_path, *, cancel_event, **_kwargs):
        while not cancel_event.is_set():
            time.sleep(0.01)
        return ProbeResult(error=ProbeError("probe_cancelled", "cancelled"))

    outcomes = []
    worker = BatchProbeWorker([source], probe_function=delayed_probe)
    worker.outcome_signal.connect(outcomes.append)
    worker.start()
    QTest.qWait(50)
    started = time.monotonic()
    worker.cancel()
    assert worker.wait(3000)
    assert time.monotonic() - started < 2
    QTest.qWait(50)
    assert outcomes and outcomes[-1].cancelled
