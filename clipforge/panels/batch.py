"""Batch processing panel."""

import sys
import os
import shutil
import subprocess
import string
from datetime import date
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QListWidget,
    QAbstractItemView, QProgressBar, QFileDialog, QLineEdit, QSpinBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from clipforge_utils import format_size, estimate_output_size

from ..constants import C, VIDEO_EXTS
from ..job_queue import JobQueue, JobRecord, QueueError
from ..processes import (
    AUDIO_ONLY_STREAM_POLICY,
    TRANSCODE_STREAM_POLICY,
    VIDEO_ONLY_STREAM_POLICY,
    StreamSelectionPolicy,
    WorkerOutcome,
    output_contract_for_streams,
)
from ..tools import FFMPEG, _confirm_overwrite, probe_video
from ..workers import FFmpegWorker
from ..widgets import FlowLayout


_BATCH_SUFFIXES = {
    "Convert to MP4 (H.264)": ("_h264", ".mp4"),
    "Convert to MKV (H.265)": ("_h265", ".mkv"),
    "Convert to WebM (VP9)": ("_vp9", ".webm"),
    "Downscale to 1080p": ("_1080p", None),
    "Downscale to 720p": ("_720p", None),
    "Extract Audio (MP3)": ("", ".mp3"),
    "Extract Audio (AAC)": ("", ".aac"),
    "Remove Audio": ("_noaudio", None),
    "Lossless Trim (first 30s)": ("_30s", None),
}
_BATCH_TEMPLATE_FIELDS = {"name", "suffix", "ext", "date", "index"}
_INVALID_FILENAME_CHARS = set('<>:"/\\|?*\0')


