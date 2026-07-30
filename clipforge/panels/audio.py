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
from ..tools import FFMPEG, _confirm_overwrite, probe_media, stream_copy_issues
from ..workers import FFmpegWorker
from ..widgets import FlowLayout


class AudioPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._filepath = None
        self._info = None
        self._worker = None
        self._audio_streams = []
        self._replace_audio_streams = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info_grp = QGroupBox("Audio Stream Info")
        il = QVBoxLayout(info_grp)
        self.lbl_audio_info = QLabel("Open a video to see audio details")
        self.lbl_audio_info.setProperty("class", "dimLabel")
        il.addWidget(self.lbl_audio_info)
        stream_row = FlowLayout()
        stream_row.addWidget(QLabel("Source stream:"))
        self.cmb_audio_stream = QComboBox()
        stream_row.addWidget(self.cmb_audio_stream, 1)
        stream_row.addWidget(QLabel("Output layout:"))
        self.cmb_audio_layout = QComboBox()
        self.cmb_audio_layout.addItems(
            ["Keep source layout", "Mono", "Stereo", "5.1 surround"]
        )
        stream_row.addWidget(self.cmb_audio_layout)
        il.addLayout(stream_row)
        layout.addWidget(info_grp)

        ext_grp = QGroupBox("Extract Audio")
        el = FlowLayout(ext_grp)
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
        rep_row = FlowLayout()
        self.lbl_replace_file = QLabel("No replacement audio selected")
        self.lbl_replace_file.setProperty("class", "dimLabel")
        self.btn_browse_audio = QPushButton("Browse Audio")
        self.btn_browse_audio.clicked.connect(self._browse_audio)
        rep_row.addWidget(self.lbl_replace_file, 1)
        self.cmb_replace_stream = QComboBox()
        self.cmb_replace_stream.setEnabled(False)
        self.cmb_replace_stream.setToolTip("Audio stream from the replacement file")
        rep_row.addWidget(self.cmb_replace_stream)
        rep_row.addWidget(self.btn_browse_audio)
        rl.addLayout(rep_row)

        rep_opts = FlowLayout()
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
        rm_l = FlowLayout(rem_grp)
        rm_l.addWidget(QLabel("Strip all audio tracks from video"))
        rm_l.addStretch()
        self.btn_remove = QPushButton("Remove Audio")
        self.btn_remove.setObjectName("dangerBtn")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self._do_remove)
        rm_l.addWidget(self.btn_remove)
        layout.addWidget(rem_grp)

        btn_row = FlowLayout()
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
        self._audio_streams = [
            stream
            for stream in (info or {}).get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
        self.cmb_audio_stream.clear()
        for stream in self._audio_streams:
            disposition = stream.get("disposition", {})
            default = " · default" if disposition.get("default") else ""
            self.cmb_audio_stream.addItem(
                (
                    f"#{stream.get('index')} · {stream.get('codec_name')} · "
                    f"{stream.get('channel_layout') or str(stream.get('channels')) + 'ch'}"
                    f"{default}"
                ),
                stream.get("index"),
            )
        has_audio = bool(self._audio_streams)
        self.cmb_audio_stream.setEnabled(has_audio)
        self.btn_extract.setEnabled(has_ffmpeg and has_audio)
        self.btn_remove.setEnabled(has_ffmpeg and has_audio)
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
        self.cmb_audio_layout.setCurrentIndex(0)
        self._replace_audio_path = None
        self._replace_audio_streams = []
        self.lbl_replace_file.setText("No replacement audio selected")
        self.cmb_replace_stream.clear()
        self.cmb_replace_stream.setEnabled(False)
        self.btn_replace.setEnabled(False)
        self.chk_keep_original.setChecked(False)
        self.requestToast.emit("Audio settings reset to defaults", C["blue"])

    def _browse_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio File", "",
            "Audio Files (*.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma);;All Files (*)")
        if path:
            result = probe_media(path)
            streams = [
                stream
                for stream in (result.info or {}).get("streams", [])
                if stream.get("codec_type") == "audio"
            ]
            if result.error or not streams:
                message = result.error.message if result.error else "No audio stream found"
                self.requestToast.emit(
                    f"Replacement audio is not usable: {message}", C["red"]
                )
                return
            self._replace_audio_path = path
            self._replace_audio_streams = streams
            self.lbl_replace_file.setText(Path(path).name)
            self.cmb_replace_stream.clear()
            for stream in streams:
                self.cmb_replace_stream.addItem(
                    (
                        f"#{stream.get('index')} · {stream.get('codec_name')} · "
                        f"{stream.get('channel_layout') or str(stream.get('channels')) + 'ch'}"
                    ),
                    stream.get("index"),
                )
            self.cmb_replace_stream.setEnabled(True)
            self.btn_replace.setEnabled(bool(FFMPEG))

    def _selected_source_audio_index(self):
        value = self.cmb_audio_stream.currentData()
        return int(value) if value is not None else None

    def _layout_args(self):
        return {
            "Mono": ["-ac", "1"],
            "Stereo": ["-ac", "2"],
            "5.1 surround": ["-ac", "6"],
        }.get(self.cmb_audio_layout.currentText(), [])

    def _video_copy_issues(self, output_path):
        container = {
            ".mp4": "MP4",
            ".mov": "MOV",
            ".mkv": "MKV",
            ".webm": "WEBM",
        }.get(Path(output_path).suffix.lower())
        if not container:
            return ["Choose an MP4, MOV, MKV, or WebM output file."]
        video_streams = [
            stream
            for stream in (self._info or {}).get("streams", [])
            if stream.get("codec_type") == "video"
        ][:1]
        return stream_copy_issues(container, video_streams)

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
        stream_index = self._selected_source_audio_index()
        if stream_index is None:
            self.requestToast.emit("Select an audio stream to extract", C["red"])
            return
        layout_args = self._layout_args()
        if fmt == "Original (copy)" and layout_args:
            self.requestToast.emit(
                "Original copy cannot change channel layout; choose an encoded format",
                C["red"],
            )
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Audio", str(src.parent / f"{src.stem}_audio{ext}"),
            f"Audio Files (*{ext});;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        duration = self._info.get("duration", 0) if self._info else 0
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            self._filepath,
            "-map",
            f"0:{stream_index}",
            "-vn",
            "-c:a",
        ] + codec_args + layout_args + [out_path]
        self._run_worker(cmd, duration, out_path, "Extract")

    def _do_replace(self):
        if not self._filepath or not self._replace_audio_path or not FFMPEG:
            return
        src = Path(self._filepath)
        default_suffix = (
            src.suffix
            if src.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}
            else ".mkv"
        )
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video",
            str(src.parent / f"{src.stem}_newaudio{default_suffix}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm);;All Files (*)",
        )
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        issues = self._video_copy_issues(out_path)
        if issues:
            self.requestToast.emit(issues[0], C["red"])
            self.console.append(f"[Audio preflight] {issues[0]}\n")
            return
        duration = self._info.get("duration", 0) if self._info else 0
        source_audio_index = self._selected_source_audio_index()
        replacement_audio_index = self.cmb_replace_stream.currentData()
        if replacement_audio_index is None:
            self.requestToast.emit("Select a replacement audio stream", C["red"])
            return
        video_stream = next(
            (
                stream
                for stream in (self._info or {}).get("streams", [])
                if stream.get("codec_type") == "video"
            ),
            None,
        )
        if not video_stream:
            self.requestToast.emit("The source has no video stream to preserve", C["red"])
            return
        video_map = f"0:{video_stream.get('index')}"
        layout_args = self._layout_args()
        if self.chk_keep_original.isChecked():
            if source_audio_index is None:
                self.requestToast.emit(
                    "The source has no audio stream to mix", C["red"]
                )
                return
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-i", self._replace_audio_path,
                   "-c:v", "copy",
                   "-filter_complex",
                   (
                       f"[0:{source_audio_index}][1:{int(replacement_audio_index)}]"
                       "amix=inputs=2:duration=shortest:normalize=0[a]"
                   ),
                   "-map", video_map, "-map", "[a]",
                   "-c:a", "aac", "-b:a", "192k"] + layout_args + [
                       "-shortest", out_path
                   ]
        else:
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-i", self._replace_audio_path,
                   "-c:v", "copy", "-map", video_map,
                   "-map", f"1:{int(replacement_audio_index)}",
                   "-c:a", "aac", "-b:a", "192k"] + layout_args + [
                       "-shortest", out_path
                   ]
        self._run_worker(cmd, duration, out_path, "Replace audio")

    def _do_remove(self):
        if not self._filepath or not FFMPEG:
            return
        src = Path(self._filepath)
        default_suffix = (
            src.suffix
            if src.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}
            else ".mkv"
        )
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Video (No Audio)",
            str(src.parent / f"{src.stem}_noaudio{default_suffix}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm);;All Files (*)",
        )
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        container = {
            ".mp4": "MP4",
            ".mov": "MOV",
            ".mkv": "MKV",
            ".webm": "WEBM",
        }.get(Path(out_path).suffix.lower())
        selected_streams = [
            stream
            for stream in (self._info or {}).get("streams", [])
            if stream.get("codec_type") != "audio"
        ]
        issues = (
            stream_copy_issues(container, selected_streams)
            if container
            else ["Choose an MP4, MOV, MKV, or WebM output file."]
        )
        if issues:
            self.requestToast.emit(issues[0], C["red"])
            self.console.append(f"[Audio preflight] {issues[0]}\n")
            return
        duration = self._info.get("duration", 0) if self._info else 0
        cmd = [
            FFMPEG, "-y", "-i", self._filepath,
            "-map", "0", "-map", "-0:a", "-c", "copy", out_path,
        ]
        self._run_worker(cmd, duration, out_path, "Remove audio")

    def _set_buttons_enabled(self, enabled):
        has_audio = bool(self._audio_streams)
        self.btn_extract.setEnabled(enabled and has_audio)
        self.btn_remove.setEnabled(enabled and has_audio)

    def _run_worker(self, cmd, duration, out_path, label):
        self.progress.setValue(0)
        self._set_buttons_enabled(False)
        self._worker = FFmpegWorker(
            cmd,
            duration,
            output_path=out_path,
            overwrite=os.path.exists(out_path),
        )
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
