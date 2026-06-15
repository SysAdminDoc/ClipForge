"""Filters panel -- color correction, processing, subtitles, LUT, normalization, silence."""

import os
import re
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QProgressBar, QFileDialog, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from clipforge_utils import format_duration_short, format_size

from ..constants import C
from ..tools import (
    FFMPEG, extract_frame,
    _confirm_overwrite, _register_temp_dir,
)
from ..workers import FFmpegWorker


class FiltersPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._filepath = None
        self._info = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Color correction
        color_grp = QGroupBox("Color Correction")
        cl = QGridLayout(color_grp)
        self._sliders = {}
        for row, (name, mn, mx, default) in enumerate([
            ("Brightness", -100, 100, 0),
            ("Contrast", -100, 100, 0),
            ("Saturation", 0, 300, 100),
            ("Hue", -180, 180, 0),
            ("Gamma", 10, 400, 100),
        ]):
            cl.addWidget(QLabel(name), row, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(mn, mx)
            slider.setValue(default)
            cl.addWidget(slider, row, 1)
            val_label = QLabel(str(default))
            val_label.setFixedWidth(40)
            val_label.setProperty("class", "dimLabel")
            slider.valueChanged.connect(lambda v, lbl=val_label: lbl.setText(str(v)))
            cl.addWidget(val_label, row, 2)
            self._sliders[name.lower()] = slider
        btn_reset = QPushButton("Reset All")
        btn_reset.clicked.connect(self._reset_sliders)
        cl.addWidget(btn_reset, len(self._sliders), 2)
        layout.addWidget(color_grp)

        # Processing filters
        proc_grp = QGroupBox("Processing")
        pl = QVBoxLayout(proc_grp)

        row1 = QHBoxLayout()
        self.chk_stabilize = QCheckBox("Video Stabilization (vidstab)")
        self.chk_denoise = QCheckBox("Noise Reduction (nlmeans)")
        self.chk_sharpen = QCheckBox("Sharpen (unsharp)")
        self.chk_deinterlace = QCheckBox("Deinterlace (yadif)")
        row1.addWidget(self.chk_stabilize)
        row1.addWidget(self.chk_denoise)
        pl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self.chk_sharpen)
        row2.addWidget(self.chk_deinterlace)
        pl.addLayout(row2)
        layout.addWidget(proc_grp)

        # Subtitle burn-in
        sub_grp = QGroupBox("Subtitle Burn-in")
        sl = QHBoxLayout(sub_grp)
        self.lbl_sub_file = QLabel("No subtitle selected")
        self.lbl_sub_file.setProperty("class", "dimLabel")
        self.btn_browse_sub = QPushButton("Browse .srt/.ass")
        self.btn_browse_sub.clicked.connect(self._browse_sub)
        sl.addWidget(self.lbl_sub_file, 1)
        sl.addWidget(self.btn_browse_sub)
        layout.addWidget(sub_grp)
        self._sub_path = None

        caption_grp = QGroupBox("Auto-Caption (Whisper)")
        cap_layout = QVBoxLayout(caption_grp)
        cap_row = QHBoxLayout()
        self.cmb_whisper_model = QComboBox()
        self.cmb_whisper_model.addItems([
            "tiny (~75 MB)", "base (~150 MB)", "small (~500 MB)",
            "medium (~1.5 GB)", "large (~3 GB)",
        ])
        self.cmb_whisper_model.setCurrentIndex(1)
        cap_row.addWidget(QLabel("Model:"))
        cap_row.addWidget(self.cmb_whisper_model)
        self.cmb_whisper_lang = QComboBox()
        self.cmb_whisper_lang.addItems(["auto", "en", "es", "fr", "de", "ja", "ko", "zh", "pt", "it", "ru"])
        cap_row.addWidget(QLabel("Language:"))
        cap_row.addWidget(self.cmb_whisper_lang)
        cap_row.addStretch()
        cap_layout.addLayout(cap_row)
        cap_btn_row = QHBoxLayout()
        self.btn_gen_srt = QPushButton("Generate .srt")
        self.btn_gen_srt.setObjectName("primaryBtn")
        self.btn_gen_srt.setEnabled(False)
        self.btn_gen_srt.clicked.connect(self._do_gen_captions)
        self.lbl_whisper_status = QLabel("")
        self.lbl_whisper_status.setProperty("class", "dimLabel")
        cap_btn_row.addWidget(self.lbl_whisper_status, 1)
        cap_btn_row.addWidget(self.btn_gen_srt)
        cap_layout.addLayout(cap_btn_row)
        layout.addWidget(caption_grp)
        self._whisper_path = shutil.which("whisper")
        if self._whisper_path:
            self.lbl_whisper_status.setText("Whisper: Found")
            self.lbl_whisper_status.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_whisper_status.setText("Whisper: Not found (pip install openai-whisper)")
            self.lbl_whisper_status.setStyleSheet(f"color: {C['yellow']};")

        # LUT
        lut_grp = QGroupBox("LUT Color Grading")
        ll = QHBoxLayout(lut_grp)
        self.lbl_lut_file = QLabel("No LUT selected")
        self.lbl_lut_file.setProperty("class", "dimLabel")
        self.btn_browse_lut = QPushButton("Browse .cube")
        self.btn_browse_lut.clicked.connect(self._browse_lut)
        ll.addWidget(self.lbl_lut_file, 1)
        ll.addWidget(self.btn_browse_lut)
        layout.addWidget(lut_grp)
        self._lut_path = None

        audio_grp = QGroupBox("Audio Normalization")
        al = QHBoxLayout(audio_grp)
        self.chk_normalize = QCheckBox("Loudness normalize")
        al.addWidget(self.chk_normalize)
        al.addWidget(QLabel("Target:"))
        self.cmb_loudness_target = QComboBox()
        self.cmb_loudness_target.addItems([
            "YouTube / Streaming (-14 LUFS)",
            "Podcast (-16 LUFS)",
            "Broadcast (-23 LUFS)",
            "Spotify (-14 LUFS)",
            "Apple Music (-16 LUFS)",
        ])
        al.addWidget(self.cmb_loudness_target)
        al.addStretch()
        layout.addWidget(audio_grp)

        silence_grp = QGroupBox("Silence Detection")
        sil_layout = QVBoxLayout(silence_grp)
        sil_opts = QHBoxLayout()
        sil_opts.addWidget(QLabel("Threshold:"))
        self.spn_silence_db = QSpinBox()
        self.spn_silence_db.setRange(-60, -10)
        self.spn_silence_db.setValue(-30)
        self.spn_silence_db.setSuffix(" dB")
        sil_opts.addWidget(self.spn_silence_db)
        sil_opts.addWidget(QLabel("Min duration:"))
        self.spn_silence_dur = QDoubleSpinBox()
        self.spn_silence_dur.setRange(0.1, 10.0)
        self.spn_silence_dur.setValue(0.5)
        self.spn_silence_dur.setSuffix(" s")
        self.spn_silence_dur.setSingleStep(0.1)
        sil_opts.addWidget(self.spn_silence_dur)
        sil_opts.addStretch()
        sil_layout.addLayout(sil_opts)
        self.lbl_silence_result = QLabel("No scan yet")
        self.lbl_silence_result.setProperty("class", "dimLabel")
        sil_layout.addWidget(self.lbl_silence_result)
        sil_btn_row = QHBoxLayout()
        self.btn_detect_silence = QPushButton("Detect Silence")
        self.btn_detect_silence.setEnabled(False)
        self.btn_detect_silence.clicked.connect(self._do_detect_silence)
        self.btn_remove_silence = QPushButton("Remove Silent Segments")
        self.btn_remove_silence.setObjectName("primaryBtn")
        self.btn_remove_silence.setEnabled(False)
        self.btn_remove_silence.clicked.connect(self._do_remove_silence)
        sil_btn_row.addStretch()
        sil_btn_row.addWidget(self.btn_detect_silence)
        sil_btn_row.addWidget(self.btn_remove_silence)
        sil_layout.addLayout(sil_btn_row)
        layout.addWidget(silence_grp)
        self._silence_segments = []

        preview_grp = QGroupBox("Before / After Preview")
        prev_layout = QVBoxLayout(preview_grp)
        self._preview_container = QHBoxLayout()
        self.lbl_preview_before = QLabel("Original")
        self.lbl_preview_before.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview_before.setMinimumHeight(120)
        self.lbl_preview_before.setStyleSheet(f"background: {C['crust']}; border-radius: 4px;")
        self.lbl_preview_after = QLabel("Filtered")
        self.lbl_preview_after.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview_after.setMinimumHeight(120)
        self.lbl_preview_after.setStyleSheet(f"background: {C['crust']}; border-radius: 4px;")
        self._preview_container.addWidget(self.lbl_preview_before)
        self._preview_container.addWidget(self.lbl_preview_after)
        prev_layout.addLayout(self._preview_container)
        self.btn_preview = QPushButton("Generate Preview")
        self.btn_preview.setEnabled(False)
        self.btn_preview.clicked.connect(self._do_preview)
        prev_layout.addWidget(self.btn_preview, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(preview_grp)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)

        btn_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply Filters")
        self.btn_apply.setObjectName("primaryBtn")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._do_apply)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_apply)
        layout.addLayout(btn_row)
        layout.addStretch()

    def _reset_sliders(self):
        defaults = {"brightness": 0, "contrast": 0, "saturation": 100, "hue": 0, "gamma": 100}
        for name, slider in self._sliders.items():
            slider.setValue(defaults.get(name, 0))

    def _browse_sub(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Subtitle File", "",
            "Subtitle Files (*.srt *.ass *.ssa *.vtt);;All Files (*)")
        if path:
            self._sub_path = path
            self.lbl_sub_file.setText(Path(path).name)

    def _browse_lut(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select LUT File", "",
            "LUT Files (*.cube *.3dl);;All Files (*)")
        if path:
            self._lut_path = path
            self.lbl_lut_file.setText(Path(path).name)

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_apply.setEnabled(bool(FFMPEG))
        self.btn_preview.setEnabled(bool(FFMPEG))
        self.btn_gen_srt.setEnabled(bool(self._whisper_path))
        self.btn_detect_silence.setEnabled(bool(FFMPEG))
        self._silence_segments = []
        self.btn_remove_silence.setEnabled(False)
        self.lbl_silence_result.setText("No scan yet")
        pix = extract_frame(filepath, 0)
        if pix:
            scaled = pix.scaledToHeight(120, Qt.TransformationMode.SmoothTransformation)
            self.lbl_preview_before.setPixmap(scaled)
        else:
            self.lbl_preview_before.setText("Original")

    def _do_preview(self):
        if not self._filepath or not FFMPEG:
            return
        vf, _ = self._build_filters()
        if not vf:
            self.requestToast.emit("No video filters to preview", C["yellow"])
            return
        self.btn_preview.setEnabled(False)
        self.btn_preview.setText("Generating...")
        self._preview_tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        self._preview_tmp.close()
        cmd = [FFMPEG, "-y", "-i", self._filepath, "-vf", ",".join(vf),
               "-frames:v", "1", "-q:v", "2", self._preview_tmp.name]
        self._preview_worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._preview_worker.finished_signal.connect(self._on_preview_done)
        self._preview_worker.start()

    def _on_preview_done(self, ok, msg):
        self.btn_preview.setEnabled(True)
        self.btn_preview.setText("Generate Preview")
        tmp_path = self._preview_tmp.name if hasattr(self, '_preview_tmp') else None
        if tmp_path and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            pix = QPixmap(tmp_path)
            scaled = pix.scaledToHeight(120, Qt.TransformationMode.SmoothTransformation)
            self.lbl_preview_after.setPixmap(scaled)
        else:
            self.lbl_preview_after.setText("Preview failed")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _do_detect_silence(self):
        if not self._filepath or not FFMPEG:
            return
        self.btn_detect_silence.setEnabled(False)
        self.lbl_silence_result.setText("Scanning...")
        threshold = self.spn_silence_db.value()
        min_dur = self.spn_silence_dur.value()
        cmd = [FFMPEG, "-i", self._filepath,
               "-af", f"silencedetect=noise={threshold}dB:d={min_dur}",
               "-f", "null", "-"]
        self._silence_worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._silence_worker.log_output.connect(self.console.append)
        self._silence_worker.finished_signal.connect(self._on_silence_detected)
        self._silence_worker.start()

    def _on_silence_detected(self, ok, msg):
        self.btn_detect_silence.setEnabled(True)
        if not ok:
            self.lbl_silence_result.setText("Detection failed")
            self.requestToast.emit(f"Silence detection failed: {msg}", C["red"])
            return
        stderr_text = "".join(self._silence_worker._stderr_buffer)
        self._silence_segments = []
        starts = re.findall(r"silence_start:\s*([\d.]+)", stderr_text)
        ends = re.findall(r"silence_end:\s*([\d.]+)", stderr_text)
        for i, s_str in enumerate(starts):
            s = float(s_str)
            e = float(ends[i]) if i < len(ends) else (self._info.get("duration", 0) if self._info else 0)
            self._silence_segments.append((s, e))
        count = len(self._silence_segments)
        if count == 0:
            self.lbl_silence_result.setText("No silent segments found")
            self.btn_remove_silence.setEnabled(False)
        else:
            total_dur = sum(e - s for s, e in self._silence_segments)
            self.lbl_silence_result.setText(
                f"Found {count} silent segment(s) totaling {format_duration_short(total_dur)}")
            self.btn_remove_silence.setEnabled(True)

    def _do_remove_silence(self):
        if not self._filepath or not self._silence_segments or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Without Silence", str(src.parent / f"{src.stem}_no_silence{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path):
            return
        duration = self._info.get("duration", 0) if self._info else 0
        keep_segments = []
        prev_end = 0.0
        for s, e in sorted(self._silence_segments):
            if s > prev_end:
                keep_segments.append((prev_end, s))
            prev_end = e
        if prev_end < duration:
            keep_segments.append((prev_end, duration))
        if not keep_segments:
            self.requestToast.emit("No content left after removing silence", C["yellow"])
            return
        select_parts = "+".join(f"between(t,{s},{e})" for s, e in keep_segments)
        cmd = [FFMPEG, "-y", "-i", self._filepath,
               "-vf", f"select='{select_parts}',setpts=N/FRAME_RATE/TB",
               "-af", f"aselect='{select_parts}',asetpts=N/SR/TB",
               "-c:v", "libx264", "-crf", "18", "-preset", "fast",
               "-c:a", "aac", "-b:a", "192k", out_path]
        self.progress.setValue(0)
        self.btn_remove_silence.setEnabled(False)
        self._worker = FFmpegWorker(cmd, sum(e - s for s, e in keep_segments))
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_silence_remove_done(ok, msg, out_path))
        self._worker.start()

    def _on_silence_remove_done(self, ok, msg, out_path):
        self.btn_remove_silence.setEnabled(bool(self._silence_segments))
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Silence removed ({size})", C["green"])
        else:
            self.requestToast.emit(f"Silence removal failed: {msg}", C["red"])

    def _do_gen_captions(self):
        if not self._filepath or not self._whisper_path:
            self.requestToast.emit("Whisper not available", C["yellow"])
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Subtitles", str(src.parent / f"{src.stem}.srt"),
            "Subtitle Files (*.srt);;All Files (*)")
        if not out_path:
            return
        model = self.cmb_whisper_model.currentText().split(" (")[0]
        lang = self.cmb_whisper_lang.currentText()
        out_dir = str(Path(out_path).parent)
        cmd = [self._whisper_path, self._filepath,
               "--model", model, "--output_format", "srt",
               "--output_dir", out_dir]
        if lang != "auto":
            cmd += ["--language", lang]
        self.console.append(f"[Auto-Caption] Generating subtitles with Whisper ({model})...\n")
        self.btn_gen_srt.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setRange(0, 0)
        self._worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_caption_done(ok, msg, out_path))
        self._worker.start()

    def _on_caption_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_gen_srt.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            out_dir = Path(out_path).parent
            input_stem = Path(self._filepath).stem
            whisper_generated = out_dir / f"{input_stem}.srt"
            actual_path = out_path
            if whisper_generated.exists() and str(whisper_generated) != out_path:
                try:
                    shutil.move(str(whisper_generated), out_path)
                except OSError:
                    actual_path = str(whisper_generated)
            elif not Path(out_path).exists() and whisper_generated.exists():
                actual_path = str(whisper_generated)
            self.requestToast.emit("Subtitles generated", C["green"])
            self._sub_path = actual_path
            self.lbl_sub_file.setText(Path(actual_path).name)
        else:
            self.requestToast.emit(f"Caption generation failed: {msg}", C["red"])

    def _build_filters(self):
        vf = []
        af = []
        b = self._sliders["brightness"].value()
        c = self._sliders["contrast"].value()
        s = self._sliders["saturation"].value()
        h = self._sliders["hue"].value()
        g = self._sliders["gamma"].value()
        eq_parts = []
        if b != 0:
            eq_parts.append(f"brightness={b/100:.2f}")
        if c != 0:
            eq_parts.append(f"contrast={1 + c/100:.2f}")
        if s != 100:
            eq_parts.append(f"saturation={s/100:.2f}")
        if g != 100:
            eq_parts.append(f"gamma={g/100:.2f}")
        if eq_parts:
            vf.append(f"eq={':'.join(eq_parts)}")
        if h != 0:
            vf.append(f"hue=h={h}")
        if self.chk_deinterlace.isChecked():
            vf.append("yadif")
        if self.chk_denoise.isChecked():
            vf.append("nlmeans")
        if self.chk_sharpen.isChecked():
            vf.append("unsharp=5:5:1.0")
        if self._lut_path:
            escaped = self._lut_path.replace("\\", "/").replace(":", "\\\\:")
            vf.append(f"lut3d='{escaped}'")
        if self._sub_path:
            escaped = self._sub_path.replace("\\", "/").replace(":", "\\\\:")
            vf.append(f"subtitles='{escaped}'")
        if self.chk_normalize.isChecked():
            target_text = self.cmb_loudness_target.currentText()
            lufs_map = {
                "YouTube / Streaming (-14 LUFS)": -14,
                "Podcast (-16 LUFS)": -16,
                "Broadcast (-23 LUFS)": -23,
                "Spotify (-14 LUFS)": -14,
                "Apple Music (-16 LUFS)": -16,
            }
            lufs = lufs_map.get(target_text, -14)
            af.append(f"loudnorm=I={lufs}:TP=-1:LRA=11")
        return vf, af

    def _do_apply(self):
        if not self._filepath or not FFMPEG:
            return
        vf, af = self._build_filters()
        if not vf and not af and not self.chk_stabilize.isChecked():
            self.requestToast.emit("No filters selected", C["yellow"])
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Filtered Video", str(src.parent / f"{src.stem}_filtered{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path):
            return

        duration = self._info.get("duration", 0) if self._info else 0

        self.progress.setValue(0)
        self.btn_apply.setEnabled(False)

        if self.chk_stabilize.isChecked():
            self._stab_tmpdir = tempfile.mkdtemp(prefix="clipforge_stab_")
            _register_temp_dir(self._stab_tmpdir)
            transforms = os.path.join(self._stab_tmpdir, "transforms.trf")
            cmd1 = [FFMPEG, "-y", "-i", self._filepath,
                    "-vf", f"vidstabdetect=result='{transforms}'",
                    "-f", "null", "-"]
            self.console.append("[Stabilization] Pass 1: Analyzing motion...\n")
            self._stab_pass1 = FFmpegWorker(cmd1, duration)
            self._stab_pass1.progress.connect(lambda v: self.progress.setValue(int(v * 0.4)))
            self._stab_pass1.log_output.connect(self.console.append)
            self._stab_pass1.finished_signal.connect(
                lambda ok, msg: self._on_stab_pass1_done(ok, msg, vf, af, duration, out_path, transforms))
            self._stab_pass1.start()
        else:
            self._run_filter_encode(vf, af, duration, out_path)

    def _on_stab_pass1_done(self, ok, msg, vf, af, duration, out_path, transforms):
        if not ok:
            self.btn_apply.setEnabled(True)
            self.requestToast.emit(f"Stabilization analysis failed: {msg}", C["red"])
            return
        stab_filter = f"vidstabtransform=input='{transforms}':smoothing=10"
        vf.insert(0, stab_filter)
        self._run_filter_encode(vf, af, duration, out_path)

    def _run_filter_encode(self, vf, af, duration, out_path):
        cmd = [FFMPEG, "-y", "-i", self._filepath]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]
        if not af:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd.append(out_path)

        self._worker = FFmpegWorker(cmd, duration)
        self._worker.progress.connect(lambda v: self.progress.setValue(40 + int(v * 0.6)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _on_done(self, ok, msg, out_path):
        self.btn_apply.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Filters applied ({size})", C["green"])
        else:
            self.requestToast.emit(f"Filter failed: {msg}", C["red"])
