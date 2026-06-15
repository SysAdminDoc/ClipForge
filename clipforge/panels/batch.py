"""Batch processing panel."""

import sys
import os
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QListWidget, QAbstractItemView,
    QProgressBar, QFileDialog, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size, estimate_output_size

from ..constants import C, VIDEO_EXTS
from ..tools import FFMPEG, probe_video
from ..workers import FFmpegWorker


class BatchPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._items = []
        self._worker = None
        self._current_idx = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grp = QGroupBox("File Queue (drag & drop or browse)")
        gl = QVBoxLayout(grp)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(140)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        gl.addWidget(self.file_list)

        btn_row = QHBoxLayout()
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
        ol = QHBoxLayout(op_grp)
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
        nl = QHBoxLayout(name_grp)
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
        outl = QHBoxLayout(out_grp)
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
        post_l = QHBoxLayout(post_grp)
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

        action_row = QHBoxLayout()
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
        import datetime
        preview = template.format(
            name="example_video", suffix="_h264", ext=".mp4",
            date=datetime.date.today().isoformat(), index="001"
        )
        self.lbl_name_preview.setText(f"Preview: {preview}")

    def _get_output_path(self, src_path, operation):
        src = Path(src_path)
        suffix_map = {
            "Convert to MP4 (H.264)": ("_h264", ".mp4"),
            "Convert to MKV (H.265)": ("_h265", ".mkv"),
            "Convert to WebM (VP9)": ("_vp9", ".webm"),
            "Downscale to 1080p": ("_1080p", src.suffix),
            "Downscale to 720p": ("_720p", src.suffix),
            "Extract Audio (MP3)": ("", ".mp3"),
            "Extract Audio (AAC)": ("", ".aac"),
            "Remove Audio": ("_noaudio", src.suffix),
            "Lossless Trim (first 30s)": ("_30s", src.suffix),
        }
        name_suffix, ext = suffix_map.get(operation, ("_out", src.suffix))
        out_dir = Path(self._out_dir) if self._out_dir else src.parent

        template = self.txt_name_template.text().strip()
        if template and template != "{name}{suffix}{ext}":
            import datetime
            try:
                fname = template.format(
                    name=src.stem, suffix=name_suffix, ext=ext,
                    date=datetime.date.today().isoformat(),
                    index=f"{self._current_idx + 1:03d}"
                )
                if not fname.endswith(ext):
                    fname += ext
                return str(out_dir / fname)
            except (KeyError, ValueError):
                pass
        return str(out_dir / f"{src.stem}{name_suffix}{ext}")

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
        out_dir = self._out_dir or (str(Path(self._items[0]).parent) if self._items else "")
        if out_dir:
            try:
                usage = shutil.disk_usage(out_dir)
                estimated_needed = 0
                operation = self.cmb_operation.currentText()
                for p in self._items:
                    if not os.path.exists(p):
                        continue
                    info = probe_video(p)
                    if info and "Downscale" in operation or "Convert" in operation:
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
            except OSError:
                pass
        self._current_idx = 0
        self.btn_start.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self._process_next()

    def _process_next(self):
        if self._current_idx >= len(self._items):
            self.btn_start.setEnabled(True)
            self.btn_cancel.setVisible(False)
            self.lbl_batch_status.setText(f"Batch complete: {len(self._items)} files processed")
            self.requestToast.emit(f"Batch complete: {len(self._items)} files", C["green"])
            self._post_completion()
            return

        src = self._items[self._current_idx]
        operation = self.cmb_operation.currentText()
        out_path = self._get_output_path(src, operation)
        cmd = self._build_cmd(src, out_path, operation)

        self.lbl_batch_status.setText(
            f"Processing {self._current_idx + 1}/{len(self._items)}: {Path(src).name}"
        )
        self.progress.setValue(0)

        item = self.file_list.item(self._current_idx)
        if item:
            item.setText(f"⟳  {Path(src).name}")

        info = probe_video(src)
        duration = info.get("duration", 0) if info else 0

        self._worker = FFmpegWorker(cmd, duration)
        self._worker.progress.connect(self._on_item_progress)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_item_done)
        self._worker.start()

    def _on_item_progress(self, pct):
        total = len(self._items)
        overall = (self._current_idx / total + pct / 100 / total) * 100
        self.progress.setValue(int(overall))

    def _on_item_done(self, ok, msg):
        item = self.file_list.item(self._current_idx)
        if item:
            if ok:
                item.setText(f"✓  {Path(self._items[self._current_idx]).name}")
            else:
                item.setText(f"✗  {Path(self._items[self._current_idx]).name}")
                self.console.append(f"[ERROR] {msg}\n")
        self._current_idx += 1
        self._process_next()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self.btn_start.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.lbl_batch_status.setText("Batch cancelled")

    def _post_completion(self):
        action = self.cmb_post_action.currentText()
        if action == "Open output folder":
            out_dir = self._out_dir or (str(Path(self._items[0]).parent) if self._items else "")
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
