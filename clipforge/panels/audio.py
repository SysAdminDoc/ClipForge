"""Audio panel -- extract, replace, or remove audio."""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size

from ..constants import C
from ..tools import FFMPEG
from ..workers import FFmpegWorker


class AudioPanel(QWidget):
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
        layout.setSpacing(12)

        info_grp = QGroupBox("Audio Stream Info")
        il = QVBoxLayout(info_grp)
        self.lbl_audio_info = QLabel("Open a video to see audio details")
        self.lbl_audio_info.setProperty("class", "dimLabel")
        il.addWidget(self.lbl_audio_info)
        layout.addWidget(info_grp)

        ext_grp = QGroupBox("Extract Audio")
        el = QHBoxLayout(ext_grp)
        el.addWidget(QLabel("Format:"))
        self.cmb_extract_fmt = QComboBox()
        self.cmb_extract_fmt.addItems(["MP3", "AAC", "WAV", "FLAC", "OGG", "Original (copy)"])
        el.addWidget(self.cmb_extract_fmt)
        self.btn_extract = QPushButton("Extract Audio")
        self.btn_extract.setObjectName("primaryBtn")
        self.btn_extract.setEnabled(False)
        self.btn_extract.clicked.connect(self._do_extract)
        el.addStretch()
        el.addWidget(self.btn_extract)
        layout.addWidget(ext_grp)

        rep_grp = QGroupBox("Replace Audio")
        rl = QVBoxLayout(rep_grp)
        rep_row = QHBoxLayout()
        self.lbl_replace_file = QLabel("No replacement audio selected")
        self.lbl_replace_file.setProperty("class", "dimLabel")
        self.btn_browse_audio = QPushButton("Browse Audio")
        self.btn_browse_audio.clicked.connect(self._browse_audio)
        rep_row.addWidget(self.lbl_replace_file, 1)
        rep_row.addWidget(self.btn_browse_audio)
        rl.addLayout(rep_row)

        rep_opts = QHBoxLayout()
        self.chk_keep_original = QCheckBox("Mix with original audio")
        rep_opts.addWidget(self.chk_keep_original)
        rep_opts.addStretch()
        self.btn_replace = QPushButton("Replace Audio")
        self.btn_replace.setObjectName("primaryBtn")
        self.btn_replace.setEnabled(False)
        self.btn_replace.clicked.connect(self._do_replace)
        rep_opts.addWidget(self.btn_replace)
        rl.addLayout(rep_opts)
        layout.addWidget(rep_grp)

        rem_grp = QGroupBox("Remove Audio")
        rm_l = QHBoxLayout(rem_grp)
        rm_l.addWidget(QLabel("Strip all audio tracks from video"))
        rm_l.addStretch()
        self.btn_remove = QPushButton("Remove Audio")
        self.btn_remove.setObjectName("dangerBtn")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self._do_remove)
        rm_l.addWidget(self.btn_remove)
        layout.addWidget(rem_grp)

        btn_row = QHBoxLayout()
        self.btn_reset_defaults = QPushButton("Reset to Defaults")
        self.btn_reset_defaults.setToolTip("Reset audio panel to defaults")
        self.btn_reset_defaults.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self.btn_reset_defaults)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        layout.addStretch()

        self._replace_audio_path = None

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        has_ffmpeg = bool(FFMPEG)
        self.btn_extract.setEnabled(has_ffmpeg)
        self.btn_remove.setEnabled(has_ffmpeg)
        if info:
            codec = info.get("audio_codec", "none")
            channels = info.get("audio_channels", 0)
            rate = info.get("audio_sample_rate", "?")
            if codec and codec != "none":
                self.lbl_audio_info.setText(
                    f"Codec: {codec}  |  Channels: {channels}  |  Sample Rate: {rate} Hz"
                )
            else:
                self.lbl_audio_info.setText("No audio stream detected")
        else:
            self.lbl_audio_info.setText("Could not read metadata")

    def _reset_to_defaults(self):
        """Reset audio panel to defaults."""
        self.cmb_extract_fmt.setCurrentIndex(0)
        self._replace_audio_path = None
        self.lbl_replace_file.setText("No replacement audio selected")
        self.btn_replace.setEnabled(False)
        self.chk_keep_original.setChecked(False)
        self.requestToast.emit("Audio settings reset to defaults", C["blue"])

    def _browse_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio File", "",
            "Audio Files (*.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma);;All Files (*)")
        if path:
            self._replace_audio_path = path
            self.lbl_replace_file.setText(Path(path).name)
            self.btn_replace.setEnabled(bool(FFMPEG))

    def _do_extract(self):
        if not self._filepath or not FFMPEG:
            return
        fmt_map = {"MP3": (".mp3", ["libmp3lame", "-b:a", "192k"]),
                   "AAC": (".aac", ["aac", "-b:a", "192k"]),
                   "WAV": (".wav", ["pcm_s16le"]),
                   "FLAC": (".flac", ["flac"]),
                   "OGG": (".ogg", ["libvorbis", "-b:a", "192k"]),
                   "Original (copy)": (".mka", ["copy"])}
        fmt = self.cmb_extract_fmt.currentText()
        ext, codec_args = fmt_map.get(fmt, (".mp3", ["libmp3lame", "-b:a", "192k"]))
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Audio", str(src.parent / f"{src.stem}_audio{ext}"),
            f"Audio Files (*{ext});;All Files (*)")
        if not out_path:
            return
        duration = self._info.get("duration", 0) if self._info else 0
        cmd = [FFMPEG, "-y", "-i", self._filepath, "-vn", "-c:a"] + codec_args + [out_path]
        self._run_worker(cmd, duration, out_path, "Extract")

    def _do_replace(self):
        if not self._filepath or not self._replace_audio_path or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Video", str(src.parent / f"{src.stem}_newaudio{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path:
            return
        duration = self._info.get("duration", 0) if self._info else 0
        if self.chk_keep_original.isChecked():
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-i", self._replace_audio_path,
                   "-c:v", "copy",
                   "-filter_complex", "[0:a][1:a]amerge=inputs=2[a]",
                   "-map", "0:v", "-map", "[a]",
                   "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
        else:
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-i", self._replace_audio_path,
                   "-c:v", "copy", "-map", "0:v", "-map", "1:a",
                   "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
        self._run_worker(cmd, duration, out_path, "Replace audio")

    def _do_remove(self):
        if not self._filepath or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Video (No Audio)", str(src.parent / f"{src.stem}_noaudio{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path:
            return
        duration = self._info.get("duration", 0) if self._info else 0
        cmd = [FFMPEG, "-y", "-i", self._filepath, "-c:v", "copy", "-an", out_path]
        self._run_worker(cmd, duration, out_path, "Remove audio")

    def _set_buttons_enabled(self, enabled):
        self.btn_extract.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)

    def _run_worker(self, cmd, duration, out_path, label):
        self.progress.setValue(0)
        self._set_buttons_enabled(False)
        self._worker = FFmpegWorker(cmd, duration)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path, label))
        self._worker.start()

    def _on_done(self, ok, msg, out_path, label):
        self._set_buttons_enabled(True)
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"{label} complete  ({size})", C["green"])
        else:
            self.requestToast.emit(f"{label} failed: {msg}", C["red"])