def build_batch_output_path(
    src_path,
    operation,
    output_dir=None,
    template="{name}{suffix}{ext}",
    index=0,
    run_date=None,
):
    """Render and confine a batch output to one direct output-directory child."""
    src = Path(src_path)
    name_suffix, configured_ext = _BATCH_SUFFIXES.get(
        operation, ("_out", None)
    )
    ext = configured_ext or src.suffix
    template = str(template or "{name}{suffix}{ext}").strip()
    formatter = string.Formatter()
    for _literal, field_name, format_spec, conversion in formatter.parse(template):
        if field_name is None:
            continue
        if (
            field_name not in _BATCH_TEMPLATE_FIELDS
            or format_spec
            or conversion
        ):
            raise ValueError(
                "Template fields must be one of {name}, {suffix}, {ext}, "
                "{date}, or {index}, without conversions or format specifiers"
            )
    try:
        filename = template.format(
            name=src.stem,
            suffix=name_suffix,
            ext=ext,
            date=(run_date or date.today()).isoformat(),
            index=f"{index + 1:03d}",
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid output template: {exc}") from exc
    if not filename.endswith(ext):
        filename += ext
    if (
        not filename
        or filename in {".", ".."}
        or Path(filename).name != filename
        or any(char in _INVALID_FILENAME_CHARS for char in filename)
    ):
        raise ValueError("Output template must produce a valid filename, not a path")

    root = Path(output_dir).resolve() if output_dir else src.parent.resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise ValueError("Batch output escaped the selected output directory")
    return str(candidate)


class BatchPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._items = []
        self._worker = None
        self._current_idx = 0
        self._cancel_requested = False
        self._batch_overwrite = {}
        self._batch_items = ()
        self._batch_outputs = ()
        self._batch_operation = None
        self._batch_out_dir = None
        self._batch_post_action = None
        self._queue = JobQueue()
        self._row_job_ids = []
        self._row_priorities = []
        self._current_job_id = None
        self._queue_paused = False
        self._outcome_received = False
        self._continue_after_worker = False
        self._setup_ui()
        self._restore_queue()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grp = QGroupBox("File Queue (drag & drop or browse)")
        gl = QVBoxLayout(grp)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName("Batch file queue")
        self.file_list.setMinimumHeight(140)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.currentRowChanged.connect(self._on_current_row_changed)
        gl.addWidget(self.file_list)

        btn_row = FlowLayout()
        self.btn_add = QPushButton("Add Files")
        self.btn_add.clicked.connect(self._add_files)
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_add_folder.clicked.connect(self._add_folder)
        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.clicked.connect(self._clear_files)
        self.btn_remove_sel = QPushButton("Remove Selected")
        self.btn_remove_sel.clicked.connect(self._remove_selected)
        self.btn_move_up = QPushButton("Move Up")
        self.btn_move_up.setAccessibleName("Move selected batch job up")
        self.btn_move_up.clicked.connect(lambda: self._move_selected(-1))
        self.btn_move_down = QPushButton("Move Down")
        self.btn_move_down.setAccessibleName("Move selected batch job down")
        self.btn_move_down.clicked.connect(lambda: self._move_selected(1))
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addWidget(self.btn_remove_sel)
        btn_row.addWidget(self.btn_move_up)
        btn_row.addWidget(self.btn_move_down)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        gl.addLayout(btn_row)
        layout.addWidget(grp)

        priority_row = FlowLayout()
        priority_row.addWidget(QLabel("Selected priority:"))
        self.spn_priority = QSpinBox()
        self.spn_priority.setRange(-10, 10)
        self.spn_priority.setValue(0)
        self.spn_priority.setToolTip("Higher-priority jobs run first; range -10 to 10")
        self.spn_priority.setAccessibleName("Selected batch job priority")
        self.spn_priority.valueChanged.connect(self._set_selected_priority)
        priority_row.addWidget(self.spn_priority)
        priority_row.addWidget(QLabel("Higher values run first"))
        priority_row.addStretch()
        layout.addLayout(priority_row)

        op_grp = QGroupBox("Batch Operation")
        ol = FlowLayout(op_grp)
        ol.addWidget(QLabel("Operation:"))
        self.cmb_operation = QComboBox()
        self.cmb_operation.addItems([
            "Convert to MP4 (H.264)", "Convert to MKV (H.265)", "Convert to WebM (VP9)",
            "Downscale to 1080p", "Downscale to 720p",
            "Extract Audio (MP3)", "Extract Audio (AAC)",
            "Remove Audio", "Lossless Trim (first 30s)",
        ])
        ol.addWidget(self.cmb_operation, 1)
        layout.addWidget(op_grp)

        # Output naming template
        name_grp = QGroupBox("Output Naming")
        nl = FlowLayout(name_grp)
        nl.addWidget(QLabel("Template:"))
        self.txt_name_template = QLineEdit("{name}{suffix}{ext}")
        self.txt_name_template.setToolTip("Variables: {name}, {suffix}, {ext}, {date}, {index}")
        nl.addWidget(self.txt_name_template, 1)
        self.lbl_name_preview = QLabel("")
        self.lbl_name_preview.setProperty("class", "dimLabel")
        nl.addWidget(self.lbl_name_preview)
        self.txt_name_template.textChanged.connect(self._update_name_preview)
        layout.addWidget(name_grp)

        out_grp = QGroupBox("Output")
        outl = FlowLayout(out_grp)
        self.lbl_out_dir = QLabel("Same as source (with suffix)")
        self.lbl_out_dir.setProperty("class", "dimLabel")
        self.chk_custom_dir = QCheckBox("Custom output directory:")
        self.chk_custom_dir.toggled.connect(self._toggle_custom_dir)
        self.btn_out_dir = QPushButton("Browse")
        self.btn_out_dir.setEnabled(False)
        self.btn_out_dir.clicked.connect(self._browse_out_dir)
        outl.addWidget(self.chk_custom_dir)
        outl.addWidget(self.lbl_out_dir, 1)
        outl.addWidget(self.btn_out_dir)
        layout.addWidget(out_grp)

        # Post-completion
        post_grp = QGroupBox("After Completion")
        post_l = FlowLayout(post_grp)
        self.cmb_post_action = QComboBox()
        self.cmb_post_action.addItems(["Do nothing", "Open output folder", "Play notification sound"])
        post_l.addWidget(QLabel("Action:"))
        post_l.addWidget(self.cmb_post_action)
        post_l.addStretch()
        layout.addWidget(post_grp)

        self.lbl_batch_status = QLabel("")
        self.lbl_batch_status.setProperty("class", "accentLabel")
        layout.addWidget(self.lbl_batch_status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        action_row = FlowLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("dangerBtn")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_pause = QPushButton("Pause Queue")
        self.btn_pause.setAccessibleName("Pause or resume batch queue")
        self.btn_pause.setVisible(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_retry = QPushButton("Retry Failed")
        self.btn_retry.setAccessibleName("Retry failed batch jobs")
        self.btn_retry.clicked.connect(self._retry_failed)
        self.btn_start = QPushButton("Start Batch")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.clicked.connect(self._start_batch)
        action_row.addStretch()
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_pause)
        action_row.addWidget(self.btn_retry)
        action_row.addWidget(self.btn_start)
        layout.addLayout(action_row)
        layout.addStretch()

        self._out_dir = None
        self._set_batch_running(False)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Videos", str(Path.home() / "Videos"),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts);;All Files (*)")
        self.add_paths(paths)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with Videos")
        if folder:
            paths = []
            for ext in VIDEO_EXTS:
                for f in Path(folder).glob(f"*{ext}"):
                    paths.append(str(f))
            self.add_paths(paths)

    def add_paths(self, paths):
        """Add unsaved sources without disturbing durable queue records."""
        if self._queue.active:
            self.requestToast.emit(
                "Finish or stop the active batch before adding files", C["yellow"]
            )
            return
        for path in paths:
            source = str(path)
            self._items.append(source)
            self._row_job_ids.append(None)
            self._row_priorities.append(0)
            self.file_list.addItem(self._format_row(source, None, 0))
        self._refresh_action_state()

    @staticmethod
    def _format_row(source, state, priority):
        symbols = {
            None: "○",
            "queued": "○",
            "paused": "Ⅱ",
            "running": "⟳",
            "cancelling": "…",
            "succeeded": "✓",
            "failed": "✗",
            "interrupted": "↺",
        }
        priority_text = f"  [priority {priority}]" if priority else ""
        return f"{symbols.get(state, '○')}  {Path(source).name}{priority_text}"

    def _row_job(self, row):
        if row < 0 or row >= len(self._row_job_ids):
            return None
        job_id = self._row_job_ids[row]
        if not job_id:
            return None
        return next((job for job in self._queue.jobs if job.job_id == job_id), None)

    def _on_current_row_changed(self, row):
        self.spn_priority.blockSignals(True)
        job = self._row_job(row)
        priority = job.priority if job else (
            self._row_priorities[row] if 0 <= row < len(self._row_priorities) else 0
        )
        self.spn_priority.setValue(priority)
        self.spn_priority.blockSignals(False)

    def _refresh_queue_rows(self):
        jobs = {job.job_id: job for job in self._queue.jobs}
        for row, source in enumerate(self._items):
            if row >= self.file_list.count():
                self.file_list.addItem("")
            item = self.file_list.item(row)
            job = jobs.get(self._row_job_ids[row]) if row < len(self._row_job_ids) else None
            priority = job.priority if job else self._row_priorities[row]
            state = job.state if job else None
            item.setText(self._format_row(source, state, priority))
            item.setToolTip(str(Path(source).resolve()))
            item.setData(Qt.ItemDataRole.UserRole, self._row_job_ids[row])
        while self.file_list.count() > len(self._items):
            self.file_list.takeItem(self.file_list.count() - 1)
        self._on_current_row_changed(self.file_list.currentRow())
        self._refresh_action_state()

    def _clear_files(self):
        if self._queue.active:
            return
        self._items.clear()
        self._row_job_ids.clear()
        self._row_priorities.clear()
        self.file_list.clear()
        try:
            self._queue.clear()
        except QueueError as exc:
            self.requestToast.emit(f"Could not clear saved queue: {exc}", C["red"])
        self._refresh_action_state()

    def _remove_selected(self):
        if self._queue.active:
            return
        selected_rows = sorted(
            {index.row() for index in self.file_list.selectedIndexes()}, reverse=True
        )
        job_ids = [
            self._row_job_ids[row]
            for row in selected_rows
            if 0 <= row < len(self._row_job_ids) and self._row_job_ids[row]
        ]
        try:
            self._queue.remove(job_ids)
        except QueueError as exc:
            self.requestToast.emit(f"Could not remove saved jobs: {exc}", C["red"])
            return
        for idx in selected_rows:
            self.file_list.takeItem(idx)
            if idx < len(self._items):
                self._items.pop(idx)
            if idx < len(self._row_job_ids):
                self._row_job_ids.pop(idx)
            if idx < len(self._row_priorities):
                self._row_priorities.pop(idx)
        self._refresh_queue_rows()

    def _move_selected(self, delta):
        if self._queue.active:
            return
        row = self.file_list.currentRow()
        target = row + int(delta)
        if row < 0 or target < 0 or target >= len(self._items):
            return
        job_id = self._row_job_ids[row]
        target_job_id = self._row_job_ids[target]
        if bool(job_id) != bool(target_job_id):
            self.requestToast.emit(
                "Saved and new jobs can only be reordered within their groups",
                C["yellow"],
            )
            return
        try:
            if job_id:
                self._queue.move(job_id, delta)
        except QueueError as exc:
            self.requestToast.emit(f"Could not reorder saved jobs: {exc}", C["red"])
            return
        for values in (self._items, self._row_job_ids, self._row_priorities):
            values[row], values[target] = values[target], values[row]
        item = self.file_list.takeItem(row)
        self.file_list.insertItem(target, item)
        self.file_list.setCurrentRow(target)
        self._refresh_queue_rows()

    def _set_selected_priority(self, priority):
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._items) or self._queue.active:
            return
        job_id = self._row_job_ids[row]
        try:
            if job_id:
                self._queue.set_priority(job_id, int(priority))
            else:
                self._row_priorities[row] = int(priority)
        except QueueError as exc:
            self.requestToast.emit(f"Could not set job priority: {exc}", C["red"])
            return
        self._refresh_queue_rows()

    def _toggle_custom_dir(self, checked):
        self.btn_out_dir.setEnabled(checked)
        if not checked:
            self.lbl_out_dir.setText("Same as source (with suffix)")
            self._out_dir = None

    def _browse_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if d:
            self._out_dir = d
            self.lbl_out_dir.setText(d)

    def _update_name_preview(self):
        template = self.txt_name_template.text()
        try:
            preview = Path(build_batch_output_path(
                "example_video.mov",
                "Convert to MP4 (H.264)",
                ".",
                template,
            )).name
            self.lbl_name_preview.setText(f"Preview: {preview}")
            self.lbl_name_preview.setStyleSheet("")
        except ValueError as exc:
            self.lbl_name_preview.setText(f"Invalid template: {exc}")
            self.lbl_name_preview.setStyleSheet(f"color: {C['red']};")

    def _restore_queue(self):
        jobs = self._queue.jobs
        if not jobs:
            return
        for job in jobs:
            self._items.append(job.source_path)
            self._row_job_ids.append(job.job_id)
            self._row_priorities.append(job.priority)
            self.file_list.addItem(self._format_row(
                job.source_path, job.state, job.priority
            ))
        first = jobs[0]
        snapshot = first.snapshot
        if first.operation in [
            self.cmb_operation.itemText(index)
            for index in range(self.cmb_operation.count())
        ]:
            self.cmb_operation.setCurrentText(first.operation)
        template = snapshot.get("template")
        if isinstance(template, str) and template:
            self.txt_name_template.setText(template)
        output_dir = snapshot.get("output_dir")
        if isinstance(output_dir, str) and output_dir:
            self._out_dir = output_dir
            self.chk_custom_dir.setChecked(True)
            self.lbl_out_dir.setText(output_dir)
        post_action = snapshot.get("post_action")
        if isinstance(post_action, str):
            self.cmb_post_action.setCurrentText(post_action)
        pending = sum(job.state == "queued" for job in jobs)
        retryable = sum(job.state in {"failed", "interrupted"} for job in jobs)
        self.lbl_batch_status.setText(
            f"Restored queue: {pending} queued, {retryable} failed or interrupted"
        )
        if self._queue.load_warning:
            self.requestToast.emit(self._queue.load_warning, C["yellow"])
        self._refresh_queue_rows()

    def _row_index(self, job_id):
        try:
            return self._row_job_ids.index(job_id)
        except ValueError:
            return -1

    def _has_retryable_jobs(self):
        return any(job.state in {"failed", "interrupted"} for job in self._queue.jobs)

    def _refresh_action_state(self):
        active = self._queue.active
        paused = self._queue.paused
        editable = not active
        self.btn_retry.setEnabled(editable and self._has_retryable_jobs())
        self.spn_priority.setEnabled(editable and self.file_list.currentRow() >= 0)
        self.btn_move_up.setEnabled(editable and self.file_list.currentRow() > 0)
        self.btn_move_down.setEnabled(
            editable and 0 <= self.file_list.currentRow() < len(self._items) - 1
        )
        self.btn_pause.setText("Resume Queue" if paused else "Pause Queue")

    def _set_batch_running(self, running):
        has_new = any(job_id is None for job_id in self._row_job_ids)
        self.btn_start.setEnabled(
            not running
            and self._worker is None
            and (has_new or bool(self._queue.has_pending))
        )
        self.btn_start.setText("Start Queue" if self._queue.jobs else "Start Batch")
        self.btn_cancel.setVisible(running)
        self.btn_cancel.setEnabled(running)
        self.btn_pause.setVisible(running)
        self.btn_pause.setEnabled(running)
        for widget in (
            self.file_list,
            self.btn_add,
            self.btn_add_folder,
            self.btn_clear,
            self.btn_remove_sel,
            self.btn_move_up,
            self.btn_move_down,
            self.spn_priority,
            self.cmb_operation,
            self.txt_name_template,
            self.chk_custom_dir,
            self.btn_out_dir,
        ):
            widget.setEnabled(not running)
        if not running:
            self.btn_out_dir.setEnabled(self.chk_custom_dir.isChecked())
        self._refresh_action_state()

    def _toggle_pause(self):
        if not self._queue.active:
            return
        try:
            if self._queue.paused:
                self._queue.resume()
                self.lbl_batch_status.setText("Queue resumed")
                self._process_next()
            else:
                self._queue.pause()
                self.lbl_batch_status.setText(
                    "Queue paused; the current job will finish before it stops"
                )
        except QueueError as exc:
            self.requestToast.emit(f"Could not change queue state: {exc}", C["red"])
        self._refresh_action_state()

    def _retry_failed(self):
        if self._queue.active:
            return
        try:
            retried = self._queue.retry_failed()
        except QueueError as exc:
            self.requestToast.emit(f"Could not retry failed jobs: {exc}", C["red"])
            return
        if not retried:
            return
        self.lbl_batch_status.setText(f"Retrying {len(retried)} failed jobs")
        self._start_queue()

    def _get_output_path(
        self,
        src_path,
        operation,
        *,
        template=None,
        index=None,
        output_dir=None,
    ):
        return build_batch_output_path(
            src_path,
            operation,
            self._out_dir if output_dir is None else output_dir,
            self.txt_name_template.text() if template is None else template,
            self._current_idx if index is None else index,
        )

    def _build_cmd(self, src_path, out_path, operation):
        if not FFMPEG:
            return None
        policy = self._stream_policy(operation)
        cmd = [FFMPEG, "-y", "-i", src_path] + policy.ffmpeg_args()
        if operation == "Convert to MP4 (H.264)":
            cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
        elif operation == "Convert to MKV (H.265)":
            cmd += ["-c:v", "libx265", "-crf", "22", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", out_path]
        elif operation == "Convert to WebM (VP9)":
            cmd += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k", out_path]
        elif operation == "Downscale to 1080p":
            cmd += ["-vf", "scale=1920:-2", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", out_path]
        elif operation == "Downscale to 720p":
            cmd += ["-vf", "scale=1280:-2", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", out_path]
        elif operation == "Extract Audio (MP3)":
            cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_path]
        elif operation == "Extract Audio (AAC)":
            cmd += ["-vn", "-c:a", "aac", "-b:a", "192k", out_path]
        elif operation == "Remove Audio":
            cmd += ["-c:v", "copy", "-an", out_path]
        elif operation == "Lossless Trim (first 30s)":
            trim_policy = StreamSelectionPolicy(
                audio="all",
                timestamps="reset",
            )
            cmd = [FFMPEG, "-y", "-i", src_path] + trim_policy.ffmpeg_args()
            cmd += ["-t", "30", "-c", "copy", out_path]
        return cmd

    @staticmethod
    def _stream_policy(operation):
        if operation in {"Extract Audio (MP3)", "Extract Audio (AAC)"}:
            return AUDIO_ONLY_STREAM_POLICY
        if operation == "Remove Audio":
            return VIDEO_ONLY_STREAM_POLICY
        return TRANSCODE_STREAM_POLICY

    @staticmethod
    def _output_contract(source_info, output_path, operation, duration):
        streams = (source_info or {}).get("streams", [])
        source_audio_count = sum(
            stream.get("codec_type") == "audio" for stream in streams
        )
        if operation in {"Extract Audio (MP3)", "Extract Audio (AAC)"}:
            audio_count = 1
            video_count = 0
            subtitle_count = 0
        elif operation == "Remove Audio":
            audio_count = 0
            video_count = 1
            subtitle_count = 0
        else:
            audio_count = source_audio_count if source_info is not None else None
            video_count = 1
            subtitle_count = 0
        codec_policy = {
            "Convert to MP4 (H.264)": (("video", ("h264",)), ("audio", ("aac",))),
            "Convert to MKV (H.265)": (("video", ("hevc",)), ("audio", ("aac",))),
            "Convert to WebM (VP9)": (("video", ("vp9",)), ("audio", ("opus",))),
            "Extract Audio (MP3)": (("audio", ("mp3",)),),
            "Extract Audio (AAC)": (("audio", ("aac",)),),
            "Downscale to 1080p": (("video", ("h264",)),),
            "Downscale to 720p": (("video", ("h264",)),),
        }.get(operation, ())
        return output_contract_for_streams(
            output_path,
            expected_duration=duration,
            video_count=video_count,
            audio_count=audio_count,
            subtitle_count=subtitle_count,
            allowed_codecs=codec_policy,
        )

    def _report_stream_policy(self, source_info, operation, source_path):
        """Surface intentional stream drops before a batch starts."""
        if not source_info:
            return False
        dropped = [
            stream.get("codec_type")
            for stream in source_info.get("streams", [])
            if stream.get("codec_type") in {"subtitle", "data", "attachment"}
        ]
        if not dropped:
            return False
        types = ", ".join(sorted(set(dropped)))
        self.console.append(
            f"[Batch stream policy] {Path(source_path).name}: "
            f"{types} stream(s) will be dropped; video/audio, metadata, "
            "chapters, and timestamps follow the selected operation.\n"
        )
        self.requestToast.emit(
            f"Batch will drop {types} stream(s); see the console for policy",
            C["yellow"],
        )
        return True

    def _start_batch(self):
        if self._queue.active or not self._items or not FFMPEG:
            return
        operation = self.cmb_operation.currentText()
        template = self.txt_name_template.text()
        output_dir = self._out_dir
        jobs_by_id = {job.job_id: job for job in self._queue.jobs}
        new_rows = [
            row for row, job_id in enumerate(self._row_job_ids) if not job_id
        ]
        if not new_rows and not self._queue.has_pending:
            self.requestToast.emit(
                "There are no queued jobs; use Retry Failed or add files",
                C["yellow"],
            )
            return

        output_keys = {
            os.path.normcase(os.path.abspath(job.output_path))
            for job in jobs_by_id.values()
        }
        prepared = []
        stream_warning_shown = False
        for row in new_rows:
            source_path = self._items[row]
            try:
                output_path = self._get_output_path(
                    source_path,
                    operation,
                    template=template,
                    index=row,
                    output_dir=output_dir,
                )
            except ValueError as exc:
                self.requestToast.emit(f"Invalid batch output: {exc}", C["red"])
                return
            output_key = os.path.normcase(os.path.abspath(output_path))
            if output_key in output_keys:
                self.requestToast.emit(
                    f"Duplicate batch output: {Path(output_path).name}",
                    C["red"],
                )
                return
            output_keys.add(output_key)
            if not _confirm_overwrite(self, output_path, source_path):
                return
            info = probe_video(source_path) if os.path.exists(source_path) else None
            if not stream_warning_shown:
                stream_warning_shown = self._report_stream_policy(
                    info, operation, source_path
                )
            source_duration = float(info.get("duration", 0) or 0) if info else 0.0
            duration = (
                min(30.0, source_duration)
                if operation == "Lossless Trim (first 30s)"
                else source_duration
            )
            command = self._build_cmd(source_path, output_path, operation)
            if not command:
                return
            prepared.append({
                "row": row,
                "source": source_path,
                "output": output_path,
                "duration": duration,
                "contract": self._output_contract(
                    info, output_path, operation, duration
                ),
                "command": command,
                "overwrite": os.path.exists(output_path),
            })

        out_dir = output_dir or (
            str(Path(self._items[new_rows[0]]).parent) if new_rows else ""
        )
        if out_dir:
            try:
                usage = shutil.disk_usage(out_dir)
                estimated_needed = 0
                for entry in prepared:
                    p = entry["source"]
                    if not os.path.exists(p):
                        continue
                    info = probe_video(p)
                    if info and (
                        "Downscale" in operation or "Convert" in operation
                    ):
                        w = info.get("width", 1920)
                        h = info.get("height", 1080)
                        dur = info.get("duration", 0)
                        estimated_needed += estimate_output_size(dur, 18, w, h)
                    else:
                        estimated_needed += int(os.path.getsize(p) * 1.2)
                if usage.free < estimated_needed:
                    self.requestToast.emit(
                        f"Low disk space: {format_size(usage.free)} free, ~{format_size(estimated_needed)} needed",
                        C["yellow"])
                    return
            except OSError:
                pass

        records = []
        post_action = self.cmb_post_action.currentText()
        for entry in prepared:
            records.append(JobRecord.create(
                entry["source"],
                entry["output"],
                operation,
                entry["command"],
                duration=entry["duration"],
                overwrite=entry["overwrite"],
                priority=self._row_priorities[entry["row"]],
                output_contract=entry["contract"],
                snapshot={
                    "template": template,
                    "output_dir": output_dir or "",
                    "post_action": post_action,
                    "index": entry["row"],
                },
            ))
        try:
            added = self._queue.add(records) if records else ()
        except QueueError as exc:
            self.requestToast.emit(f"Could not save batch queue: {exc}", C["red"])
            return
        for entry, job in zip(prepared, added):
            row = entry["row"]
            self._row_job_ids[row] = job.job_id
            self.file_list.item(row).setData(Qt.ItemDataRole.UserRole, job.job_id)
        self._batch_items = tuple(self._items)
        self._batch_outputs = tuple(job.output_path for job in self._queue.jobs)
        self._batch_operation = operation
        self._batch_out_dir = output_dir
        self._batch_post_action = post_action
        self._start_queue()

    def _start_queue(self):
        if self._queue.active or not self._queue.has_pending:
            self._set_batch_running(False)
            return
        self._cancel_requested = False
        self._current_job_id = None
        self._queue_paused = False
        self._queue.activate()
        self.progress.setValue(0)
        self._set_batch_running(True)
        self._process_next()

    def _process_next(self):
        if not self._queue.active or self._queue.paused:
            self._refresh_queue_rows()
            return
        job = self._queue.claim_next()
        self._refresh_queue_rows()
        if not job:
            if not self._queue.has_pending:
                self._finish_queue()
            return

        self._current_job_id = job.job_id
        self._current_idx = self._row_index(job.job_id)
        src = job.source_path
        out_path = job.output_path

        self.lbl_batch_status.setText(
            f"Processing {self._current_idx + 1}/{len(self._row_job_ids)}: {Path(src).name}"
        )
        self.progress.setValue(0)

        self._worker = FFmpegWorker(
            job.command,
            job.duration,
            output_path=out_path,
            overwrite=job.overwrite,
            output_contract=job.output_contract,
        )
        self._worker.progress.connect(self._on_item_progress)
        self._worker.log_output.connect(self.console.append)
        self._outcome_received = False
        self._continue_after_worker = False
        self._worker.finished.connect(self._on_worker_thread_finished)
        if hasattr(self._worker, "outcome_signal"):
            self._worker.outcome_signal.connect(self._on_item_outcome)
        else:
            self._worker.finished_signal.connect(self._on_item_done)
        self._worker.start()

    def _on_item_progress(self, pct):
        total = max(1, len(self._queue.jobs))
        completed = sum(job.state == "succeeded" for job in self._queue.jobs)
        overall = (completed + max(0.0, min(100.0, pct)) / 100) / total * 100
        self.progress.setValue(int(overall))
        if self._current_job_id:
            try:
                self._queue.update_progress(self._current_job_id, pct)
            except QueueError:
                pass

    def _on_item_outcome(self, outcome):
        if not isinstance(outcome, WorkerOutcome):
            return
        self._outcome_received = True
        self._complete_current_job(outcome)

    def _on_item_done(self, ok, msg):
        if self._outcome_received:
            return
        self._complete_current_job(WorkerOutcome(
            "succeeded" if ok else "failed",
            "completed" if ok else "process_failed",
            msg,
            output_valid=True if ok else None,
            cancelled=self._cancel_requested and not ok,
        ))

    def _complete_current_job(self, outcome):
        job_id = self._current_job_id
        if not job_id:
            return
        try:
            completed = self._queue.complete(
                job_id,
                outcome.succeeded,
                outcome.message,
                cancelled=outcome.cancelled,
                output_valid=outcome.output_valid,
            )
        except QueueError as exc:
            self.requestToast.emit(f"Could not record batch result: {exc}", C["red"])
            self._finish_queue(cancelled=True)
            return
        row = self._row_index(job_id)
        if row >= 0:
            self.file_list.setCurrentRow(row)
        if completed.state in {"failed", "interrupted"}:
            self.console.append(f"[ERROR] {outcome.message}\n")
        self._current_job_id = None
        self._refresh_queue_rows()
        if self._cancel_requested:
            self._cancel_requested = False
            self._continue_after_worker = False
            self._finish_queue(cancelled=True)
        elif self._queue.paused:
            self._continue_after_worker = False
            self.lbl_batch_status.setText(
                "Queue paused; resume when you are ready for the next job"
            )
            self._refresh_action_state()
        else:
            self._continue_after_worker = True
            if self._worker is None:
                self._continue_after_worker = False
                self._process_next()

    def _on_worker_thread_finished(self):
        self._worker = None
        if self._continue_after_worker:
            self._continue_after_worker = False
            self._process_next()
        elif not self._queue.active:
            self._set_batch_running(False)
        else:
            self._refresh_action_state()

    def _cancel(self):
        if not self._queue.active:
            return
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        try:
            if self._current_job_id:
                self._queue.cancel(self._current_job_id)
            if self._worker:
                self._worker.cancel()
            else:
                self._finish_queue(cancelled=True)
        except QueueError as exc:
            self.requestToast.emit(f"Could not cancel batch: {exc}", C["red"])
            self._finish_queue(cancelled=True)
        self.lbl_batch_status.setText("Cancelling current job...")

    def _finish_queue(self, *, cancelled=False):
        try:
            self._queue.deactivate()
        except QueueError as exc:
            self.requestToast.emit(f"Could not close batch queue: {exc}", C["red"])
        counts = self._queue.counts()
        self._set_batch_running(False)
        self._refresh_queue_rows()
        if cancelled:
            remaining = counts["queued"]
            self.lbl_batch_status.setText(
                f"Batch stopped; {remaining} queued job(s) remain"
            )
            return
        succeeded = counts["succeeded"]
        failed = counts["failed"] + counts["interrupted"]
        self.lbl_batch_status.setText(
            f"Batch complete: {succeeded} succeeded, {failed} failed or interrupted"
        )
        self.requestToast.emit(
            f"Batch complete: {succeeded} succeeded, {failed} failed",
            C["green"] if not failed else C["yellow"],
        )
        self._post_completion()

    def _post_completion(self):
        action = self._batch_post_action or self.cmb_post_action.currentText()
        if action == "Open output folder":
            out_dir = self._batch_out_dir or (
                str(Path(self._batch_items[0]).parent) if self._batch_items else ""
            )
            if out_dir:
                if sys.platform == "win32":
                    os.startfile(out_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", out_dir])
                else:
                    subprocess.Popen(["xdg-open", out_dir])
        elif action == "Play notification sound":
            try:
                if sys.platform == "win32":
                    import winsound
                    winsound.MessageBeep(winsound.MB_OK)
            except (ImportError, OSError):
                pass
