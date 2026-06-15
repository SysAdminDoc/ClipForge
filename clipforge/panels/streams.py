"""Streams panel -- media info, stream management, remux, snapshot."""

import sys
import os
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QTextEdit, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal

from clipforge_utils import format_duration, format_size, format_bitrate

from ..constants import C
from ..tools import FFMPEG, probe_video
from ..workers import FFmpegWorker


class StreamsPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, player=None, parent=None):
        super().__init__(parent)
        self.console = console
        self._player = player
        self._filepath = None
        self._info = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Full media info
        info_grp = QGroupBox("Media Information")
        il = QVBoxLayout(info_grp)
        self.txt_media_info = QTextEdit()
        self.txt_media_info.setObjectName("cmdPreview")
        self.txt_media_info.setReadOnly(True)
        self.txt_media_info.setMaximumHeight(200)
        il.addWidget(self.txt_media_info)
        btn_copy_info = QPushButton("Copy Info")
        btn_copy_info.clicked.connect(lambda: QApplication.clipboard().setText(self.txt_media_info.toPlainText()))
        il.addWidget(btn_copy_info, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(info_grp)

        # Stream list with toggles
        stream_grp = QGroupBox("Streams (toggle for remux)")
        self._stream_layout = QVBoxLayout(stream_grp)
        self.lbl_no_streams = QLabel("Open a video to inspect streams")
        self.lbl_no_streams.setProperty("class", "dimLabel")
        self._stream_layout.addWidget(self.lbl_no_streams)
        self._stream_checks = []
        layout.addWidget(stream_grp)

        # Remux
        remux_grp = QGroupBox("Remux / Extract")
        rl = QHBoxLayout(remux_grp)
        rl.addWidget(QLabel("Container:"))
        self.cmb_remux_container = QComboBox()
        self.cmb_remux_container.addItems(["MP4", "MKV", "MOV", "WebM"])
        rl.addWidget(self.cmb_remux_container)
        self.btn_remux = QPushButton("Remux (no re-encode)")
        self.btn_remux.setObjectName("primaryBtn")
        self.btn_remux.setEnabled(False)
        self.btn_remux.clicked.connect(self._do_remux)
        rl.addStretch()
        rl.addWidget(self.btn_remux)
        layout.addWidget(remux_grp)

        # Snapshot
        snap_grp = QGroupBox("Frame Export")
        sl = QHBoxLayout(snap_grp)
        sl.addWidget(QLabel("Export current frame at full resolution"))
        self.btn_snapshot = QPushButton("Snapshot (PNG)")
        self.btn_snapshot.setObjectName("primaryBtn")
        self.btn_snapshot.setEnabled(False)
        self.btn_snapshot.clicked.connect(self._do_snapshot)
        sl.addStretch()
        sl.addWidget(self.btn_snapshot)
        layout.addWidget(snap_grp)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_remux.setEnabled(bool(FFMPEG))
        self.btn_snapshot.setEnabled(bool(FFMPEG))
        self._update_info()

    def _update_info(self):
        if not self._info:
            self.txt_media_info.setText("Open a video to see media information")
            return
        lines = []
        lines.append(f"File: {self._info.get('path', 'N/A')}")
        lines.append(f"Format: {self._info.get('format_name', 'N/A')}")
        lines.append(f"Duration: {format_duration(self._info.get('duration', 0))}")
        lines.append(f"Size: {format_size(self._info.get('size', 0))}")
        lines.append(f"Bitrate: {format_bitrate(self._info.get('bit_rate', 0))}")
        lines.append("")
        # Clear old stream checkboxes
        for chk in self._stream_checks:
            chk.setParent(None)
        self._stream_checks.clear()
        if self.lbl_no_streams.parent():
            self.lbl_no_streams.setParent(None)

        for s in self._info.get("streams", []):
            idx = s.get("index", 0)
            codec_type = s.get("codec_type", "unknown")
            codec_name = s.get("codec_name", "unknown")
            detail = f"Stream #{idx}: {codec_type} ({codec_name})"
            if codec_type == "video":
                detail += f" - {s.get('width', '?')}x{s.get('height', '?')}, {s.get('fps', '?')} fps"
                detail += f", {s.get('pix_fmt', '')}, {s.get('profile', '')}"
                if s.get('color_space'):
                    detail += f", {s['color_space']}"
            elif codec_type == "audio":
                detail += f" - {s.get('channels', '?')}ch, {s.get('sample_rate', '?')} Hz"
                detail += f", {s.get('channel_layout', '')}"
            elif codec_type == "subtitle":
                lang = s.get("language", "")
                title = s.get("title", "")
                detail += f" - {lang} {title}"
            lines.append(detail)
            chk = QCheckBox(detail)
            chk.setChecked(True)
            chk.setObjectName("streamItem")
            self._stream_layout.addWidget(chk)
            self._stream_checks.append(chk)

        # Tags
        tags = self._info.get("tags", {})
        if tags:
            lines.append("")
            lines.append("Metadata:")
            for k, v in tags.items():
                lines.append(f"  {k}: {v}")

        self.txt_media_info.setText("\n".join(lines))

    def _do_remux(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov", "WebM": ".webm"}
        container = self.cmb_remux_container.currentText()
        ext = ext_map.get(container, ".mkv")
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Remuxed Video", str(src.parent / f"{src.stem}_remux{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm);;All Files (*)")
        if not out_path:
            return
        cmd = [FFMPEG, "-y", "-i", self._filepath]
        # Map selected streams
        for i, chk in enumerate(self._stream_checks):
            if chk.isChecked():
                cmd += ["-map", f"0:{i}"]
        cmd += ["-c", "copy"]
        if container == "MP4":
            cmd += ["-movflags", "+faststart"]
        cmd.append(out_path)
        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setRange(0, 0)
        self.btn_remux.setEnabled(False)
        self._worker = FFmpegWorker(cmd, duration)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_remux_done(ok, msg, out_path))
        self._worker.start()

    def _on_remux_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_remux.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Remux complete ({size})", C["green"])
        else:
            self.requestToast.emit(f"Remux failed: {msg}", C["red"])

    def _do_snapshot(self):
        if not self._filepath or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", str(src.parent / f"{src.stem}_snapshot.png"),
            "Images (*.png *.jpg);;All Files (*)")
        if not out_path:
            return
        seek_sec = 0
        if self._player:
            seek_sec = self._player.get_position_sec()
        cmd = [FFMPEG, "-y", "-ss", str(seek_sec), "-i", self._filepath, "-frames:v", "1", "-q:v", "1", out_path]
        subprocess.run(cmd, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if os.path.exists(out_path):
            size = format_size(os.path.getsize(out_path))
            self.requestToast.emit(f"Snapshot saved ({size})", C["green"])
        else:
            self.requestToast.emit("Snapshot failed", C["red"])
