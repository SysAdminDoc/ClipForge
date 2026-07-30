"""Batch processing panel."""

import sys
import os
import shutil
import subprocess
import string
from datetime import date
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QListWidget, QAbstractItemView,
    QProgressBar, QFileDialog, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size, estimate_output_size

from ..constants import C, VIDEO_EXTS
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
        self._setup_ui()

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
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addWidget(self.btn_remove_sel)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        gl.addLayout(btn_row)
        layout.addWidget(grp)

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
        self.btn_start = QPushButton("Start Batch")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.clicked.connect(self._start_batch)
        action_row.addStretch()
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_start)
        layout.addLayout(action_row)
        layout.addStretch()

        self._out_dir = None

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Videos", str(Path.home() / "Videos"),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts);;All Files (*)")
        for p in paths:
            self._items.append(p)
            self.file_list.addItem(Path(p).name)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with Videos")
        if folder:
            for ext in VIDEO_EXTS:
                for f in Path(folder).glob(f"*{ext}"):
                    self._items.append(str(f))
                    self.file_list.addItem(f.name)

    def _clear_files(self):
        self._items.clear()
        self.file_list.clear()

    def _remove_selected(self):
        for item in sorted(self.file_list.selectedIndexes(), reverse=True):
            idx = item.row()
            self.file_list.takeItem(idx)
            if idx < len(self._items):
                self._items.pop(idx)

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

    def _set_batch_running(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_cancel.setVisible(running)
        self.btn_cancel.setEnabled(running)
        for widget in (
            self.file_list,
            self.btn_add,
            self.btn_add_folder,
            self.btn_clear,
            self.btn_remove_sel,
            self.cmb_operation,
            self.txt_name_template,
            self.chk_custom_dir,
            self.btn_out_dir,
        ):
            widget.setEnabled(not running)
        if not running:
            self.btn_out_dir.setEnabled(self.chk_custom_dir.isChecked())

    def _build_cmd(self, src_path, out_path, operation):
        if not FFMPEG:
            return None
        cmd = [FFMPEG, "-y", "-i", src_path]
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
            cmd = [FFMPEG, "-y", "-i", src_path, "-t", "30", "-c", "copy",
                   "-avoid_negative_ts", "make_zero", out_path]
        return cmd

    def _start_batch(self):
        if not self._items or not FFMPEG:
            return
        operation = self.cmb_operation.currentText()
        batch_items = tuple(self._items)
        template = self.txt_name_template.text()
        output_dir = self._out_dir
        self._batch_overwrite = {}
        output_keys = set()
        batch_outputs = []
        for index, source_path in enumerate(batch_items):
            try:
                output_path = self._get_output_path(
                    source_path,
                    operation,
                    template=template,
                    index=index,
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
            batch_outputs.append(output_path)
            if not _confirm_overwrite(self, output_path, source_path):
                return
            self._batch_overwrite[output_path] = os.path.exists(output_path)
        out_dir = output_dir or (str(Path(batch_items[0]).parent) if batch_items else "")
        if out_dir:
            try:
                usage = shutil.disk_usage(out_dir)
                estimated_needed = 0
                for p in batch_items:
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
        self._current_idx = 0
        self._cancel_requested = False
        self._batch_items = batch_items
        self._batch_outputs = tuple(batch_outputs)
        self._batch_operation = operation
        self._batch_out_dir = output_dir
        self._batch_post_action = self.cmb_post_action.currentText()
        self._set_batch_running(True)
        self._process_next()

    def _process_next(self):
        if self._current_idx >= len(self._batch_items):
            self._set_batch_running(False)
            self.lbl_batch_status.setText(
                f"Batch complete: {len(self._batch_items)} files processed"
            )
            self.requestToast.emit(
                f"Batch complete: {len(self._batch_items)} files", C["green"]
            )
            self._post_completion()
            return

        src = self._batch_items[self._current_idx]
        operation = self._batch_operation
        out_path = self._batch_outputs[self._current_idx]
        cmd = self._build_cmd(src, out_path, operation)

        self.lbl_batch_status.setText(
            f"Processing {self._current_idx + 1}/{len(self._batch_items)}: {Path(src).name}"
        )
        self.progress.setValue(0)

        item = self.file_list.item(self._current_idx)
        if item:
            item.setText(f"⟳  {Path(src).name}")

        info = probe_video(src)
        duration = info.get("duration", 0) if info else 0

        self._worker = FFmpegWorker(
            cmd,
            duration,
            output_path=out_path,
            overwrite=self._batch_overwrite.get(out_path, False),
        )
        self._worker.progress.connect(self._on_item_progress)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_item_done)
        self._worker.start()

    def _on_item_progress(self, pct):
        total = len(self._batch_items)
        overall = (self._current_idx / total + pct / 100 / total) * 100
        self.progress.setValue(int(overall))

    def _on_item_done(self, ok, msg):
        item = self.file_list.item(self._current_idx)
        if item:
            if ok:
                item.setText(f"✓  {Path(self._batch_items[self._current_idx]).name}")
            else:
                item.setText(f"✗  {Path(self._batch_items[self._current_idx]).name}")
                self.console.append(f"[ERROR] {msg}\n")
        if self._cancel_requested:
            self._set_batch_running(False)
            self.lbl_batch_status.setText("Batch cancelled")
            self._cancel_requested = False
            return
        self._current_idx += 1
        self._process_next()

    def _cancel(self):
        if self._worker:
            self._cancel_requested = True
            self.btn_cancel.setEnabled(False)
            self._worker.cancel()
            self.lbl_batch_status.setText("Cancelling current job...")

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
