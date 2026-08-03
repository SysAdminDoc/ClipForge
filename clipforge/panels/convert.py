"""Convert panel -- codec, format, resolution, speed, presets, cmd preview."""

import sys
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTextEdit, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size, estimate_output_size

from ..constants import C, BUILTIN_PRESETS
from ..settings import (
    consume_persistence_notices,
    delete_user_preset,
    export_presets,
    import_presets,
    load_user_presets,
    save_user_preset,
)
from ..tools import (
    FFMPEG,
    HW_ENCODERS,
    _unregister_temp_dir,
    _confirm_overwrite,
    create_job_temp_dir,
    hardware_decode_args,
    stream_copy_issues,
)
from ..workers import FFmpegWorker
from ..widgets import FlowLayout


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
        pl = FlowLayout(preset_grp)
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
        fl = FlowLayout(fmt_grp)
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
        ql = FlowLayout(q_grp)
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
        ql.addWidget(QLabel("Rate control:"))
        self.cmb_rate_control = QComboBox()
        self.cmb_rate_control.addItems(["Constant Quality", "Target Bitrate"])
        ql.addWidget(self.cmb_rate_control)
        self.spn_video_bitrate = QSpinBox()
        self.spn_video_bitrate.setRange(100, 200_000)
        self.spn_video_bitrate.setValue(5_000)
        self.spn_video_bitrate.setSuffix(" kbps")
        self.spn_video_bitrate.setToolTip("Average target video bitrate")
        ql.addWidget(self.spn_video_bitrate)
        self.chk_two_pass = QCheckBox("Two-pass")
        self.chk_two_pass.setToolTip("Better quality at target bitrate (slower)")
        ql.addWidget(self.chk_two_pass)
        layout.addWidget(q_grp)

        res_grp = QGroupBox("Resolution & Speed")
        rl = FlowLayout(res_grp)
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
        cmd_btn_row = FlowLayout()
        btn_copy_cmd = QPushButton("Copy")
        btn_copy_cmd.clicked.connect(self._copy_cmd)
        self.btn_gen_script = QPushButton("Save Script")
        self.btn_gen_script.setToolTip("Export the FFmpeg command as a .ps1 (Windows) or .sh (Unix) script")
        self.btn_gen_script.clicked.connect(self._gen_script)
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
        cmd_btn_row.addWidget(self.btn_gen_script)
        cmd_btn_row.addWidget(self.btn_reset_cmd)
        cmd_btn_row.addWidget(self.btn_run_custom)
        cmd_layout.addLayout(cmd_btn_row)
        layout.addWidget(cmd_grp)

        # Connect signals for live preview
        for widget in [self.cmb_container, self.cmb_vcodec, self.cmb_acodec, self.cmb_enc_preset,
                       self.cmb_resolution, self.cmb_fps]:
            widget.currentTextChanged.connect(self._update_cmd_preview)
        self.cmb_vcodec.currentTextChanged.connect(self._on_vcodec_changed)
        self.cmb_rate_control.currentTextChanged.connect(self._on_rate_control_changed)
        self.spn_crf.valueChanged.connect(self._update_cmd_preview)
        self.spn_video_bitrate.valueChanged.connect(self._update_cmd_preview)
        self.spn_video_bitrate.valueChanged.connect(self._update_estimate)
        self.spn_speed.valueChanged.connect(self._update_cmd_preview)
        self._update_rate_control_state()

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = FlowLayout()
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
        current = self.cmb_vcodec.currentText()
        self.cmb_vcodec.clear()
        items = ["H.264 (libx264)", "H.265 (libx265)", "VP9", "AV1 (libaom)",
                 "SVT-AV1 (libsvtav1)", "Copy (no re-encode)"]
        for label in HW_ENCODERS:
            items.insert(-1, label)
        self.cmb_vcodec.addItems(items)
        if current and self.cmb_vcodec.findText(current) >= 0:
            self.cmb_vcodec.setCurrentText(current)

    def refresh_hw_encoders(self):
        """Refresh choices after asynchronous capability discovery."""
        self._populate_vcodecs()
        self._update_cmd_preview()

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
        self.cmb_rate_control.setCurrentText(
            data.get("rate_control", "Constant Quality")
        )
        self.spn_video_bitrate.setValue(int(data.get("video_bitrate", 5000)))

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
            "rate_control": self.cmb_rate_control.currentText(),
            "video_bitrate": self.spn_video_bitrate.value(),
        }
        saved_name = save_user_preset(name.strip(), data)
        if saved_name:
            self._refresh_presets()
            self.requestToast.emit(f"Preset '{saved_name}' saved", C["green"])
        else:
            self._show_persistence_error("Failed to save preset", C["red"])

    def _show_persistence_error(self, fallback, color):
        notices = consume_persistence_notices()
        message = notices[-1]["message"] if notices else fallback
        self.requestToast.emit(message, color)

    def _export_presets(self):
        """Export all user presets (or selected) to a JSON file."""
        user = load_user_presets()
        if not user:
            self.requestToast.emit("No custom presets to export", C["yellow"])
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Export Presets", str(Path.home() / "clipforge_presets.json"),
            "JSON Files (*.json);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path):
            return
        if export_presets(list(user.keys()), out_path):
            self.requestToast.emit(f"Exported {len(user)} preset(s)", C["green"])
        else:
            self._show_persistence_error("Export failed", C["red"])

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
            self._show_persistence_error(
                "No valid presets found in file",
                C["yellow"],
            )

    def _delete_preset(self):
        text = self.cmb_preset_select.currentText()
        if text.startswith("[Custom] "):
            name = text.replace("[Custom] ", "")
            if delete_user_preset(name):
                self._refresh_presets()
                self.requestToast.emit(f"Preset '{name}' deleted", C["yellow"])
            else:
                self._show_persistence_error("Failed to delete preset", C["red"])

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
        self.cmb_rate_control.setCurrentText("Constant Quality")
        self.spn_video_bitrate.setValue(5_000)
        self.chk_two_pass.setChecked(False)
        self.cmb_preset_select.setCurrentIndex(0)
        self.requestToast.emit("Settings reset to defaults", C["blue"])

    def _on_container_changed(self, container):
        if container == "WebM":
            self.cmb_vcodec.setCurrentText("VP9")
            self.cmb_acodec.setCurrentText("Opus")
        elif container == "GIF":
            self.cmb_acodec.setCurrentText("None (remove audio)")
        self._update_rate_control_state()

    def _on_vcodec_changed(self, vcodec_text):
        is_av1 = "AV1" in vcodec_text or "svtav1" in vcodec_text.lower()
        is_vp9 = "VP9" in vcodec_text
        max_crf = 63 if (is_av1 or is_vp9) else 51
        if self.spn_crf.maximum() != max_crf:
            self.spn_crf.setRange(0, max_crf)
        self._update_rate_control_state()
        self._update_estimate()

    def _on_rate_control_changed(self):
        self._update_rate_control_state()
        self._update_estimate()
        self._update_cmd_preview()

    def _selected_video_encoder(self):
        mapping = {
            "H.264 (libx264)": "libx264",
            "H.265 (libx265)": "libx265",
            "VP9": "libvpx-vp9",
            "AV1 (libaom)": "libaom-av1",
            "SVT-AV1 (libsvtav1)": "libsvtav1",
            "Copy (no re-encode)": "copy",
        }
        mapping.update(HW_ENCODERS)
        return mapping.get(self.cmb_vcodec.currentText(), "libx264")

    def _two_pass_supported(self):
        encoder = self._selected_video_encoder()
        container = self.cmb_container.currentText()
        compatible = {
            "libx264": {"MP4", "MKV", "MOV", "AVI"},
            "libvpx-vp9": {"WebM", "MKV"},
            "libaom-av1": {"MP4", "MKV", "WebM"},
        }
        return (
            self.cmb_rate_control.currentText() == "Target Bitrate"
            and container in compatible.get(encoder, set())
        )

    def _update_rate_control_state(self):
        target = self.cmb_rate_control.currentText() == "Target Bitrate"
        encoder = self._selected_video_encoder()
        rate_control_available = (
            encoder != "copy" and self.cmb_container.currentText() != "GIF"
        )
        self.cmb_rate_control.setEnabled(rate_control_available)
        if not rate_control_available:
            self.cmb_rate_control.setCurrentText("Constant Quality")
            target = False
        self.spn_video_bitrate.setEnabled(target and rate_control_available)
        self.spn_crf.setEnabled(not target and encoder != "copy")
        supported = self._two_pass_supported()
        self.chk_two_pass.setEnabled(supported)
        if not supported:
            self.chk_two_pass.setChecked(False)
        if target and not supported:
            self.chk_two_pass.setToolTip(
                "Two-pass is unavailable for this encoder/container; "
                "target bitrate still uses one pass"
            )
        else:
            self.chk_two_pass.setToolTip(
                "Better quality at target bitrate (slower)"
            )

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
        if self.cmb_rate_control.currentText() == "Target Bitrate":
            audio_kbps = (
                0
                if self.cmb_acodec.currentText() == "None (remove audio)"
                else 192
            )
            est = int(
                max(float(dur), 0)
                * (self.spn_video_bitrate.value() + audio_kbps)
                * 1000
                / 8
            )
        else:
            est = estimate_output_size(dur, crf, w, h)
        vcodec_text = self.cmb_vcodec.currentText()
        is_av1 = "AV1" in vcodec_text or "svtav1" in vcodec_text.lower()
        is_vp9 = "VP9" in vcodec_text
        if is_av1:
            quality_labels = {range(0, 10): "Lossless / near-lossless",
                              range(10, 20): "Very high quality", range(20, 28): "High quality",
                              range(28, 35): "Good quality (recommended)", range(35, 45): "Medium quality",
                              range(45, 56): "Low quality", range(56, 64): "Very low quality"}
        elif is_vp9:
            quality_labels = {range(0, 5): "Lossless", range(5, 15): "High quality",
                              range(15, 25): "Visually lossless", range(25, 35): "Good quality",
                              range(35, 45): "Medium quality", range(45, 56): "Low quality",
                              range(56, 64): "Very low quality"}
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
        # Show CRF/CQ equivalence across codecs
        if is_av1:
            equiv_h264 = max(0, int(crf * 51 / 63))
            hint += f"  (~ H.264 CRF {equiv_h264})"
        elif is_vp9:
            equiv_h264 = max(0, int(crf * 51 / 63))
            hint += f"  (~ H.264 CRF {equiv_h264})"
        self.lbl_quality_hint.setText(
            "Average bitrate target"
            if self.cmb_rate_control.currentText() == "Target Bitrate"
            else hint
        )
        self.lbl_estimate.setText(f"Estimated output: ~{format_size(est)}")

    def _build_cmd(self, out_path=None):
        """Build the FFmpeg command from current settings."""
        if not self._filepath or not FFMPEG:
            return []
        target = out_path or "<output>"
        vcodec = self._selected_video_encoder()
        container = self.cmb_container.currentText()
        target_bitrate = (
            self.cmb_rate_control.currentText() == "Target Bitrate"
        )

        # Build command with optional hardware decode
        cmd = [FFMPEG, "-y"]
        cmd += hardware_decode_args(vcodec)
        cmd += ["-i", self._filepath]

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
                if target_bitrate:
                    cmd += ["-b:v", f"{self.spn_video_bitrate.value()}k"]
                    if vcodec in ("libx264", "libx265"):
                        cmd += ["-preset", self.cmb_enc_preset.currentText()]
                elif vcodec in ("libx264", "libx265"):
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
            self.txt_cmd_preview.setText(shlex.join(cmd))

    def _copy_cmd(self):
        QApplication.clipboard().setText(self.txt_cmd_preview.toPlainText())
        self.requestToast.emit("Command copied to clipboard", C["blue"])

    def _gen_script(self):
        """Export the FFmpeg command as a runnable script."""
        cmd_text = self.txt_cmd_preview.toPlainText().strip()
        if not cmd_text:
            self.requestToast.emit("No command to export", C["yellow"])
            return
        if sys.platform == "win32":
            ext = ".ps1"
            filt = "PowerShell Scripts (*.ps1);;Batch Files (*.bat);;All Files (*)"
            default_name = "clipforge_encode.ps1"
        else:
            ext = ".sh"
            filt = "Shell Scripts (*.sh);;All Files (*)"
            default_name = "clipforge_encode.sh"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Script", str(Path.home() / default_name), filt)
        if not out_path or not _confirm_overwrite(self, out_path):
            return
        try:
            parts = shlex.split(cmd_text, posix=True)
        except ValueError:
            self.requestToast.emit("Invalid command syntax", C["red"])
            return
        is_bat = out_path.endswith(".bat")
        rendered_command = (
            subprocess.list2cmdline(parts) if is_bat else shlex.join(parts)
        )
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                if is_bat:
                    f.write("@echo off\n")
                    f.write(f"REM Generated by ClipForge\n")
                    f.write(f"{rendered_command}\n")
                    f.write("pause\n")
                elif out_path.endswith(".ps1"):
                    f.write(f"# Generated by ClipForge\n")
                    f.write(f"{rendered_command}\n")
                    f.write('Write-Host "Done." -ForegroundColor Green\n')
                else:
                    f.write("#!/usr/bin/env bash\n")
                    f.write(f"# Generated by ClipForge\n")
                    f.write(f"set -e\n\n")
                    f.write(f"{rendered_command}\n\n")
                    f.write('echo "Done."\n')
            self.requestToast.emit(f"Script saved: {Path(out_path).name}", C["green"])
        except OSError as e:
            self.requestToast.emit(f"Failed to save script: {e}", C["red"])

    def _run_custom_cmd(self):
        if not self._filepath or not FFMPEG:
            return
        cmd_text = self.txt_cmd_preview.toPlainText().strip()
        if not cmd_text:
            return
        try:
            parts = shlex.split(cmd_text, posix=True)
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
        if out_path and not _confirm_overwrite(self, out_path, self._filepath):
            return
        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setValue(0)
        self.btn_convert.setEnabled(False)
        self.btn_run_custom.setEnabled(False)
        self._worker = FFmpegWorker(
            parts,
            duration,
            output_path=out_path,
            overwrite=bool(out_path and os.path.exists(out_path)),
        )
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_done(ok, msg, out_path or ""))
        self._worker.start()

    def _conversion_preflight(self):
        container = self.cmb_container.currentText()
        video_copy = self.cmb_vcodec.currentText() == "Copy (no re-encode)"
        audio_copy = self.cmb_acodec.currentText() == "Copy (no re-encode)"
        issues = []
        if video_copy and (
            self.cmb_resolution.currentText() != "Original"
            or self.cmb_fps.currentText() != "Original"
            or self.spn_speed.value() != 1.0
        ):
            issues.append(
                "Video stream copy cannot change resolution, frame rate, or speed; "
                "choose a video encoder."
            )
        if audio_copy and self.spn_speed.value() != 1.0:
            issues.append(
                "Audio stream copy cannot change speed; choose an audio encoder."
            )
        copied_streams = []
        if video_copy:
            copied_streams.extend(
                stream
                for stream in (self._info or {}).get("streams", [])
                if stream.get("codec_type") == "video"
            )
        if audio_copy:
            copied_streams.extend(
                stream
                for stream in (self._info or {}).get("streams", [])
                if stream.get("codec_type") == "audio"
            )
        if copied_streams:
            issues.extend(stream_copy_issues(container, copied_streams))
        if self.chk_two_pass.isChecked() and not self._two_pass_supported():
            issues.append(
                "Two-pass requires Target Bitrate with a supported software "
                "encoder/container combination."
            )
        return issues

    def _do_convert(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"MP4": ".mp4", "MKV": ".mkv", "WebM": ".webm", "MOV": ".mov", "AVI": ".avi", "GIF": ".gif"}
        container = self.cmb_container.currentText()
        issues = self._conversion_preflight()
        if issues:
            self.console.append(
                "[Convert preflight]\n" + "\n".join(f"• {issue}" for issue in issues) + "\n"
            )
            self.requestToast.emit(issues[0], C["red"])
            return
        ext = ext_map.get(container, ".mp4")
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Converted Video", str(src.parent / f"{src.stem}_converted{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi *.gif);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)

        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setValue(0)
        self.btn_convert.setEnabled(False)

        if self.chk_two_pass.isChecked():
            self._do_two_pass(out_path, duration, overwrite)
        else:
            cmd = self._build_cmd(out_path)
            self._worker = FFmpegWorker(
                cmd,
                duration,
                output_path=out_path,
                overwrite=overwrite,
            )
            self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
            self._worker.speed_info.connect(self.lbl_progress_detail.setText)
            self._worker.log_output.connect(self.console.append)
            self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
            self._worker.start()

    def _do_two_pass(self, out_path, duration, overwrite):
        """Execute two-pass encoding for higher quality."""
        if not self._two_pass_supported():
            self._on_done(
                False,
                "Two-pass is not supported by these rate-control settings",
                out_path,
            )
            return
        workspace = create_job_temp_dir("two-pass")
        self._two_pass_workspace = Path(workspace)
        passlog = self._two_pass_workspace / "ffmpeg-pass"
        self._two_pass_log = os.fspath(passlog)

        # Build the base command but split into pass1 and pass2
        cmd_base = self._build_cmd(out_path)
        if not cmd_base:
            self.btn_convert.setEnabled(True)
            self._cleanup_passlog()
            return

        # Pass 1: analyze
        cmd1 = cmd_base[:-1]  # remove output path
        cmd1 += ["-pass", "1", "-passlogfile", passlog, "-an", "-f", "null"]
        if sys.platform == "win32":
            cmd1.append("NUL")
        else:
            cmd1.append("/dev/null")

        self.console.append("[Two-Pass] Pass 1: Analyzing...\n")
        self._two_pass_out = out_path
        self._two_pass_overwrite = overwrite
        self._two_pass_duration = duration
        self._two_pass_cmd_base = cmd_base
        self._worker = FFmpegWorker(cmd1, duration)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v * 0.5)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_two_pass_1_done)
        self._worker.start()

    def _on_two_pass_1_done(self, ok, msg):
        if not ok:
            self._on_done(False, f"Two-pass analysis failed: {msg}", self._two_pass_out)
            self._cleanup_passlog()
            return
        # Pass 2: encode
        cmd2 = self._two_pass_cmd_base[:-1]  # remove output
        cmd2 += ["-pass", "2", "-passlogfile", self._two_pass_log]
        cmd2.append(self._two_pass_out)

        self.console.append("[Two-Pass] Pass 2: Encoding...\n")
        self._worker = FFmpegWorker(
            cmd2,
            self._two_pass_duration,
            output_path=self._two_pass_out,
            overwrite=self._two_pass_overwrite,
        )
        self._worker.progress.connect(lambda v: self.progress.setValue(50 + int(v * 0.5)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_two_pass_2_done(ok, msg))
        self._worker.start()

    def _on_two_pass_2_done(self, ok, msg):
        self._cleanup_passlog()
        self._on_done(ok, msg, self._two_pass_out)

    def _cleanup_passlog(self):
        """Remove only this panel's registered two-pass workspace."""
        workspace = getattr(self, "_two_pass_workspace", None)
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
            _unregister_temp_dir(workspace)
            self._two_pass_workspace = None
        self._two_pass_log = None

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
