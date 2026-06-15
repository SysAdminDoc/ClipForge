"""Trim panel -- cut segments from video."""

import os
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_duration, format_size

from ..constants import C
from ..tools import FFMPEG, FFPROBE, _confirm_overwrite, _register_temp_dir, _unregister_temp_dir
from ..workers import FFmpegWorker
from ..widgets import RangeSlider


class TrimPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, player, parent=None):
        super().__init__(parent)
        self.console = console
        self.player = player
        self._filepath = None
        self._info = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grp = QGroupBox("Trim Range")
        gl = QVBoxLayout(grp)
        self.range_slider = RangeSlider()
        self.range_slider.rangeChanged.connect(self._on_range_changed)
        gl.addWidget(self.range_slider)

        times_row = QHBoxLayout()
        self.lbl_start = QLabel("Start: 00:00:00.000")
        self.lbl_end = QLabel("End: 00:00:00.000")
        self.lbl_duration = QLabel("Duration: 00:00:00.000")
        self.lbl_duration.setProperty("class", "accentLabel")
        times_row.addWidget(self.lbl_start)
        times_row.addStretch()
        times_row.addWidget(self.lbl_duration)
        times_row.addStretch()
        times_row.addWidget(self.lbl_end)
        gl.addLayout(times_row)

        marker_row = QHBoxLayout()
        self.btn_set_in = QPushButton("Set In (current)")
        self.btn_set_in.clicked.connect(self._set_in_from_player)
        self.btn_set_out = QPushButton("Set Out (current)")
        self.btn_set_out.clicked.connect(self._set_out_from_player)
        marker_row.addWidget(self.btn_set_in)
        marker_row.addWidget(self.btn_set_out)
        marker_row.addStretch()
        gl.addLayout(marker_row)
        layout.addWidget(grp)

        opts = QGroupBox("Cut Mode")
        ol = QVBoxLayout(opts)
        mode_row = QHBoxLayout()
        self.chk_lossless = QCheckBox("Lossless (keyframe-aligned, fastest)")
        self.chk_lossless.setChecked(True)
        self.chk_lossless.toggled.connect(self._on_mode_changed)
        self.chk_smart = QCheckBox("Smart Cut (re-encode edges only)")
        self.chk_smart.toggled.connect(self._on_mode_changed)
        self.chk_reencode = QCheckBox("Full re-encode (frame-accurate)")
        self.chk_reencode.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.chk_lossless)
        mode_row.addWidget(self.chk_smart)
        mode_row.addWidget(self.chk_reencode)
        ol.addLayout(mode_row)
        fmt_row = QHBoxLayout()
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["Same as source", "MP4", "MKV", "MOV", "WebM"])
        fmt_row.addWidget(QLabel("Format:"))
        fmt_row.addWidget(self.cmb_format)
        fmt_row.addStretch()
        ol.addLayout(fmt_row)
        layout.addWidget(opts)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = QHBoxLayout()
        self.btn_trim = QPushButton("Trim Video")
        self.btn_trim.setObjectName("primaryBtn")
        self.btn_trim.setEnabled(False)
        self.btn_trim.clicked.connect(self._do_trim)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_trim)
        layout.addLayout(btn_row)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_trim.setEnabled(bool(FFMPEG))
        duration = info.get("duration", 0) if info else 0
        self.range_slider._max = duration
        self.range_slider.set_range(0, duration)

    def _on_range_changed(self, low, high):
        self.lbl_start.setText(f"Start: {format_duration(low)}")
        self.lbl_end.setText(f"End: {format_duration(high)}")
        self.lbl_duration.setText(f"Duration: {format_duration(high - low)}")

    def _set_in_from_player(self):
        pos = self.player.get_position_sec()
        self.range_slider.set_range(pos, self.range_slider.high())

    def _set_out_from_player(self):
        pos = self.player.get_position_sec()
        self.range_slider.set_range(self.range_slider.low(), pos)

    def _on_mode_changed(self, checked):
        sender = self.sender()
        if not checked:
            sender.blockSignals(True)
            sender.setChecked(True)
            sender.blockSignals(False)
            return
        for chk in (self.chk_lossless, self.chk_smart, self.chk_reencode):
            if chk is not sender:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)

    def _find_prev_keyframe(self, filepath, time_sec):
        if not FFPROBE:
            return time_sec
        try:
            cmd = [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
                   "-show_entries", "packet=pts_time,flags",
                   "-of", "csv=p=0", "-read_intervals", f"%{time_sec+0.5}",
                   filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            last_kf = 0
            for line in result.stdout.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        pts = float(parts[0])
                    except ValueError:
                        continue
                    if "K" in parts[1] and pts <= time_sec:
                        last_kf = pts
            return last_kf
        except (OSError, subprocess.TimeoutExpired):
            return time_sec

    def _do_trim(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"Same as source": "", "MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov", "WebM": ".webm"}
        fmt = self.cmb_format.currentText()
        src = Path(self._filepath)
        ext = ext_map.get(fmt, "") or src.suffix
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Trimmed Video", str(src.parent / f"{src.stem}_trimmed{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path):
            return
        start = self.range_slider.low()
        end = self.range_slider.high()
        if self.chk_smart.isChecked():
            self._do_smart_cut(start, end, out_path)
            return
        elif self.chk_lossless.isChecked():
            cmd = [FFMPEG, "-y", "-ss", str(start), "-i", self._filepath,
                   "-t", str(end - start), "-c", "copy", "-avoid_negative_ts", "make_zero"]
        else:
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-ss", str(start), "-to", str(end),
                   "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k"]
        cmd.append(out_path)
        self.btn_trim.setEnabled(False)
        if self.chk_lossless.isChecked():
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self._worker = FFmpegWorker(cmd, end - start)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _do_smart_cut(self, start, end, out_path):
        self.btn_trim.setEnabled(False)
        self.progress.setRange(0, 0)
        self.console.append("[Smart Cut] Finding keyframes and preparing segments...\n")
        prev_kf = self._find_prev_keyframe(self._filepath, start)
        tmpdir = tempfile.mkdtemp(prefix="clipforge_smartcut_")
        _register_temp_dir(tmpdir)
        self._smart_tmpdir = tmpdir

        head_seg = os.path.join(tmpdir, "head.mp4")
        mid_seg = os.path.join(tmpdir, "mid.mp4")
        concat_list = os.path.join(tmpdir, "concat.txt")

        steps = []
        use_head = prev_kf < start - 0.05

        if use_head:
            cmd_head = [FFMPEG, "-y", "-i", self._filepath,
                        "-ss", str(prev_kf), "-to", str(start),
                        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "192k", head_seg]
            steps.append(("Re-encoding head segment...", cmd_head))

        cmd_mid = [FFMPEG, "-y", "-ss", str(start), "-i", self._filepath,
                   "-t", str(end - start), "-c", "copy",
                   "-avoid_negative_ts", "make_zero", mid_seg]
        steps.append(("Copying middle (lossless)...", cmd_mid))

        parts = []
        if use_head:
            parts.append(f"file '{head_seg}'")
        parts.append(f"file '{mid_seg}'")
        with open(concat_list, "w") as f:
            f.write("\n".join(parts) + "\n")

        cmd_concat = [FFMPEG, "-y", "-f", "concat", "-safe", "0",
                      "-i", concat_list, "-c", "copy", out_path]
        steps.append(("Joining segments...", cmd_concat))

        self._smart_steps = steps
        self._smart_out_path = out_path
        self._smart_step_idx = 0
        self._run_next_smart_step()

    def _run_next_smart_step(self):
        if self._smart_step_idx >= len(self._smart_steps):
            if hasattr(self, '_smart_tmpdir'):
                shutil.rmtree(self._smart_tmpdir, ignore_errors=True)
                _unregister_temp_dir(self._smart_tmpdir)
            self._on_done(True, "Smart Cut complete", self._smart_out_path)
            return
        label, cmd = self._smart_steps[self._smart_step_idx]
        self.console.append(f"[Smart Cut] {label}\n")
        self._worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_smart_step_done)
        self._worker.start()

    def _on_smart_step_done(self, ok, msg):
        if not ok:
            if hasattr(self, '_smart_tmpdir'):
                shutil.rmtree(self._smart_tmpdir, ignore_errors=True)
                _unregister_temp_dir(self._smart_tmpdir)
            self._on_done(False, f"Smart Cut failed: {msg}", self._smart_out_path)
            return
        self._smart_step_idx += 1
        self._run_next_smart_step()

    def _on_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_trim.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Trim complete  ({size})", C["green"])
        else:
            self.console.append(f"\n[ERROR] {msg}\n")
            self.requestToast.emit(f"Trim failed: {msg}", C["red"])
