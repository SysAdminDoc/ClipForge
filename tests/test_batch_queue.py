import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTextEdit

from clipforge.job_queue import JobQueue
from clipforge.panels import batch as batch_module
from clipforge.panels.batch import BatchPanel


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


def test_batch_panel_persists_snapshot_and_restores_queue(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    panel, queue_path = _panel(monkeypatch, tmp_path)
    monkeypatch.setattr(batch_module, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(batch_module, "probe_video", lambda _path: None)
    monkeypatch.setattr(batch_module, "_confirm_overwrite", lambda *args: True)
    monkeypatch.setattr(BatchPanel, "_start_queue", lambda _self: None)

    panel.txt_name_template.setText("{index}-{name}{suffix}{ext}")
    panel.add_paths([source])
    panel._start_batch()

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
    monkeypatch.setattr(batch_module, "probe_video", lambda _path: None)
    monkeypatch.setattr(batch_module, "_confirm_overwrite", lambda *args: True)
    monkeypatch.setattr(BatchPanel, "_start_queue", lambda _self: None)

    panel.add_paths([first, second])
    panel._start_batch()
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
