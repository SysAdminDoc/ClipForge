"""Upscale & Frame Interpolation panel."""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QComboBox, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size

from ..constants import C
from ..tools import FFMPEG, find_realesrgan, find_rife, find_span
from ..workers import UpscaleWorker, InterpolateWorker


class UpscalePanel(QWidget):
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

        grp = QGroupBox("AI Upscale")
        gl = QVBoxLayout(grp)
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Engine:"))
        self.cmb_engine = QComboBox()
        self.cmb_engine.addItems(["Real-ESRGAN (quality)", "SPAN (fast)"])
        self.cmb_engine.currentTextChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self.cmb_engine)
        engine_row.addWidget(QLabel("Scale:"))
        self.cmb_scale = QComboBox()
        self.cmb_scale.addItems(["2x", "3x", "4x"])
        self.cmb_scale.currentTextChanged.connect(self._update_output_res)
        engine_row.addWidget(self.cmb_scale)
        engine_row.addStretch()
        gl.addLayout(engine_row)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.cmb_model = QComboBox()
        self.cmb_model.addItems(["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3"])
        model_row.addWidget(self.cmb_model)
        self.lbl_output_res = QLabel("")
        self.lbl_output_res.setProperty("class", "accentLabel")
        model_row.addStretch()
        model_row.addWidget(self.lbl_output_res)
        gl.addLayout(model_row)
        layout.addWidget(grp)

        interp_grp = QGroupBox("Frame Interpolation (RIFE)")
        il = QHBoxLayout(interp_grp)
        il.addWidget(QLabel("Multiplier:"))
        self.cmb_interp = QComboBox()
        self.cmb_interp.addItems(["2x (double fps)", "4x (quadruple fps)", "8x"])
        self.cmb_interp.currentTextChanged.connect(lambda: self._update_interp_info())
        il.addWidget(self.cmb_interp)
        il.addWidget(QLabel("Model:"))
        self.cmb_rife_model = QComboBox()
        self.cmb_rife_model.addItems(["rife-v4.25", "rife-v4.6", "rife-v4.22", "rife-v4"])
        il.addWidget(self.cmb_rife_model)
        self.lbl_interp_info = QLabel("")
        self.lbl_interp_info.setProperty("class", "accentLabel")
        il.addStretch()
        il.addWidget(self.lbl_interp_info)
        layout.addWidget(interp_grp)

        info_grp = QGroupBox("Dependencies")
        info_l = QVBoxLayout(info_grp)
        self.lbl_esrgan = QLabel("Checking Real-ESRGAN...")
        self.lbl_rife = QLabel("Checking RIFE...")
        self.lbl_esrgan.setProperty("class", "dimLabel")
        self.lbl_rife.setProperty("class", "dimLabel")
        info_l.addWidget(self.lbl_esrgan)
        info_l.addWidget(self.lbl_rife)
        layout.addWidget(info_grp)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("dangerBtn")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_upscale = QPushButton("Upscale Video")
        self.btn_upscale.setObjectName("primaryBtn")
        self.btn_upscale.setEnabled(False)
        self.btn_upscale.clicked.connect(self._do_upscale)
        self.btn_interp = QPushButton("Interpolate Frames")
        self.btn_interp.setObjectName("successBtn")
        self.btn_interp.setEnabled(False)
        self.btn_interp.clicked.connect(self._do_interpolate)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_interp)
        btn_row.addWidget(self.btn_upscale)
        layout.addLayout(btn_row)
        layout.addStretch()

        self._check_tools()

    def _check_tools(self):
        if find_realesrgan():
            self.lbl_esrgan.setText("Real-ESRGAN: Found")
            self.lbl_esrgan.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_esrgan.setText("Real-ESRGAN: Not found - download from github.com/xinntao/Real-ESRGAN/releases")
            self.lbl_esrgan.setStyleSheet(f"color: {C['yellow']};")
        if find_span():
            self.lbl_esrgan.setText(self.lbl_esrgan.text() + "  |  SPAN: Found")
        if find_rife():
            self.lbl_rife.setText("RIFE: Found")
            self.lbl_rife.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_rife.setText("RIFE: Not found - download from github.com/nihui/rife-ncnn-vulkan/releases")
            self.lbl_rife.setStyleSheet(f"color: {C['yellow']};")

    def _on_engine_changed(self, text):
        if "SPAN" in text:
            self.cmb_model.clear()
            self.cmb_model.addItems(["spanx4_ch48", "spanx2_ch48", "ClearRealityV1"])
            self.cmb_scale.clear()
            self.cmb_scale.addItems(["2x", "4x"])
        else:
            self.cmb_model.clear()
            self.cmb_model.addItems(["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3"])
            self.cmb_scale.clear()
            self.cmb_scale.addItems(["2x", "3x", "4x"])

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_upscale.setEnabled(bool(FFMPEG))
        self.btn_interp.setEnabled(bool(FFMPEG))
        self._update_output_res()
        self._update_interp_info()

    def _update_output_res(self):
        if not self._info:
            return
        scale = int(self.cmb_scale.currentText().replace("x", ""))
        w = self._info.get("width", 0) * scale
        h = self._info.get("height", 0) * scale
        self.lbl_output_res.setText(f"Output: {w}x{h}")

    def _update_interp_info(self):
        if not self._info:
            return
        fps = self._info.get("fps", 30)
        mult = int(self.cmb_interp.currentText().split("x")[0])
        self.lbl_interp_info.setText(f"{fps} fps -> {fps * mult} fps")

    def _do_upscale(self):
        if not self._filepath:
            return
        scale = int(self.cmb_scale.currentText().replace("x", ""))
        model = self.cmb_model.currentText()
        engine = "span" if "SPAN" in self.cmb_engine.currentText() else "realesrgan"
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Upscaled Video", str(src.parent / f"{src.stem}_{scale}x{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path:
            return
        self.progress.setValue(0)
        self._set_processing(True)
        self._worker = UpscaleWorker(self._filepath, out_path, scale, model, engine=engine)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _do_interpolate(self):
        if not self._filepath:
            return
        mult = int(self.cmb_interp.currentText().split("x")[0])
        src = Path(self._filepath)
        fps = self._info.get("fps", 30) if self._info else 30
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Interpolated Video",
            str(src.parent / f"{src.stem}_{int(fps * mult)}fps{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov);;All Files (*)")
        if not out_path:
            return
        self.progress.setValue(0)
        self._set_processing(True)
        rife_model = self.cmb_rife_model.currentText()
        self._worker = InterpolateWorker(self._filepath, out_path, mult, model=rife_model)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()

    def _set_processing(self, active):
        self.btn_upscale.setEnabled(not active)
        self.btn_interp.setEnabled(not active)
        self.btn_cancel.setVisible(active)

    def _on_done(self, ok, msg, out_path):
        self._set_processing(False)
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Complete  ({size}) - {msg}", C["green"])
        else:
            self.requestToast.emit(f"Failed: {msg}", C["red"])
