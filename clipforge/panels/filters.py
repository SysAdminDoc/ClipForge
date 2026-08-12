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
    QListWidget, QListWidgetItem, QAbstractItemView,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap

from clipforge_utils import format_duration_short, format_size

from ..constants import C
from ..tools import (
    FFMPEG,
    _confirm_overwrite, create_job_temp_dir, _unregister_temp_dir,
    escape_ffmpeg_filter_value,
)
from ..workers import FFmpegWorker, FrameExtractWorker
from ..workers import OCRWorker
from ..ocr import find_tesseract, output_srt_path
from ..widgets import FlowLayout
from ..filter_stack import (
    FILTER_STACK_DEFAULT,
    FILTER_STACK_LABELS,
    filter_graph,
    normalize_filter_order,
    reorder_filter_stack,
)
from ..redaction import (
    DEFAULT_REDACTION,
    build_redaction_filter,
    normalize_redaction_state,
)


class _CompactDoubleSpinBox(QDoubleSpinBox):
    """Keep the two-keyframe editor usable inside the narrow panel column."""

    def __init__(self, compact_width, parent=None):
        super().__init__(parent)
        self._compact_width = compact_width

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(self._compact_width, size.height())

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(self._compact_width, size.height())


class FiltersPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._filepath = None
        self._info = None
        self._worker = None
        self._ocr_worker = None
        self._frame_worker = None
        self._caption_tmpdir = None
        self._filter_stack_order = list(FILTER_STACK_DEFAULT)
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
            slider.setAccessibleName(f"{name} adjustment")
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

        row1 = FlowLayout()
        self.chk_stabilize = QCheckBox("Video Stabilization (vidstab)")
        self.chk_denoise = QCheckBox("Noise Reduction (nlmeans)")
        self.chk_sharpen = QCheckBox("Sharpen (unsharp)")
        self.chk_deinterlace = QCheckBox("Deinterlace (yadif)")
        row1.addWidget(self.chk_stabilize)
        row1.addWidget(self.chk_denoise)
        pl.addLayout(row1)

        row2 = FlowLayout()
        row2.addWidget(self.chk_sharpen)
        row2.addWidget(self.chk_deinterlace)
        pl.addLayout(row2)
        layout.addWidget(proc_grp)

        redaction_grp = QGroupBox("Motion-tracked Redaction")
        redaction_layout = QVBoxLayout(redaction_grp)
        self.chk_redaction = QCheckBox("Blur tracked region")
        self.chk_redaction.setAccessibleName("Enable motion-tracked redaction")
        redaction_layout.addWidget(self.chk_redaction)
        redaction_help = QLabel(
            "Times are seconds; X/Y/width/height are frame percentages. "
            "The rectangle is linearly tracked between keyframes before blur."
        )
        redaction_help.setWordWrap(True)
        redaction_help.setFixedWidth(300)
        redaction_help.setProperty("class", "dimLabel")
        redaction_layout.addWidget(redaction_help)
        redaction_grid = QGridLayout()

        def _time_spin(name, value):
            control = _CompactDoubleSpinBox(78)
            control.setRange(0.0, 86400.0)
            control.setDecimals(3)
            control.setSingleStep(0.1)
            control.setValue(value)
            control.setAccessibleName(name)
            control.setFixedWidth(78)
            return control

        def _percent_spin(name, value):
            control = _CompactDoubleSpinBox(58)
            control.setRange(0.1, 99.9)
            control.setDecimals(1)
            control.setSingleStep(1.0)
            control.setValue(value)
            control.setAccessibleName(name)
            control.setFixedWidth(58)
            return control

        self.spn_redaction_start = _time_spin("Redaction start time", 0.0)
        self.spn_redaction_start_x = _percent_spin("Redaction start X", 35.0)
        self.spn_redaction_start_y = _percent_spin("Redaction start Y", 25.0)
        self.spn_redaction_start_w = _percent_spin("Redaction start width", 30.0)
        self.spn_redaction_start_h = _percent_spin("Redaction start height", 30.0)
        self.spn_redaction_end = _time_spin("Redaction end time", 1.0)
        self.spn_redaction_end_x = _percent_spin("Redaction end X", 35.0)
        self.spn_redaction_end_y = _percent_spin("Redaction end Y", 25.0)
        self.spn_redaction_end_w = _percent_spin("Redaction end width", 30.0)
        self.spn_redaction_end_h = _percent_spin("Redaction end height", 30.0)
        for row, label, controls in (
            (
                0,
                "Start",
                (
                    self.spn_redaction_start,
                    self.spn_redaction_start_x,
                    self.spn_redaction_start_y,
                    self.spn_redaction_start_w,
                    self.spn_redaction_start_h,
                ),
            ),
            (
                1,
                "End",
                (
                    self.spn_redaction_end,
                    self.spn_redaction_end_x,
                    self.spn_redaction_end_y,
                    self.spn_redaction_end_w,
                    self.spn_redaction_end_h,
                ),
            ),
        ):
            redaction_grid.addWidget(QLabel(label), row, 0)
            for column, control in enumerate(controls, start=1):
                redaction_grid.addWidget(control, row, column)
                control.valueChanged.connect(lambda _value: self._refresh_filter_graph())
        redaction_layout.addLayout(redaction_grid)
        redaction_options = FlowLayout()
        redaction_options.addWidget(QLabel("Blur radius:"))
        self.spn_redaction_blur = QSpinBox()
        self.spn_redaction_blur.setRange(1, 8)
        self.spn_redaction_blur.setValue(6)
        self.spn_redaction_blur.setSuffix(" px")
        self.spn_redaction_blur.setAccessibleName("Redaction blur radius")
        self.spn_redaction_blur.valueChanged.connect(
            lambda _value: self._refresh_filter_graph()
        )
        redaction_options.addWidget(self.spn_redaction_blur)
        self.lbl_redaction_status = QLabel("Disabled — no redaction")
        self.lbl_redaction_status.setProperty("class", "dimLabel")
        redaction_options.addWidget(self.lbl_redaction_status, 1)
        redaction_layout.addLayout(redaction_options)
        self.chk_redaction.stateChanged.connect(
            lambda _state: self._refresh_filter_graph()
        )
        layout.addWidget(redaction_grp)

        stack_grp = QGroupBox("Filter Stack (drag to reorder)")
        stack_layout = QVBoxLayout(stack_grp)
        self.lst_filter_stack = QListWidget()
        self.lst_filter_stack.setAccessibleName("Filter stack order")
        self.lst_filter_stack.setToolTip(
            "Drag filters to change the order used for the FFmpeg video graph"
        )
        self.lst_filter_stack.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lst_filter_stack.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.lst_filter_stack.currentRowChanged.connect(lambda _row: self._refresh_filter_graph())
        self.lst_filter_stack.model().rowsMoved.connect(
            lambda *_args: self._on_filter_stack_changed()
        )
        stack_layout.addWidget(self.lst_filter_stack)
        stack_buttons = FlowLayout()
        self.btn_filter_up = QPushButton("Move Up")
        self.btn_filter_up.clicked.connect(lambda: self._move_filter_stack(-1))
        self.btn_filter_down = QPushButton("Move Down")
        self.btn_filter_down.clicked.connect(lambda: self._move_filter_stack(1))
        self.lbl_filter_graph = QLabel("[video] → passthrough → [output]")
        self.lbl_filter_graph.setProperty("class", "dimLabel")
        self.lbl_filter_graph.setWordWrap(True)
        stack_buttons.addWidget(self.btn_filter_up)
        stack_buttons.addWidget(self.btn_filter_down)
        stack_buttons.addWidget(self.lbl_filter_graph, 1)
        stack_layout.addLayout(stack_buttons)
        layout.addWidget(stack_grp)
        self._populate_filter_stack()

        # Subtitle burn-in
        sub_grp = QGroupBox("Subtitle Burn-in")
        sl = FlowLayout(sub_grp)
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
        cap_row = FlowLayout()
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
        cap_btn_row = FlowLayout()
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

        ocr_grp = QGroupBox("Hard-sub OCR (Tesseract)")
        ocr_layout = QVBoxLayout(ocr_grp)
        ocr_row = FlowLayout()
        ocr_row.addWidget(QLabel("Language:"))
        self.cmb_ocr_lang = QComboBox()
        self.cmb_ocr_lang.addItems(["eng", "spa", "fra", "deu", "ita", "por", "jpn", "kor", "chi_sim"])
        self.cmb_ocr_lang.setAccessibleName("Tesseract OCR language")
        self.cmb_ocr_lang.setToolTip(
            "Use a language code installed in the local Tesseract data directory"
        )
        ocr_row.addWidget(self.cmb_ocr_lang)
        self.lbl_ocr_status = QLabel()
        self.lbl_ocr_status.setProperty("class", "dimLabel")
        self.lbl_ocr_status.setWordWrap(True)
        self._tesseract_path = find_tesseract()
        if self._tesseract_path:
            self.lbl_ocr_status.setText("Tesseract: Found")
            self.lbl_ocr_status.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_ocr_status.setText("Tesseract: Not found (install locally to enable)")
            self.lbl_ocr_status.setStyleSheet(f"color: {C['yellow']};")
        ocr_row.addWidget(self.lbl_ocr_status, 1)
        ocr_layout.addLayout(ocr_row)
        ocr_button_row = FlowLayout()
        self.btn_ocr_srt = QPushButton("Extract hardsubs to .srt")
        self.btn_ocr_srt.setObjectName("primaryBtn")
        self.btn_ocr_srt.setEnabled(False)
        self.btn_ocr_srt.clicked.connect(self._do_ocr_srt)
        ocr_button_row.addStretch()
        ocr_button_row.addWidget(self.btn_ocr_srt)
        ocr_layout.addLayout(ocr_button_row)
        layout.addWidget(ocr_grp)

        # LUT
        lut_grp = QGroupBox("LUT Color Grading")
        ll = FlowLayout(lut_grp)
        self.lbl_lut_file = QLabel("No LUT selected")
        self.lbl_lut_file.setProperty("class", "dimLabel")
        self.btn_browse_lut = QPushButton("Browse .cube")
        self.btn_browse_lut.clicked.connect(self._browse_lut)
        ll.addWidget(self.lbl_lut_file, 1)
        ll.addWidget(self.btn_browse_lut)
        layout.addWidget(lut_grp)
        self._lut_path = None

        audio_grp = QGroupBox("Audio Normalization")
        al = FlowLayout(audio_grp)
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
        sil_opts = FlowLayout()
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
        self.tbl_silence_markers = QTableWidget(0, 3)
        self.tbl_silence_markers.setAccessibleName("Reviewable silence markers")
        self.tbl_silence_markers.setHorizontalHeaderLabels(
            ["Remove", "Start (seconds)", "End (seconds)"]
        )
        self.tbl_silence_markers.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tbl_silence_markers.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.tbl_silence_markers.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.tbl_silence_markers.setMaximumHeight(150)
        self.tbl_silence_markers.itemChanged.connect(
            lambda _item: self._sync_silence_segments()
        )
        sil_layout.addWidget(self.tbl_silence_markers)
        sil_btn_row = FlowLayout()
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

        btn_row = FlowLayout()
        self.btn_reset_defaults = QPushButton("Reset to Defaults")
        self.btn_reset_defaults.setToolTip("Reset all filter settings to defaults")
        self.btn_reset_defaults.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self.btn_reset_defaults)
        btn_row.addStretch()
        self.btn_apply = QPushButton("Apply Filters")
        self.btn_apply.setObjectName("primaryBtn")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._do_apply)
        btn_row.addWidget(self.btn_apply)
        layout.addLayout(btn_row)
        layout.addStretch()
        for slider in self._sliders.values():
            slider.valueChanged.connect(lambda _value: self._refresh_filter_graph())
        for check in (
            self.chk_deinterlace,
            self.chk_denoise,
            self.chk_sharpen,
            self.chk_normalize,
        ):
            check.stateChanged.connect(lambda _state: self._refresh_filter_graph())
        self.cmb_loudness_target.currentTextChanged.connect(
            lambda _text: self._refresh_filter_graph()
        )
        self._refresh_filter_graph()

    def _reset_to_defaults(self):
        """Reset all filter settings to defaults."""
        self._reset_sliders()
        self.chk_stabilize.setChecked(False)
        self.chk_denoise.setChecked(False)
        self.chk_sharpen.setChecked(False)
        self.chk_deinterlace.setChecked(False)
        self.chk_normalize.setChecked(False)
        self._sub_path = None
        self.lbl_sub_file.setText("No subtitle selected")
        self._lut_path = None
        self.lbl_lut_file.setText("No LUT selected")
        self._restore_redaction_controls(normalize_redaction_state(DEFAULT_REDACTION))
        self.cmb_ocr_lang.setCurrentText("eng")
        self.spn_silence_db.setValue(-30)
        self.spn_silence_dur.setValue(0.5)
        self._silence_segments = []
        self._populate_silence_markers([])
        self._filter_stack_order = list(FILTER_STACK_DEFAULT)
        self._populate_filter_stack()
        self.btn_remove_silence.setEnabled(False)
        self.lbl_silence_result.setText("No scan yet")
        self.cmb_loudness_target.setCurrentIndex(0)
        self.lbl_preview_after.setText("Filtered")
        self.lbl_preview_after.setPixmap(QPixmap())
        self.requestToast.emit("Filter settings reset to defaults", C["blue"])

    def _reset_sliders(self):
        defaults = {"brightness": 0, "contrast": 0, "saturation": 100, "hue": 0, "gamma": 100}
        for name, slider in self._sliders.items():
            slider.setValue(defaults.get(name, 0))

    def _redaction_state_from_controls(self):
        return normalize_redaction_state({
            "enabled": self.chk_redaction.isChecked(),
            "start": self.spn_redaction_start.value(),
            "end": self.spn_redaction_end.value(),
            "blur_radius": self.spn_redaction_blur.value(),
            "keyframes": [
                {
                    "time": self.spn_redaction_start.value(),
                    "x": self.spn_redaction_start_x.value() / 100,
                    "y": self.spn_redaction_start_y.value() / 100,
                    "width": self.spn_redaction_start_w.value() / 100,
                    "height": self.spn_redaction_start_h.value() / 100,
                },
                {
                    "time": self.spn_redaction_end.value(),
                    "x": self.spn_redaction_end_x.value() / 100,
                    "y": self.spn_redaction_end_y.value() / 100,
                    "width": self.spn_redaction_end_w.value() / 100,
                    "height": self.spn_redaction_end_h.value() / 100,
                },
            ],
        })

    def _restore_redaction_controls(self, state):
        state = normalize_redaction_state(state)
        start = state["keyframes"][0]
        end = state["keyframes"][-1]
        controls = (
            (self.chk_redaction, state["enabled"]),
            (self.spn_redaction_start, state["start"]),
            (self.spn_redaction_start_x, start["x"] * 100),
            (self.spn_redaction_start_y, start["y"] * 100),
            (self.spn_redaction_start_w, start["width"] * 100),
            (self.spn_redaction_start_h, start["height"] * 100),
            (self.spn_redaction_end, state["end"]),
            (self.spn_redaction_end_x, end["x"] * 100),
            (self.spn_redaction_end_y, end["y"] * 100),
            (self.spn_redaction_end_w, end["width"] * 100),
            (self.spn_redaction_end_h, end["height"] * 100),
            (self.spn_redaction_blur, state["blur_radius"]),
        )
        for control, value in controls:
            control.blockSignals(True)
            if isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            else:
                control.setValue(value)
            control.blockSignals(False)
        self._refresh_filter_graph()

    def _browse_sub(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Subtitle File", "",
            "Subtitle Files (*.srt *.ass *.ssa *.vtt);;All Files (*)")
        if path:
            self._sub_path = path
            self.lbl_sub_file.setText(Path(path).name)
            self._refresh_filter_graph()

    def _browse_lut(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select LUT File", "",
            "LUT Files (*.cube *.3dl);;All Files (*)")
        if path:
            self._lut_path = path
            self.lbl_lut_file.setText(Path(path).name)
            self._refresh_filter_graph()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_apply.setEnabled(bool(FFMPEG))
        self.btn_preview.setEnabled(bool(FFMPEG))
        self.btn_gen_srt.setEnabled(bool(self._whisper_path))
        self.btn_ocr_srt.setEnabled(bool(self._tesseract_path and FFMPEG))
        self.btn_detect_silence.setEnabled(bool(FFMPEG))
        self._silence_segments = []
        self._populate_silence_markers([])
        self.btn_remove_silence.setEnabled(False)
        self.lbl_silence_result.setText("No scan yet")
        if self._ocr_worker and self._ocr_worker.isRunning():
            self._ocr_worker.cancel()
        self._ocr_worker = None
        if self._frame_worker and self._frame_worker.isRunning():
            self._frame_worker.cancel()
        self.lbl_preview_before.setText("Loading original preview…")
        worker = FrameExtractWorker(filepath, parent=self)
        self._frame_worker = worker
        worker.finished_signal.connect(self._on_source_preview_ready)
        worker.start()

    def project_state(self):
        """Return filter controls and reviewable silence markers."""
        return {
            "sliders": {name: control.value() for name, control in self._sliders.items()},
            "stabilize": self.chk_stabilize.isChecked(),
            "denoise": self.chk_denoise.isChecked(),
            "sharpen": self.chk_sharpen.isChecked(),
            "deinterlace": self.chk_deinterlace.isChecked(),
            "subtitle_path": self._sub_path or "",
            "lut_path": self._lut_path or "",
            "normalize": self.chk_normalize.isChecked(),
            "loudness_target": self.cmb_loudness_target.currentText(),
            "silence_threshold_db": self.spn_silence_db.value(),
            "silence_min_duration": self.spn_silence_dur.value(),
            "silence_segments": [
                [float(start), float(end)]
                for start, end in self._silence_segments
            ],
            "silence_markers": [
                {
                    "start": start,
                    "end": end,
                    "remove": remove,
                }
                for start, end, remove in self._silence_marker_rows()
            ],
            "filter_stack_order": list(self._filter_stack_order),
            "redaction": self._redaction_state_from_controls(),
            "ocr_language": self.cmb_ocr_lang.currentText(),
        }

    def restore_project_state(self, state):
        state = state if isinstance(state, dict) else {}
        for name, value in (state.get("sliders") or {}).items():
            control = self._sliders.get(name)
            if control is not None:
                control.setValue(int(value))
        for key, control in (
            ("stabilize", self.chk_stabilize),
            ("denoise", self.chk_denoise),
            ("sharpen", self.chk_sharpen),
            ("deinterlace", self.chk_deinterlace),
            ("normalize", self.chk_normalize),
        ):
            control.setChecked(bool(state.get(key, False)))
        targets = [self.cmb_loudness_target.itemText(i) for i in range(self.cmb_loudness_target.count())]
        if state.get("loudness_target") in targets:
            self.cmb_loudness_target.setCurrentText(state["loudness_target"])
        self.spn_silence_db.setValue(
            max(-60, min(-10, int(state.get("silence_threshold_db", -30) or -30)))
        )
        self.spn_silence_dur.setValue(
            max(0.1, min(10.0, float(state.get("silence_min_duration", 0.5) or 0.5)))
        )
        segments = []
        for item in (state.get("silence_segments") or [])[:500]:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                start, end = sorted((max(0.0, float(item[0])), max(0.0, float(item[1]))))
                if end > start:
                    segments.append((start, end))
        self._silence_segments = segments
        markers = []
        for item in (state.get("silence_markers") or []):
            if isinstance(item, dict):
                markers.append((item.get("start", 0), item.get("end", 0), bool(item.get("remove", True))))
        self._populate_silence_markers(markers or [(start, end, True) for start, end in segments])
        self.btn_remove_silence.setEnabled(bool(segments))
        self._restore_redaction_controls(state.get("redaction"))
        if state.get("ocr_language") in {
            self.cmb_ocr_lang.itemText(index)
            for index in range(self.cmb_ocr_lang.count())
        }:
            self.cmb_ocr_lang.setCurrentText(state["ocr_language"])
        self._filter_stack_order = normalize_filter_order(state.get("filter_stack_order"))
        self._populate_filter_stack()

    def _populate_filter_stack(self):
        if not hasattr(self, "lst_filter_stack"):
            return
        self.lst_filter_stack.blockSignals(True)
        self.lst_filter_stack.clear()
        for filter_id in normalize_filter_order(self._filter_stack_order):
            item = QListWidgetItem(FILTER_STACK_LABELS[filter_id])
            item.setData(Qt.ItemDataRole.UserRole, filter_id)
            self.lst_filter_stack.addItem(item)
        self.lst_filter_stack.blockSignals(False)
        self._on_filter_stack_changed()

    def _on_filter_stack_changed(self):
        if hasattr(self, "lst_filter_stack"):
            self._filter_stack_order = normalize_filter_order(
                [
                    self.lst_filter_stack.item(index).data(Qt.ItemDataRole.UserRole)
                    for index in range(self.lst_filter_stack.count())
                ]
            )
        self._refresh_filter_graph()

    def _move_filter_stack(self, delta):
        row = self.lst_filter_stack.currentRow()
        target = row + int(delta)
        if row < 0 or not 0 <= target < self.lst_filter_stack.count():
            return
        values = reorder_filter_stack(self._filter_stack_order, row, target)
        self._filter_stack_order = values
        self._populate_filter_stack()
        self.lst_filter_stack.setCurrentRow(target)

    def _refresh_filter_graph(self):
        required = (
            "lbl_filter_graph",
            "_sliders",
            "chk_deinterlace",
            "chk_denoise",
            "chk_sharpen",
            "chk_normalize",
            "_sub_path",
            "_lut_path",
            "chk_redaction",
        )
        if all(hasattr(self, name) for name in required):
            vf, af = self._build_filters(update_graph=False)
            self.lbl_filter_graph.setText(filter_graph(vf, af))
            if self.chk_redaction.isChecked() and build_redaction_filter(
                self._redaction_state_from_controls()
            ):
                self.lbl_redaction_status.setText(
                    "Enabled — tracked blur"
                )
            else:
                self.lbl_redaction_status.setText("Disabled — no redaction")

    def _silence_marker_rows(self):
        rows = []
        for row in range(self.tbl_silence_markers.rowCount()):
            remove_item = self.tbl_silence_markers.item(row, 0)
            start_item = self.tbl_silence_markers.item(row, 1)
            end_item = self.tbl_silence_markers.item(row, 2)
            try:
                start = max(0.0, float(start_item.text()))
                end = max(0.0, float(end_item.text()))
            except (AttributeError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            remove = bool(
                remove_item
                and remove_item.checkState() == Qt.CheckState.Checked
            )
            rows.append((start, end, remove))
        return rows

    def _sync_silence_segments(self):
        if not hasattr(self, "tbl_silence_markers"):
            return
        self._silence_segments = [
            (start, end)
            for start, end, remove in self._silence_marker_rows()
            if remove
        ]
        total = self.tbl_silence_markers.rowCount()
        if total:
            total_duration = sum(end - start for start, end in self._silence_segments)
            self.lbl_silence_result.setText(
                f"{len(self._silence_segments)} of {total} marker(s) selected "
                f"for removal ({format_duration_short(total_duration)}); "
                "edit times or uncheck rows before rendering"
            )
        self.btn_remove_silence.setEnabled(bool(self._silence_segments))

    def _populate_silence_markers(self, markers):
        self.tbl_silence_markers.blockSignals(True)
        self.tbl_silence_markers.setRowCount(0)
        for start, end, remove in markers[:500]:
            try:
                start = max(0.0, float(start))
                end = max(0.0, float(end))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            row = self.tbl_silence_markers.rowCount()
            self.tbl_silence_markers.insertRow(row)
            remove_item = QTableWidgetItem()
            remove_item.setFlags(
                remove_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            remove_item.setCheckState(
                Qt.CheckState.Checked if remove else Qt.CheckState.Unchecked
            )
            self.tbl_silence_markers.setItem(row, 0, remove_item)
            self.tbl_silence_markers.setItem(row, 1, QTableWidgetItem(f"{start:.3f}"))
            self.tbl_silence_markers.setItem(row, 2, QTableWidgetItem(f"{end:.3f}"))
        self.tbl_silence_markers.blockSignals(False)
        self._sync_silence_segments()

    def _on_source_preview_ready(self, filepath, pixmap):
        if filepath != self._filepath:
            return
        self._frame_worker = None
        if pixmap:
            scaled = pixmap.scaledToHeight(
                120,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_preview_before.setPixmap(scaled)
        else:
            self.lbl_preview_before.setText("Original preview unavailable")

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
        self._preview_worker = FFmpegWorker(
            cmd,
            0,
            parse_progress=False,
            output_path=self._preview_tmp.name,
            overwrite=True,
        )
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
            if e > s:
                self._silence_segments.append((s, e))
        count = len(self._silence_segments)
        if count == 0:
            self.lbl_silence_result.setText("No silent segments found")
            self.btn_remove_silence.setEnabled(False)
        else:
            total_dur = sum(e - s for s, e in self._silence_segments)
            self._populate_silence_markers(
                [(start, end, True) for start, end in self._silence_segments]
            )
            self.lbl_silence_result.setText(
                f"Found {count} silent segment(s) totaling "
                f"{format_duration_short(total_dur)}; review the markers before removal"
            )
            self.btn_remove_silence.setEnabled(True)

    def _do_remove_silence(self):
        self._sync_silence_segments()
        if not self._filepath or not self._silence_segments or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Without Silence", str(src.parent / f"{src.stem}_no_silence{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
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
        self._worker = FFmpegWorker(
            cmd,
            sum(e - s for s, e in keep_segments),
            output_path=out_path,
            overwrite=overwrite,
        )
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
        if not out_path or not _confirm_overwrite(self, out_path):
            return
        model = self.cmb_whisper_model.currentText().split(" (")[0]
        lang = self.cmb_whisper_lang.currentText()
        target_path = Path(out_path)
        try:
            caption_tmpdir = Path(tempfile.mkdtemp(
                prefix=".clipforge-caption-",
                dir=target_path.parent,
            ))
        except OSError as exc:
            self.requestToast.emit(f"Could not stage subtitles: {exc}", C["red"])
            return
        self._caption_tmpdir = caption_tmpdir
        generated_path = caption_tmpdir / f"{src.stem}.srt"
        cmd = [self._whisper_path, self._filepath,
               "--model", model, "--output_format", "srt",
               "--output_dir", str(caption_tmpdir)]
        if lang != "auto":
            cmd += ["--language", lang]
        self.console.append(f"[Auto-Caption] Generating subtitles with Whisper ({model})...\n")
        self.btn_gen_srt.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setRange(0, 0)
        self._worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(
            lambda ok, msg: self._on_caption_done(
                ok, msg, target_path, generated_path, caption_tmpdir
            ))
        self._worker.start()

    def _do_ocr_srt(self):
        if not self._filepath or not self._tesseract_path or not FFMPEG:
            self.requestToast.emit(
                "FFmpeg and local Tesseract are required for hardsub OCR",
                C["yellow"],
            )
            return
        src = Path(self._filepath)
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save OCR Subtitles",
            str(src.parent / f"{src.stem}.ocr.srt"),
            "SubRip subtitles (*.srt);;All Files (*)",
        )
        if not selected_path:
            return
        out_path = output_srt_path(selected_path)
        if not _confirm_overwrite(self, str(out_path)):
            return
        if self._ocr_worker and self._ocr_worker.isRunning():
            self.requestToast.emit("Hardsub OCR is already running", C["yellow"])
            return
        self._ocr_worker = OCRWorker(
            self._filepath,
            self._tesseract_path,
            self.cmb_ocr_lang.currentText(),
            parent=self,
        )
        self._ocr_output_path = out_path
        self._ocr_worker.progress.connect(self.progress.setValue)
        self._ocr_worker.log_output.connect(self.console.append)
        self._ocr_worker.finished_signal.connect(
            lambda ok, message, srt_text: self._on_ocr_done(
                ok, message, srt_text, out_path
            )
        )
        self.btn_ocr_srt.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lbl_progress_detail.setText("Sampling frames and reading hardsubs…")
        self._ocr_worker.start()

    def _on_ocr_done(self, ok, message, srt_text, out_path):
        self.btn_ocr_srt.setEnabled(bool(self._tesseract_path and FFMPEG))
        self.lbl_progress_detail.setText("")
        if not ok:
            self.requestToast.emit(f"Hardsub OCR failed: {message}", C["red"])
            return
        staged_path = None
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=out_path.parent,
                prefix=f".{out_path.stem}.clipforge-",
                suffix=".tmp",
                delete=False,
            ) as staged:
                staged_path = Path(staged.name)
                staged.write(srt_text)
                staged.flush()
                os.fsync(staged.fileno())
            os.replace(staged_path, out_path)
            self.progress.setValue(100)
            self.requestToast.emit(
                f"Hardsub subtitles exported: {out_path.name}",
                C["green"],
            )
        except OSError as exc:
            self.requestToast.emit(f"Could not write OCR subtitles: {exc}", C["red"])
        finally:
            if staged_path and staged_path.exists():
                try:
                    staged_path.unlink()
                except OSError:
                    pass

    def _on_caption_done(self, ok, msg, out_path, generated_path, caption_tmpdir):
        self.progress.setRange(0, 100)
        self.btn_gen_srt.setEnabled(True)
        try:
            if not ok:
                self.requestToast.emit(f"Caption generation failed: {msg}", C["red"])
                return
            if not generated_path.is_file() or generated_path.stat().st_size <= 0:
                self.requestToast.emit(
                    "Caption generation failed: Whisper produced no subtitle file",
                    C["red"],
                )
                return
            os.replace(generated_path, out_path)
            self.progress.setValue(100)
            self.requestToast.emit("Subtitles generated", C["green"])
            self._sub_path = str(out_path)
            self.lbl_sub_file.setText(out_path.name)
            if hasattr(self, "_refresh_filter_graph"):
                self._refresh_filter_graph()
        except OSError as exc:
            self.requestToast.emit(f"Caption generation failed: {exc}", C["red"])
        finally:
            shutil.rmtree(caption_tmpdir, ignore_errors=True)
            if self._caption_tmpdir == caption_tmpdir:
                self._caption_tmpdir = None

    def _build_filters(self, update_graph=True):
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
        for filter_id in normalize_filter_order(self._filter_stack_order):
            if filter_id == "color":
                if eq_parts:
                    vf.append(f"eq={':'.join(eq_parts)}")
                if h != 0:
                    vf.append(f"hue=h={h}")
            elif filter_id == "deinterlace" and self.chk_deinterlace.isChecked():
                vf.append("yadif")
            elif filter_id == "denoise" and self.chk_denoise.isChecked():
                vf.append("nlmeans")
            elif filter_id == "sharpen" and self.chk_sharpen.isChecked():
                vf.append("unsharp=5:5:1.0")
            elif filter_id == "lut" and self._lut_path:
                escaped = escape_ffmpeg_filter_value(self._lut_path)
                vf.append(f"lut3d=filename={escaped}")
            elif filter_id == "subtitles" and self._sub_path:
                escaped = escape_ffmpeg_filter_value(self._sub_path)
                vf.append(f"subtitles=filename={escaped}")
        redaction_filter = build_redaction_filter(self._redaction_state_from_controls())
        if redaction_filter:
            vf.append(redaction_filter)
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
        if update_graph and hasattr(self, "lbl_filter_graph"):
            self.lbl_filter_graph.setText(filter_graph(vf, af))
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
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        self._output_overwrite = os.path.exists(out_path)

        duration = self._info.get("duration", 0) if self._info else 0

        self.progress.setValue(0)
        self.btn_apply.setEnabled(False)

        if self.chk_stabilize.isChecked():
            self._stab_tmpdir = create_job_temp_dir("stabilize")
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

        self._worker = FFmpegWorker(
            cmd,
            duration,
            output_path=out_path,
            overwrite=getattr(self, "_output_overwrite", False),
        )
        self._worker.progress.connect(lambda v: self.progress.setValue(40 + int(v * 0.6)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _on_done(self, ok, msg, out_path):
        self.btn_apply.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if hasattr(self, "_stab_tmpdir"):
            shutil.rmtree(self._stab_tmpdir, ignore_errors=True)
            _unregister_temp_dir(self._stab_tmpdir)
            del self._stab_tmpdir
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Filters applied ({size})", C["green"])
        else:
            self.requestToast.emit(f"Filter failed: {msg}", C["red"])
