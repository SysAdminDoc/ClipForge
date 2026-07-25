"""Streams panel -- media info, stream management, remux, snapshot, contact sheet."""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QTextEdit, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal

from clipforge_utils import format_duration, format_size, format_bitrate

from ..constants import C
from ..tools import FFMPEG, _confirm_overwrite, probe_video
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

        # Contact sheet / thumbnail grid
        cs_grp = QGroupBox("Contact Sheet Generator")
        cs_layout = QVBoxLayout(cs_grp)
        cs_opts = QHBoxLayout()
        cs_opts.addWidget(QLabel("Columns:"))
        self.spn_cs_cols = QSpinBox()
        self.spn_cs_cols.setRange(1, 10)
        self.spn_cs_cols.setValue(4)
        cs_opts.addWidget(self.spn_cs_cols)
        cs_opts.addWidget(QLabel("Rows:"))
        self.spn_cs_rows = QSpinBox()
        self.spn_cs_rows.setRange(1, 10)
        self.spn_cs_rows.setValue(4)
        cs_opts.addWidget(self.spn_cs_rows)
        cs_opts.addWidget(QLabel("Format:"))
        self.cmb_cs_format = QComboBox()
        self.cmb_cs_format.addItems(["PNG", "JPG"])
        cs_opts.addWidget(self.cmb_cs_format)
        cs_opts.addStretch()
        cs_layout.addLayout(cs_opts)
        cs_btn_row = QHBoxLayout()
        self.lbl_cs_info = QLabel("Generates an NxM grid of evenly-spaced thumbnails")
        self.lbl_cs_info.setProperty("class", "dimLabel")
        cs_btn_row.addWidget(self.lbl_cs_info)
        cs_btn_row.addStretch()
        self.btn_contact_sheet = QPushButton("Generate Contact Sheet")
        self.btn_contact_sheet.setObjectName("primaryBtn")
        self.btn_contact_sheet.setEnabled(False)
        self.btn_contact_sheet.clicked.connect(self._do_contact_sheet)
        cs_btn_row.addWidget(self.btn_contact_sheet)
        cs_layout.addLayout(cs_btn_row)
        layout.addWidget(cs_grp)

        # Chapter file
        chap_grp = QGroupBox("Chapter Metadata")
        chap_layout = QVBoxLayout(chap_grp)
        chap_info = QHBoxLayout()
        self.lbl_chapter_file = QLabel("No chapter file selected")
        self.lbl_chapter_file.setProperty("class", "dimLabel")
        self.btn_browse_chapters = QPushButton("Browse chapters.txt")
        self.btn_browse_chapters.setToolTip(
            "YouTube-style chapter file: one line per chapter, e.g.:\n"
            "00:00 Intro\n00:30 Topic 1\n01:15 Topic 2"
        )
        self.btn_browse_chapters.clicked.connect(self._browse_chapters)
        chap_info.addWidget(self.lbl_chapter_file, 1)
        chap_info.addWidget(self.btn_browse_chapters)
        chap_layout.addLayout(chap_info)
        chap_btn_row = QHBoxLayout()
        chap_btn_row.addStretch()
        self.btn_mux_chapters = QPushButton("Mux Chapters into Video")
        self.btn_mux_chapters.setObjectName("primaryBtn")
        self.btn_mux_chapters.setEnabled(False)
        self.btn_mux_chapters.clicked.connect(self._do_mux_chapters)
        chap_btn_row.addWidget(self.btn_mux_chapters)
        chap_layout.addLayout(chap_btn_row)
        layout.addWidget(chap_grp)
        self._chapter_path = None

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        has_ffmpeg = bool(FFMPEG)
        self.btn_remux.setEnabled(has_ffmpeg)
        self.btn_snapshot.setEnabled(has_ffmpeg)
        self.btn_contact_sheet.setEnabled(has_ffmpeg)
        self.btn_mux_chapters.setEnabled(has_ffmpeg and self._chapter_path is not None)
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
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
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
        self._worker = FFmpegWorker(
            cmd,
            duration,
            output_path=out_path,
            overwrite=overwrite,
        )
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
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
        seek_sec = 0
        if self._player:
            seek_sec = self._player.get_position_sec()
        cmd = [FFMPEG, "-y", "-ss", str(seek_sec), "-i", self._filepath, "-frames:v", "1", "-q:v", "1", out_path]
        self.btn_snapshot.setEnabled(False)
        self._worker = FFmpegWorker(
            cmd,
            0,
            parse_progress=False,
            output_path=out_path,
            overwrite=overwrite,
        )
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_snapshot_done(ok, msg, out_path)
        )
        self._worker.start()

    def _on_snapshot_done(self, ok, msg, out_path):
        self.btn_snapshot.setEnabled(bool(FFMPEG) and bool(self._filepath))
        if ok and os.path.exists(out_path):
            size = format_size(os.path.getsize(out_path))
            self.requestToast.emit(f"Snapshot saved ({size})", C["green"])
        else:
            self.requestToast.emit(f"Snapshot failed: {msg}", C["red"])

    def _do_contact_sheet(self):
        """Generate an NxM contact sheet of evenly-spaced thumbnails."""
        if not self._filepath or not FFMPEG or not self._info:
            return
        cols = self.spn_cs_cols.value()
        rows = self.spn_cs_rows.value()
        total_frames = cols * rows
        duration = self._info.get("duration", 0)
        if duration <= 0:
            self.requestToast.emit("Cannot generate contact sheet: unknown duration", C["red"])
            return
        fmt = self.cmb_cs_format.currentText().lower()
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Contact Sheet",
            str(src.parent / f"{src.stem}_contact_{cols}x{rows}.{fmt}"),
            f"Images (*.{fmt});;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
        # Use FFmpeg select filter + tile filter for efficient single-pass generation
        # select every Nth frame to get evenly spaced frames across duration
        interval = duration / total_frames
        fps = self._info.get("fps", 30) or 30
        frame_interval = max(1, int(fps * interval))
        w = self._info.get("width", 1920)
        tile_w = min(320, w)
        vf = (f"select='not(mod(n\\,{frame_interval}))',"
              f"scale={tile_w}:-1,tile={cols}x{rows}")
        cmd = [FFMPEG, "-y", "-i", self._filepath,
               "-vf", vf, "-frames:v", "1", "-q:v", "2", out_path]
        self.console.append(f"[Contact Sheet] Generating {cols}x{rows} grid...\n")
        self.progress.setRange(0, 0)
        self.btn_contact_sheet.setEnabled(False)
        self._worker = FFmpegWorker(
            cmd,
            0,
            parse_progress=False,
            output_path=out_path,
            overwrite=overwrite,
        )
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_contact_sheet_done(ok, msg, out_path))
        self._worker.start()

    def _on_contact_sheet_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_contact_sheet.setEnabled(True)
        if ok and os.path.exists(out_path):
            size = format_size(os.path.getsize(out_path))
            self.progress.setValue(100)
            self.requestToast.emit(f"Contact sheet saved ({size})", C["green"])
        else:
            self.requestToast.emit(f"Contact sheet failed: {msg}", C["red"])

    def _browse_chapters(self):
        """Browse for a YouTube-style chapter file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Chapter File", "",
            "Text Files (*.txt);;All Files (*)")
        if path:
            self._chapter_path = path
            self.lbl_chapter_file.setText(Path(path).name)
            self.btn_mux_chapters.setEnabled(bool(FFMPEG) and bool(self._filepath))

    def _parse_chapters(self, chapter_path):
        """Parse YouTube-style chapter file into FFmpeg metadata format.

        Expected input format (one per line):
            00:00 Intro
            00:30 Topic 1
            01:15:00 Long Chapter Title

        Returns FFmpeg metadata string for -metadata_file.
        """
        import re as _re
        entries = []
        try:
            with open(chapter_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Match HH:MM:SS or MM:SS at start, followed by title
                    m = _re.match(r"(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s+(.*)", line)
                    if m:
                        time_str = m.group(1)
                        title = m.group(2).strip()
                        parts = time_str.split(":")
                        if len(parts) == 3:
                            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                        else:
                            secs = int(parts[0]) * 60 + float(parts[1])
                        entries.append((secs, title))
        except (OSError, ValueError):
            return None

        if not entries:
            return None

        # Build FFmpeg metadata format
        duration = self._info.get("duration", 0) if self._info else 0
        lines = [";FFMETADATA1"]
        for i, (start, title) in enumerate(entries):
            end = entries[i + 1][0] if i + 1 < len(entries) else duration
            # FFmpeg uses milliseconds * 1000 for chapter timestamps (timebase 1/1000)
            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={int(start * 1000)}")
            lines.append(f"END={int(end * 1000)}")
            lines.append(f"title={title}")
        return "\n".join(lines)

    def _do_mux_chapters(self):
        """Mux chapter metadata into the video file."""
        if not self._filepath or not self._chapter_path or not FFMPEG:
            return
        metadata_content = self._parse_chapters(self._chapter_path)
        if not metadata_content:
            self.requestToast.emit("Could not parse chapter file", C["red"])
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Video with Chapters",
            str(src.parent / f"{src.stem}_chapters{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
        import tempfile
        meta_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="clipforge_meta_", delete=False, encoding="utf-8")
        meta_file.write(metadata_content)
        meta_file.close()
        cmd = [FFMPEG, "-y", "-i", self._filepath, "-i", meta_file.name,
               "-map_metadata", "1", "-c", "copy"]
        if src.suffix.lower() == ".mp4":
            cmd += ["-movflags", "+faststart"]
        cmd.append(out_path)
        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setRange(0, 0)
        self.btn_mux_chapters.setEnabled(False)
        self.console.append("[Chapters] Muxing chapter metadata...\n")
        self._meta_tmpfile = meta_file.name
        self._worker = FFmpegWorker(
            cmd,
            duration,
            parse_progress=False,
            output_path=out_path,
            overwrite=overwrite,
        )
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_chapters_done(ok, msg, out_path))
        self._worker.start()

    def _on_chapters_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_mux_chapters.setEnabled(True)
        # Clean up temp metadata file
        if hasattr(self, '_meta_tmpfile') and os.path.exists(self._meta_tmpfile):
            try:
                os.unlink(self._meta_tmpfile)
            except OSError:
                pass
        if ok and os.path.exists(out_path):
            size = format_size(os.path.getsize(out_path))
            self.progress.setValue(100)
            self.requestToast.emit(f"Chapters muxed ({size})", C["green"])
        else:
            self.requestToast.emit(f"Chapter muxing failed: {msg}", C["red"])
