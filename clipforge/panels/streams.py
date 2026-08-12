"""Streams panel -- media info, stream management, remux, snapshot, contact sheet."""

import csv
import json
import os
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit,
    QProgressBar, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal

from clipforge_utils import format_duration, format_size, format_bitrate

from ..constants import C
from ..tools import (
    FFMPEG,
    _confirm_overwrite,
    escape_ffmetadata_value,
    stream_copy_issues,
)
from ..workers import FFmpegWorker, MediaProbeWorker, QualityMetricsWorker
from ..widgets import FlowLayout
from ..scene_detection import normalize_scene_markers, parse_scene_markers


class StreamsPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, player=None, parent=None):
        super().__init__(parent)
        self.console = console
        self._player = player
        self._filepath = None
        self._info = None
        self._worker = None
        self._quality_worker = None
        self._quality_probe_worker = None
        self._quality_path = None
        self._quality_info = None
        self._quality_report = None
        self._scene_worker = None
        self._scene_log = []
        self._scene_markers = []
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

        # Quality comparison
        quality_grp = QGroupBox("Quality Comparison")
        quality_layout = QVBoxLayout(quality_grp)
        quality_file_row = FlowLayout()
        quality_file_row.addWidget(QLabel("Encoded file:"))
        self.lbl_quality_file = QLabel("No comparison file selected")
        self.lbl_quality_file.setProperty("class", "dimLabel")
        quality_file_row.addWidget(self.lbl_quality_file, 1)
        self.btn_browse_quality = QPushButton("Browse")
        self.btn_browse_quality.clicked.connect(self._browse_quality_file)
        quality_file_row.addWidget(self.btn_browse_quality)
        quality_layout.addLayout(quality_file_row)

        sync_row = FlowLayout()
        sync_row.addWidget(QLabel("Encoded offset (seconds):"))
        self.spn_quality_offset = QDoubleSpinBox()
        self.spn_quality_offset.setRange(-30.0, 30.0)
        self.spn_quality_offset.setDecimals(3)
        self.spn_quality_offset.setSingleStep(0.100)
        self.spn_quality_offset.setToolTip(
            "Positive values trim the start of the encoded file; negative values "
            "trim the reference. Both timelines are then aligned at zero."
        )
        sync_row.addWidget(self.spn_quality_offset)
        self.lbl_quality_preflight = QLabel("Select an encoded file to preflight it")
        self.lbl_quality_preflight.setProperty("class", "dimLabel")
        sync_row.addWidget(self.lbl_quality_preflight, 1)
        quality_layout.addLayout(sync_row)

        self.txt_quality_results = QTextEdit()
        self.txt_quality_results.setObjectName("cmdPreview")
        self.txt_quality_results.setReadOnly(True)
        self.txt_quality_results.setMaximumHeight(115)
        self.txt_quality_results.setPlaceholderText("Compare the open reference video with an encoded output.")
        quality_layout.addWidget(self.txt_quality_results)

        quality_btn_row = FlowLayout()
        self.lbl_quality_hint = QLabel(
            "Compares the shorter aligned duration; encoded frames scale to the reference"
        )
        self.lbl_quality_hint.setProperty("class", "dimLabel")
        self.lbl_quality_hint.setWordWrap(True)
        quality_btn_row.addWidget(self.lbl_quality_hint)
        quality_btn_row.addStretch()
        self.btn_export_quality = QPushButton("Export Report")
        self.btn_export_quality.setEnabled(False)
        self.btn_export_quality.clicked.connect(self._export_quality_report)
        quality_btn_row.addWidget(self.btn_export_quality)
        self.btn_cancel_quality = QPushButton("Cancel")
        self.btn_cancel_quality.setEnabled(False)
        self.btn_cancel_quality.clicked.connect(self._cancel_quality_compare)
        quality_btn_row.addWidget(self.btn_cancel_quality)
        self.btn_compare_quality = QPushButton("Compare Quality")
        self.btn_compare_quality.setObjectName("primaryBtn")
        self.btn_compare_quality.setEnabled(False)
        self.btn_compare_quality.clicked.connect(self._do_quality_compare)
        quality_btn_row.addWidget(self.btn_compare_quality)
        quality_layout.addLayout(quality_btn_row)
        layout.addWidget(quality_grp)

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
        rl = FlowLayout(remux_grp)
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
        sl = FlowLayout(snap_grp)
        frame_export_hint = QLabel("Export current frame at full resolution")
        frame_export_hint.setWordWrap(True)
        sl.addWidget(frame_export_hint)
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
        cs_opts = FlowLayout()
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
        cs_btn_row = FlowLayout()
        self.lbl_cs_info = QLabel("Generates an NxM grid of evenly-spaced thumbnails")
        self.lbl_cs_info.setProperty("class", "dimLabel")
        self.lbl_cs_info.setWordWrap(True)
        cs_btn_row.addWidget(self.lbl_cs_info)
        cs_btn_row.addStretch()
        self.btn_contact_sheet = QPushButton("Generate Contact Sheet")
        self.btn_contact_sheet.setObjectName("primaryBtn")
        self.btn_contact_sheet.setEnabled(False)
        self.btn_contact_sheet.clicked.connect(self._do_contact_sheet)
        cs_btn_row.addWidget(self.btn_contact_sheet)
        cs_layout.addLayout(cs_btn_row)
        layout.addWidget(cs_grp)

        scene_grp = QGroupBox("Scene-change Markers (review only)")
        scene_layout = QVBoxLayout(scene_grp)
        scene_opts = FlowLayout()
        scene_opts.addWidget(QLabel("Sensitivity:"))
        self.spn_scene_threshold = QDoubleSpinBox()
        self.spn_scene_threshold.setRange(0.05, 0.95)
        self.spn_scene_threshold.setDecimals(2)
        self.spn_scene_threshold.setSingleStep(0.05)
        self.spn_scene_threshold.setValue(0.35)
        self.spn_scene_threshold.setAccessibleName("Scene detection sensitivity")
        self.spn_scene_threshold.setToolTip(
            "FFmpeg scene score threshold; lower values produce more review markers"
        )
        scene_opts.addWidget(self.spn_scene_threshold)
        self.lbl_scene_result = QLabel("No scene scan yet")
        self.lbl_scene_result.setProperty("class", "dimLabel")
        scene_opts.addWidget(self.lbl_scene_result, 1)
        scene_layout.addLayout(scene_opts)
        self.tbl_scene_markers = QTableWidget(0, 2)
        self.tbl_scene_markers.setAccessibleName("Reviewable scene markers")
        self.tbl_scene_markers.setHorizontalHeaderLabels(["Keep", "Time (seconds)"])
        self.tbl_scene_markers.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tbl_scene_markers.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.tbl_scene_markers.setMaximumHeight(150)
        self.tbl_scene_markers.itemChanged.connect(
            lambda _item: self._sync_scene_markers()
        )
        self.tbl_scene_markers.cellDoubleClicked.connect(
            lambda row, _column: self._jump_to_scene_marker(row)
        )
        scene_layout.addWidget(self.tbl_scene_markers)
        scene_btn_row = FlowLayout()
        self.btn_detect_scenes = QPushButton("Detect Scenes")
        self.btn_detect_scenes.setObjectName("primaryBtn")
        self.btn_detect_scenes.setEnabled(False)
        self.btn_detect_scenes.clicked.connect(self._do_detect_scenes)
        self.btn_cancel_scenes = QPushButton("Cancel")
        self.btn_cancel_scenes.setEnabled(False)
        self.btn_cancel_scenes.clicked.connect(self._cancel_scene_detection)
        self.btn_jump_scene = QPushButton("Jump to Selected")
        self.btn_jump_scene.setEnabled(False)
        self.btn_jump_scene.clicked.connect(self._jump_to_selected_scene)
        scene_btn_row.addStretch()
        scene_btn_row.addWidget(self.btn_jump_scene)
        scene_btn_row.addWidget(self.btn_cancel_scenes)
        scene_btn_row.addWidget(self.btn_detect_scenes)
        scene_layout.addLayout(scene_btn_row)
        layout.addWidget(scene_grp)

        # Chapter file
        chap_grp = QGroupBox("Chapter Metadata")
        chap_layout = QVBoxLayout(chap_grp)
        chap_info = FlowLayout()
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
        chap_btn_row = FlowLayout()
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
        if self._scene_worker and self._scene_worker.isRunning():
            self._scene_worker.cancel()
        has_ffmpeg = bool(FFMPEG)
        self.btn_remux.setEnabled(has_ffmpeg)
        self.btn_snapshot.setEnabled(has_ffmpeg)
        self.btn_contact_sheet.setEnabled(has_ffmpeg)
        self.btn_detect_scenes.setEnabled(has_ffmpeg)
        self.btn_cancel_scenes.setEnabled(False)
        self._scene_log = []
        self._populate_scene_markers([])
        self.lbl_scene_result.setText("No scene scan yet")
        self.btn_mux_chapters.setEnabled(has_ffmpeg and self._chapter_path is not None)
        self.btn_compare_quality.setEnabled(has_ffmpeg and self._quality_path is not None)
        self._update_info()

    def project_state(self):
        """Return the scene threshold and review markers for session files."""

        return {
            "scene_threshold": self.spn_scene_threshold.value(),
            "scene_markers": [
                {"time": marker.time, "keep": marker.keep}
                for marker in self._scene_markers
            ],
        }

    def restore_project_state(self, state):
        state = state if isinstance(state, dict) else {}
        try:
            self.spn_scene_threshold.setValue(
                max(0.05, min(0.95, float(state.get("scene_threshold", 0.35))))
            )
        except (TypeError, ValueError):
            self.spn_scene_threshold.setValue(0.35)
        self._populate_scene_markers(state.get("scene_markers"))

    def _scene_marker_rows(self):
        rows = []
        for row in range(self.tbl_scene_markers.rowCount()):
            keep_item = self.tbl_scene_markers.item(row, 0)
            time_item = self.tbl_scene_markers.item(row, 1)
            try:
                time_sec = float(time_item.text())
            except (AttributeError, TypeError, ValueError):
                continue
            rows.append({
                "time": time_sec,
                "keep": bool(
                    keep_item
                    and keep_item.checkState() == Qt.CheckState.Checked
                ),
            })
        duration = float((self._info or {}).get("duration") or 0)
        return normalize_scene_markers(rows, duration=duration)

    def _sync_scene_markers(self):
        if not hasattr(self, "tbl_scene_markers"):
            return
        self._scene_markers = self._scene_marker_rows()
        total = self.tbl_scene_markers.rowCount()
        kept = sum(marker.keep for marker in self._scene_markers)
        if total:
            self.lbl_scene_result.setText(
                f"{kept} of {total} marker(s) retained for review; "
                "no cuts are changed automatically"
            )
        self.btn_jump_scene.setEnabled(bool(self._scene_markers))

    def _populate_scene_markers(self, markers):
        duration = float((self._info or {}).get("duration") or 0)
        normalized = normalize_scene_markers(markers, duration=duration)
        self.tbl_scene_markers.blockSignals(True)
        self.tbl_scene_markers.setRowCount(0)
        for marker in normalized:
            row = self.tbl_scene_markers.rowCount()
            self.tbl_scene_markers.insertRow(row)
            keep_item = QTableWidgetItem()
            keep_item.setFlags(keep_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            keep_item.setCheckState(
                Qt.CheckState.Checked if marker.keep else Qt.CheckState.Unchecked
            )
            self.tbl_scene_markers.setItem(row, 0, keep_item)
            self.tbl_scene_markers.setItem(
                row,
                1,
                QTableWidgetItem(f"{marker.time:.3f}"),
            )
        self.tbl_scene_markers.blockSignals(False)
        self._scene_markers = normalized
        self._sync_scene_markers()

    def _on_scene_log(self, text):
        self._scene_log.append(str(text))
        self.console.append(text)

    def _do_detect_scenes(self):
        if not self._filepath or not FFMPEG or not self._info:
            return
        if self._scene_worker and self._scene_worker.isRunning():
            return
        threshold = self.spn_scene_threshold.value()
        duration = float(self._info.get("duration") or 0)
        if duration <= 0:
            self.requestToast.emit("Cannot detect scenes: unknown duration", C["red"])
            return
        command = [
            FFMPEG,
            "-hide_banner",
            "-i",
            self._filepath,
            "-vf",
            f"select='gt(scene,{threshold:.3f})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ]
        self._scene_log = []
        self._scene_worker = FFmpegWorker(
            command,
            duration,
            parse_progress=False,
            parent=self,
        )
        self._scene_worker.log_output.connect(self._on_scene_log)
        self._scene_worker.finished_signal.connect(self._on_scenes_detected)
        self.btn_detect_scenes.setEnabled(False)
        self.btn_cancel_scenes.setEnabled(True)
        self.lbl_scene_result.setText("Scanning scene changes…")
        self._scene_worker.start()

    def _cancel_scene_detection(self):
        if self._scene_worker and self._scene_worker.isRunning():
            self._scene_worker.cancel()
            self.btn_cancel_scenes.setEnabled(False)
            self.lbl_scene_result.setText("Cancelling scene scan…")

    def _on_scenes_detected(self, ok, message):
        self.btn_detect_scenes.setEnabled(bool(FFMPEG) and bool(self._filepath))
        self.btn_cancel_scenes.setEnabled(False)
        if not ok:
            self.lbl_scene_result.setText("Scene scan failed")
            self.requestToast.emit(f"Scene detection failed: {message}", C["red"])
            return
        duration = float((self._info or {}).get("duration") or 0)
        markers = parse_scene_markers(
            "".join(self._scene_log),
            duration=duration,
        )
        self._populate_scene_markers(markers)
        if markers:
            self.requestToast.emit(
                f"Detected {len(markers)} scene marker(s); review before editing",
                C["green"],
            )
        else:
            self.lbl_scene_result.setText("No scene changes crossed the threshold")

    def _jump_to_selected_scene(self):
        row = self.tbl_scene_markers.currentRow()
        if row >= 0:
            self._jump_to_scene_marker(row)

    def _jump_to_scene_marker(self, row):
        if not 0 <= row < len(self._scene_markers):
            return
        if self._player and hasattr(self._player, "seek_seconds"):
            self._player.seek_seconds(self._scene_markers[row].time)

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
                if s.get("rotation"):
                    detail += f", rotation {s['rotation']:g}°"
            elif codec_type == "audio":
                detail += f" - {s.get('channels', '?')}ch, {s.get('sample_rate', '?')} Hz"
                detail += f", {s.get('channel_layout', '')}"
            elif codec_type == "subtitle":
                lang = s.get("language", "")
                title = s.get("title", "")
                detail += f" - {lang} {title}"
            dispositions = [
                name for name, enabled in s.get("disposition", {}).items() if enabled
            ]
            if dispositions:
                detail += f" [{', '.join(dispositions)}]"
            if s.get("time_base"):
                detail += f" · time base {s['time_base']}"
            lines.append(detail)
            chk = QCheckBox(detail)
            chk.setChecked(True)
            chk.setObjectName("streamItem")
            chk.setProperty("streamIndex", idx)
            self._stream_layout.addWidget(chk)
            self._stream_checks.append(chk)

        chapters = self._info.get("chapters", [])
        if chapters:
            lines.append("")
            lines.append(f"Chapters: {len(chapters)}")
            for chapter in chapters:
                title = chapter.get("tags", {}).get("title", "Untitled")
                lines.append(
                    f"  {format_duration(chapter.get('start_time', 0))} — {title}"
                )

        # Tags
        tags = self._info.get("tags", {})
        if tags:
            lines.append("")
            lines.append("Metadata:")
            for k, v in tags.items():
                lines.append(f"  {k}: {v}")

        self.txt_media_info.setText("\n".join(lines))

    def _browse_quality_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Encoded File to Compare", "",
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi);;All Files (*)")
        if not path:
            return
        if self._filepath and self._same_path(path, self._filepath):
            self.requestToast.emit(
                "Choose an encoded file different from the open reference", C["red"]
            )
            return
        if self._quality_probe_worker and self._quality_probe_worker.isRunning():
            self._quality_probe_worker.cancel()
        self.lbl_quality_file.setText(f"Inspecting {Path(path).name}…")
        self.btn_browse_quality.setEnabled(False)
        worker = MediaProbeWorker(path, self)
        self._quality_probe_worker = worker
        worker.finished_signal.connect(self._on_quality_file_probed)
        worker.start()

    def _on_quality_file_probed(self, path, result):
        self._quality_probe_worker = None
        self.btn_browse_quality.setEnabled(True)
        info = result.info
        if not info or not info.get("width") or not info.get("height"):
            self.lbl_quality_file.setText("No comparison file selected")
            self.requestToast.emit("Encoded file could not be probed", C["red"])
            return
        self._quality_path = path
        self._quality_info = info
        self._quality_report = None
        self.btn_export_quality.setEnabled(False)
        self.lbl_quality_file.setText(Path(path).name)
        self.lbl_quality_file.setToolTip(path)
        reference_width = int((self._info or {}).get("width") or 0)
        reference_height = int((self._info or {}).get("height") or 0)
        reference_duration = float((self._info or {}).get("duration") or 0)
        dimensions = f"{info['width']}x{info['height']}"
        if reference_width and reference_height:
            dimensions += f" → {reference_width}x{reference_height}"
        duration = min(
            value for value in (reference_duration, float(info.get("duration") or 0))
            if value > 0
        ) if reference_duration > 0 or float(info.get("duration") or 0) > 0 else 0
        self.lbl_quality_preflight.setText(
            f"{dimensions}; compare up to {format_duration(duration)}"
        )
        self.btn_compare_quality.setEnabled(bool(FFMPEG) and bool(self._filepath))

    @staticmethod
    def _format_metric(name, data, unit="", decimals=2):
        if not data or data.get("status") != "succeeded":
            status = (data or {}).get("status", "unavailable").replace("_", " ").title()
            message = (data or {}).get("message", "No result")
            return f"{name}: {status} — {message}"
        value = data["value"]
        if value == float("inf"):
            text = "inf"
        else:
            text = f"{value:.{decimals}f}"
        return f"{name}: {text}{unit}"

    @staticmethod
    def _same_path(first, second):
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(
            os.path.realpath(second)
        )

    def _do_quality_compare(self):
        if not self._filepath or not self._quality_path or not FFMPEG:
            return
        if not os.path.exists(self._quality_path):
            self.requestToast.emit("Comparison file not found", C["red"])
            return
        if self._same_path(self._filepath, self._quality_path):
            self.requestToast.emit(
                "Reference and encoded files must be different", C["red"]
            )
            return
        reference_info = self._info
        encoded_info = self._quality_info
        if not reference_info or not encoded_info:
            self.requestToast.emit(
                "Both files must be readable videos before comparison", C["red"]
            )
            return
        self._info = reference_info
        self._quality_info = encoded_info
        self._quality_report = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_compare_quality.setEnabled(False)
        self.btn_browse_quality.setEnabled(False)
        self.btn_cancel_quality.setEnabled(True)
        self.btn_export_quality.setEnabled(False)
        self.txt_quality_results.setText("Running quality comparison...")
        self.console.append("[Quality] Comparing encoded file against open reference...\n")
        self._quality_worker = QualityMetricsWorker(
            self._filepath,
            self._quality_path,
            reference_info,
            encoded_info,
            self.spn_quality_offset.value(),
        )
        self._quality_worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._quality_worker.log_output.connect(self.console.append)
        self._quality_worker.finished_signal.connect(self._on_quality_done)
        self._quality_worker.start()

    def _cancel_quality_compare(self):
        if self._quality_worker and self._quality_worker.isRunning():
            self.btn_cancel_quality.setEnabled(False)
            self.txt_quality_results.setText("Cancelling quality comparison...")
            self._quality_worker.cancel()

    def _on_quality_done(self, ok, msg, results):
        self.progress.setRange(0, 100)
        self.btn_compare_quality.setEnabled(bool(FFMPEG) and bool(self._quality_path))
        self.btn_browse_quality.setEnabled(True)
        self.btn_cancel_quality.setEnabled(False)
        self._quality_report = results if results else None
        self.btn_export_quality.setEnabled(bool(results))
        metrics = results.get("metrics", {})
        lines = [
            f"Status: {results.get('status', 'failed').title()}",
            f"Reference: {Path(results.get('reference', self._filepath or '')).name}",
            f"Encoded: {Path(results.get('encoded', self._quality_path or '')).name}",
            f"Offset: {results.get('sync_offset_seconds', 0):.3f} seconds",
            "",
            self._format_metric("VMAF", metrics.get("vmaf")),
            self._format_metric("PSNR", metrics.get("psnr"), " dB"),
            self._format_metric("SSIM", metrics.get("ssim"), "", 6),
        ]
        self.txt_quality_results.setText("\n".join(lines))
        if ok:
            self.progress.setValue(100)
            self.requestToast.emit(msg, C["green"])
        elif results.get("status") == "cancelled":
            self.requestToast.emit("Quality comparison cancelled", C["yellow"])
        else:
            self.requestToast.emit(f"Quality comparison failed: {msg}", C["red"])

    def _export_quality_report(self):
        if not self._quality_report:
            return
        source = Path(self._quality_path or "quality")
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Quality Report",
            str(source.parent / f"{source.stem}_quality.json"),
            "JSON Report (*.json);;CSV Report (*.csv)",
        )
        if not path:
            return
        if "CSV" in selected_filter and not path.lower().endswith(".csv"):
            path += ".csv"
        elif "JSON" in selected_filter and not path.lower().endswith(".json"):
            path += ".json"
        if not _confirm_overwrite(self, path):
            return
        try:
            self._write_quality_report(path, self._quality_report)
        except (OSError, TypeError, ValueError) as exc:
            self.requestToast.emit(f"Report export failed: {exc}", C["red"])
            return
        self.requestToast.emit(f"Quality report saved: {Path(path).name}", C["green"])

    @staticmethod
    def _write_quality_report(path, report):
        final_path = Path(path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            suffix=final_path.suffix,
            prefix=f".{final_path.stem}.clipforge-",
            dir=final_path.parent,
            delete=False,
        )
        staged_path = Path(handle.name)
        try:
            with handle:
                if final_path.suffix.lower() == ".csv":
                    writer = csv.writer(handle)
                    writer.writerow(("field", "value", "status", "message"))
                    for field in (
                        "schema_version",
                        "generated_at",
                        "status",
                        "reference",
                        "encoded",
                        "sync_offset_seconds",
                        "comparison_duration_seconds",
                        "ffmpeg_version",
                    ):
                        writer.writerow((field, report.get(field, ""), "", ""))
                    for name, metric in report.get("metrics", {}).items():
                        writer.writerow(
                            (
                                name,
                                metric.get("value", ""),
                                metric.get("status", ""),
                                metric.get("message", ""),
                            )
                        )
                else:
                    json.dump(report, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
            os.replace(staged_path, final_path)
        except Exception:
            staged_path.unlink(missing_ok=True)
            raise

    def _do_remux(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov", "WebM": ".webm"}
        container = self.cmb_remux_container.currentText()
        selected_indexes = self._selected_stream_indexes()
        selected_streams = [
            stream
            for stream in (self._info or {}).get("streams", [])
            if int(stream.get("index", -1)) in selected_indexes
        ]
        issues = stream_copy_issues(container, selected_streams)
        if issues:
            explanation = "\n".join(f"• {issue}" for issue in issues)
            self.console.append(f"[Remux preflight]\n{explanation}\n")
            self.requestToast.emit(issues[0], C["red"])
            return
        ext = ext_map.get(container, ".mkv")
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Remuxed Video", str(src.parent / f"{src.stem}_remux{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
        cmd = [FFMPEG, "-y", "-i", self._filepath]
        # Map by the actual ffprobe stream index; indexes need not be contiguous.
        for stream_index in sorted(selected_indexes):
            cmd += ["-map", f"0:{stream_index}"]
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

    def _selected_stream_indexes(self):
        return {
            int(chk.property("streamIndex"))
            for chk in self._stream_checks
            if chk.isChecked()
        }

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
            lines.append(f"title={escape_ffmetadata_value(title)}")
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
