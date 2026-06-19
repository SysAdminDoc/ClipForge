"""Convert panel -- codec, format, resolution, speed, presets, cmd preview."""

import sys
import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTextEdit, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size, estimate_output_size

from ..constants import C, BUILTIN_PRESETS
from ..settings import load_user_presets, save_user_preset, delete_user_preset, export_presets, import_presets
from ..tools import FFMPEG, HW_ENCODERS, _confirm_overwrite
from ..workers import FFmpegWorker


class ConvertPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._filepath = None
        self._info = None
        self._worker = None
        self._gif_palette = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Preset bar
        preset_grp = QGroupBox("Presets")
        pl = QHBoxLayout(preset_grp)
        pl.addWidget(QLabel("Quick:"))
        self.cmb_preset_select = QComboBox()
        self._refresh_presets()
        self.cmb_preset_select.currentTextChanged.connect(self._load_preset)
        pl.addWidget(self.cmb_preset_select, 1)
        self.btn_save_preset = QPushButton("Save As...")
        self.btn_save_preset.clicked.connect(self._save_current_as_preset)
        pl.addWidget(self.btn_save_preset)
        self.btn_del_preset = QPushButton("Delete")
        self.btn_del_preset.clicked.connect(self._delete_preset)
        pl.addWidget(self.btn_del_preset)
        self.btn_export_preset = QPushButton("Export...")
        self.btn_export_preset.setToolTip("Export presets to a JSON file for sharing")
        self.btn_export_preset.clicked.connect(self._export_presets)
        pl.addWidget(self.btn_export_preset)
        self.btn_import_preset = QPushButton("Import...")
        self.btn_import_preset.setToolTip("Import presets from a JSON file")
        self.btn_import_preset.clicked.connect(self._import_presets)
        pl.addWidget(self.btn_import_preset)
        layout.addWidget(preset_grp)

        fmt_grp = QGroupBox("Output Format")
        fl = QHBoxLayout(fmt_grp)
        fl.addWidget(QLabel("Container:"))
        self.cmb_container = QComboBox()
        self.cmb_container.addItems(["MP4", "MKV", "WebM", "MOV", "AVI", "GIF"])
        self.cmb_container.currentTextChanged.connect(self._on_container_changed)
        fl.addWidget(self.cmb_container)
        fl.addWidget(QLabel("Video Codec:"))
        self.cmb_vcodec = QComboBox()
        self._populate_vcodecs()
        fl.addWidget(self.cmb_vcodec)
        fl.addWidget(QLabel("Audio Codec:"))
        self.cmb_acodec = QComboBox()
        self.cmb_acodec.addItems(["AAC", "Opus", "MP3", "FLAC", "Copy (no re-encode)", "None (remove audio)"])
        fl.addWidget(self.cmb_acodec)
        layout.addWidget(fmt_grp)

        q_grp = QGroupBox("Quality")
        ql = QHBoxLayout(q_grp)
        ql.addWidget(QLabel("Preset:"))
        self.cmb_enc_preset = QComboBox()
        self.cmb_enc_preset.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast",
                                       "medium", "slow", "slower", "veryslow"])
        self.cmb_enc_preset.setCurrentIndex(5)
        ql.addWidget(self.cmb_enc_preset)
        ql.addWidget(QLabel("CRF:"))
        self.spn_crf = QSpinBox()
        self.spn_crf.setRange(0, 51)
        self.spn_crf.setValue(18)
        self.spn_crf.setToolTip("0=lossless, 18=visually lossless, 23=default, 51=worst")
        self.spn_crf.valueChanged.connect(self._update_estimate)
        ql.addWidget(self.spn_crf)
        self.lbl_quality_hint = QLabel("Visually lossless")
        self.lbl_quality_hint.setProperty("class", "dimLabel")
        ql.addWidget(self.lbl_quality_hint)
        self.chk_two_pass = QCheckBox("Two-pass")
        self.chk_two_pass.setToolTip("Better quality at target bitrate (slower)")
        ql.addWidget(self.chk_two_pass)
        layout.addWidget(q_grp)

        res_grp = QGroupBox("Resolution & Speed")
        rl = QHBoxLayout(res_grp)
        self.cmb_resolution = QComboBox()
        self.cmb_resolution.addItems(["Original", "3840x2160 (4K)", "2560x1440 (2K)",
                                       "1920x1080 (1080p)", "1280x720 (720p)",
                                       "854x480 (480p)", "640x360 (360p)", "1080x1920"])
        self.cmb_resolution.currentTextChanged.connect(self._update_estimate)
        rl.addWidget(QLabel("Resolution:"))
        rl.addWidget(self.cmb_resolution)
        rl.addWidget(QLabel("FPS:"))
        self.cmb_fps = QComboBox()
        self.cmb_fps.addItems(["Original", "60", "30", "24", "15"])
        rl.addWidget(self.cmb_fps)
        rl.addWidget(QLabel("Speed:"))
        self.spn_speed = QDoubleSpinBox()
        self.spn_speed.setRange(0.1, 10.0)
        self.spn_speed.setValue(1.0)
        self.spn_speed.setSingleStep(0.25)
        self.spn_speed.setSuffix("x")
        rl.addWidget(self.spn_speed)
        rl.addStretch()
        layout.addWidget(res_grp)

        # Estimated output size
        self.lbl_estimate = QLabel("")
        self.lbl_estimate.setProperty("class", "dimLabel")
        layout.addWidget(self.lbl_estimate)

        cmd_grp = QGroupBox("FFmpeg Command Preview (editable)")
        cmd_layout = QVBoxLayout(cmd_grp)
        self.txt_cmd_preview = QTextEdit()
        self.txt_cmd_preview.setObjectName("cmdPreview")
        self.txt_cmd_preview.setReadOnly(False)
        self.txt_cmd_preview.setMaximumHeight(60)
        cmd_layout.addWidget(self.txt_cmd_preview)
        cmd_btn_row = QHBoxLayout()
        btn_copy_cmd = QPushButton("Copy")
        btn_copy_cmd.clicked.connect(self._copy_cmd)
        self.btn_reset_cmd = QPushButton("Reset")
        self.btn_reset_cmd.setToolTip("Regenerate command from current settings")
        self.btn_reset_cmd.clicked.connect(self._update_cmd_preview)
        self.btn_run_custom = QPushButton("Run Custom")
        self.btn_run_custom.setObjectName("primaryBtn")
        self.btn_run_custom.setToolTip("Execute the edited command as-is")
        self.btn_run_custom.clicked.connect(self._run_custom_cmd)
        self.btn_run_custom.setEnabled(False)
        cmd_btn_row.addStretch()
        cmd_btn_row.addWidget(btn_copy_cmd)
        cmd_btn_row.addWidget(self.btn_reset_cmd)
        cmd_btn_row.addWidget(self.btn_run_custom)
        cmd_layout.addLayout(cmd_btn_row)
        layout.addWidget(cmd_grp)

        # Connect signals for live preview
        for widget in [self.cmb_container, self.cmb_vcodec, self.cmb_acodec, self.cmb_enc_preset,
                       self.cmb_resolution, self.cmb_fps]:
            widget.currentTextChanged.connect(self._update_cmd_preview)
        self.cmb_vcodec.currentTextChanged.connect(self._on_vcodec_changed)
        self.spn_crf.valueChanged.connect(self._update_cmd_preview)
        self.spn_speed.valueChanged.connect(self._update_cmd_preview)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = QHBoxLayout()
        self.btn_reset_defaults = QPushButton("Reset to Defaults")
        self.btn_reset_defaults.setToolTip("Reset all conversion settings to their defaults")
        self.btn_reset_defaults.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self.btn_reset_defaults)
        btn_row.addStretch()
        self.btn_convert = QPushButton("Convert Video")
        self.btn_convert.setObjectName("primaryBtn")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self._do_convert)
        btn_row.addWidget(self.btn_convert)
        layout.addLayout(btn_row)
        layout.addStretch()

    def _populate_vcodecs(self):
        items = ["H.264 (libx264)", "H.265 (libx265)", "VP9", "AV1 (libaom)",
                 "SVT-AV1 (libsvtav1)", "Copy (no re-encode)"]
        for label in HW_ENCODERS:
            items.insert(-1, label)
        self.cmb_vcodec.addItems(items)

    def _refresh_presets(self):
        self.cmb_preset_select.blockSignals(True)
        self.cmb_preset_select.clear()
        self.cmb_preset_select.addItem("-- Select Preset --")
        for name in sorted(BUILTIN_PRESETS.keys()):
            self.cmb_preset_select.addItem(f"[Built-in] {name}")
        user = load_user_presets()
        for name in sorted(user.keys()):
            self.cmb_preset_select.addItem(f"[Custom] {name}")
        self.cmb_preset_select.blockSignals(False)

    def _load_preset(self, text):
        if text.startswith("-- "):
            return
        if text.startswith("[Built-in] "):
            name = text.replace("[Built-in] ", "")
            data = BUILTIN_PRESETS.get(name, {})
        elif text.startswith("[Custom] "):
            name = text.replace("[Custom] ", "")
            data = load_user_presets().get(name, {})
        else:
            return
        if not data:
            return
        self.cmb_container.setCurrentText(data.get("container", "MP4"))
        self.cmb_vcodec.setCurrentText(data.get("vcodec", "H.264 (libx264)"))
        self.cmb_acodec.setCurrentText(data.get("acodec", "AAC"))
        self.spn_crf.setValue(data.get("crf", 18))
        self.cmb_enc_preset.setCurrentText(data.get("preset", "medium"))
        self.cmb_resolution.setCurrentText(data.get("resolution", "Original"))
        self.cmb_fps.setCurrentText(data.get("fps", "Original"))
        self.spn_speed.setValue(data.get("speed", 1.0))

    def _save_current_as_preset(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        data = {
            "container": self.cmb_container.currentText(),
            "vcodec": self.cmb_vcodec.currentText(),
            "acodec": self.cmb_acodec.currentText(),
            "crf": self.spn_crf.value(),
            "preset": self.cmb_enc_preset.currentText(),
            "resolution": self.cmb_resolution.currentText(),
            "fps": self.cmb_fps.currentText(),
            "speed": self.spn_speed.value(),
        }
        saved_name = save_user_preset(name.strip(), data)
        if saved_name:
            self._refresh_presets()
            self.requestToast.emit(f"Preset '{saved_name}' saved", C["green"])
        else:
            self.requestToast.emit("Failed to save preset", C["red"])

    def _export_presets(self):
        """Export all user presets (or selected) to a JSON file."""
        user = load_user_presets()
        if not user:
            self.requestToast.emit("No custom presets to export", C["yellow"])
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Presets", str(Path.home() / "clipforge_presets.json"),
            "JSON Files (*.json);;All Files (*)")
        if not out_path:
            return
        if export_presets(list(user.keys()), out_path):
            self.requestToast.emit(f"Exported {len(user)} preset(s)", C["green"])
        else:
            self.requestToast.emit("Export failed", C["red"])

    def _import_presets(self):
        """Import presets from a JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Presets", str(Path.home()),
            "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        imported = import_presets(path)
        if imported:
            self._refresh_presets()
            self.requestToast.emit(f"Imported {len(imported)} preset(s)", C["green"])
        else:
            self.requestToast.emit("No valid presets found in file", C["yellow"])

    def _delete_preset(self):
        text = self.cmb_preset_select.currentText()
        if text.startswith("[Custom] "):
            name = text.replace("[Custom] ", "")
            delete_user_preset(name)
            self._refresh_presets()
            self.requestToast.emit(f"Preset '{name}' deleted", C["yellow"])

    def _reset_to_defaults(self):
        """Reset all conversion settings to defaults."""
        self.cmb_container.setCurrentText("MP4")
        self.cmb_vcodec.setCurrentText("H.264 (libx264)")
        self.cmb_acodec.setCurrentText("AAC")
        self.spn_crf.setValue(18)
        self.cmb_enc_preset.setCurrentText("medium")
        self.cmb_resolution.setCurrentText("Original")
        self.cmb_fps.setCurrentText("Original")
        self.spn_speed.setValue(1.0)
        self.chk_two_pass.setChecked(False)
        self.cmb_preset_select.setCurrentIndex(0)
        self.requestToast.emit("Settings reset to defaults", C["blue"])

    def _on_container_changed(self, container):
        if container == "WebM":
            self.cmb_vcodec.setCurrentText("VP9")
            self.cmb_acodec.setCurrentText("Opus")
        elif container == "GIF":
            self.cmb_acodec.setCurrentText("None (remove audio)")

    def _on_vcodec_changed(self, vcodec_text):
        is_av1 = "AV1" in vcodec_text or "svtav1" in vcodec_text.lower()
        max_crf = 63 if is_av1 else 51
        if self.spn_crf.maximum() != max_crf:
            self.spn_crf.setRange(0, max_crf)
        self._update_estimate()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_convert.setEnabled(bool(FFMPEG))
        self.btn_run_custom.setEnabled(bool(FFMPEG))
        self._update_estimate()
        self._update_cmd_preview()

    def _update_estimate(self):
        if not self._info:
            return
        crf = self.spn_crf.value()
        dur = self._info.get("duration", 0)
        w = self._info.get("width", 1920)
        h = self._info.get("height", 1080)
        res = self.cmb_resolution.currentText()
        if res != "Original" and "x" in res:
            try:
                w = int(res.split("x")[0])
                h = int(res.split("x")[1].split(" ")[0])
            except (ValueError, IndexError):
                pass
        est = estimate_output_size(dur, crf, w, h)
        vcodec_text = self.cmb_vcodec.currentText()
        is_av1 = "AV1" in vcodec_text or "svtav1" in vcodec_text.lower()
        if is_av1:
            quality_labels = {range(0, 10): "Lossless / near-lossless",
                              range(10, 20): "Very high quality", range(20, 28): "High quality",
                              range(28, 35): "Good quality (recommended)", range(35, 45): "Medium quality",
                              range(45, 56): "Low quality", range(56, 64): "Very low quality"}
        else:
            quality_labels = {range(0, 5): "Lossless", range(5, 15): "High quality",
                              range(15, 21): "Visually lossless", range(21, 28): "Good quality",
                              range(28, 35): "Medium quality", range(35, 45): "Low quality",
                              range(45, 52): "Very low quality"}
        hint = "Unknown"
        for r, label in quality_labels.items():
            if crf in r:
                hint = label
                break
        self.lbl_quality_hint.setText(hint)
        self.lbl_estimate.setText(f"Estimated output: ~{format_size(est)}")

    def _build_cmd(self, out_path=None):
        """Build the FFmpeg command from current settings."""
        if not self._filepath or not FFMPEG:
            return []
        target = out_path or "<output>"
        cmd = [FFMPEG, "-y", "-i", self._filepath]
        vcodec_map = {
            "H.264 (libx264)": "libx264", "H.265 (libx265)": "libx265",
            "VP9": "libvpx-vp9", "AV1 (libaom)": "libaom-av1",
            "SVT-AV1 (libsvtav1)": "libsvtav1", "Copy (no re-encode)": "copy",
        }
        # Add HW encoder mappings
        for label, enc_name in HW_ENCODERS.items():
            vcodec_map[label] = enc_name

        vcodec_text = self.cmb_vcodec.currentText()
        vcodec = vcodec_map.get(vcodec_text, "libx264")
        container = self.cmb_container.currentText()

        if container == "GIF":
            filters = "fps=15,scale=480:-1:flags=lanczos"
            res = self.cmb_resolution.currentText()
            if res != "Original":
                w = res.split("x")[0]
                filters = f"fps=15,scale={w}:-1:flags=lanczos"
            cmd = [FFMPEG, "-y", "-i", self._filepath,
                   "-vf", f"{filters},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse", target]
        else:
            vf_parts = []
            res = self.cmb_resolution.currentText()
            if res != "Original" and "x" in res:
                w_val = res.split("x")[0]
                vf_parts.append(f"scale={w_val}:-2")
            speed = self.spn_speed.value()
            if speed != 1.0:
                vf_parts.append(f"setpts={1/speed}*PTS")
            if vf_parts:
                cmd += ["-vf", ",".join(vf_parts)]
            if vcodec == "copy" and vf_parts:
                vcodec = "libx264"
            if vcodec != "copy":
                cmd += ["-c:v", vcodec]
                if vcodec in ("libx264", "libx265"):
                    cmd += ["-crf", str(self.spn_crf.value()), "-preset", self.cmb_enc_preset.currentText()]
                elif vcodec == "libsvtav1":
                    cmd += ["-crf", str(self.spn_crf.value())]
                    enc_preset = self.cmb_enc_preset.currentText()
                    if enc_preset.isdigit():
                        cmd += ["-preset", enc_preset]
                    else:
                        svt_map = {"ultrafast": "12", "superfast": "11", "veryfast": "10",
                                   "faster": "9", "fast": "8", "medium": "6",
                                   "slow": "4", "slower": "2", "veryslow": "0"}
                        cmd += ["-preset", svt_map.get(enc_preset, "6")]
                elif vcodec == "libaom-av1":
                    cmd += ["-crf", str(self.spn_crf.value()), "-b:v", "0"]
                    enc_preset = self.cmb_enc_preset.currentText()
                    aom_map = {"ultrafast": "8", "superfast": "7", "veryfast": "6",
                               "faster": "5", "fast": "4", "medium": "4",
                               "slow": "3", "slower": "2", "veryslow": "1"}
                    cmd += ["-cpu-used", aom_map.get(enc_preset, "4")]
                elif "nvenc" in vcodec or "qsv" in vcodec or "amf" in vcodec:
                    cmd += ["-rc", "constqp", "-qp", str(self.spn_crf.value())]
            else:
                cmd += ["-c:v", "copy"]
            acodec_text = self.cmb_acodec.currentText()
            acodec_map = {"AAC": ["aac", "-b:a", "192k"], "Opus": ["libopus", "-b:a", "128k"],
                          "MP3": ["libmp3lame", "-b:a", "192k"], "FLAC": ["flac"],
                          "Copy (no re-encode)": ["copy"]}
            if acodec_text == "None (remove audio)":
                cmd += ["-an"]
            else:
                cmd += ["-c:a"] + acodec_map.get(acodec_text, ["aac", "-b:a", "192k"])
            if speed != 1.0 and acodec_text not in ("None (remove audio)", "Copy (no re-encode)"):
                atempo_val = speed
                atempo_parts = []
                if atempo_val < 0.5:
                    while atempo_val < 0.5:
                        atempo_parts.append("atempo=0.5")
                        atempo_val /= 0.5
                    atempo_parts.append(f"atempo={atempo_val:.4f}")
                elif atempo_val > 2.0:
                    while atempo_val > 2.0:
                        atempo_parts.append("atempo=2.0")
                        atempo_val /= 2.0
                    atempo_parts.append(f"atempo={atempo_val:.4f}")
                else:
                    atempo_parts.append(f"atempo={atempo_val:.4f}")
                cmd += ["-af", ",".join(atempo_parts)]
            fps = self.cmb_fps.currentText()
            if fps != "Original":
                cmd += ["-r", fps]
            if container == "MP4":
                cmd += ["-movflags", "+faststart"]
            cmd.append(target)
        return cmd

    def _update_cmd_preview(self):
        cmd = self._build_cmd()
        if cmd:
            self.txt_cmd_preview.setText(" ".join(cmd))

    def _copy_cmd(self):
        QApplication.clipboard().setText(self.txt_cmd_preview.toPlainText())
        self.requestToast.emit("Command copied to clipboard", C["blue"])

    def _run_custom_cmd(self):
        if not self._filepath or not FFMPEG:
            return
        cmd_text = self.txt_cmd_preview.toPlainText().strip()
        if not cmd_text:
            return
        import shlex
        try:
            if sys.platform == "win32":
                parts = cmd_text.split()
            else:
                parts = shlex.split(cmd_text)
        except ValueError:
            self.requestToast.emit("Invalid command syntax", C["red"])
            return
        if not parts:
            return
        out_path = parts[-1] if not parts[-1].startswith("-") else None
        if out_path and out_path == "<output>":
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Save Output", str(Path(self._filepath).parent / "custom_output.mp4"),
                "All Files (*)")
            if not out_path:
                return
            parts[-1] = out_path
        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setValue(0)
        self.btn_convert.setEnabled(False)
        self.btn_run_custom.setEnabled(False)
        self._worker = FFmpegWorker(parts, duration)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_done(ok, msg, out_path or ""))
        self._worker.start()

    def _do_convert(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"MP4": ".mp4", "MKV": ".mkv", "WebM": ".webm", "MOV": ".mov", "AVI": ".avi", "GIF": ".gif"}
        container = self.cmb_container.currentText()
        ext = ext_map.get(container, ".mp4")
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Converted Video", str(src.parent / f"{src.stem}_converted{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi *.gif);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path):
            return

        cmd = self._build_cmd(out_path)

        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setValue(0)
        self.btn_convert.setEnabled(False)
        self._worker = FFmpegWorker(cmd, duration)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _on_done(self, ok, msg, out_path):
        self.btn_convert.setEnabled(True)
        self.btn_run_custom.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Convert complete  ({size})", C["green"])
        else:
            self.requestToast.emit(f"Convert failed: {msg}", C["red"])
