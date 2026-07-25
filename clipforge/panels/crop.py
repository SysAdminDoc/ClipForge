"""Crop & Rotate panel."""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QProgressBar, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from clipforge_utils import format_size

from ..constants import C
from ..tools import FFMPEG, _confirm_overwrite, extract_frame
from ..workers import FFmpegWorker
from ..widgets import CropView


class CropPanel(QWidget):
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
        self.crop_view = CropView()
        self.crop_view.setMinimumHeight(240)
        layout.addWidget(self.crop_view)

        grp = QGroupBox("Crop Region")
        gl = QHBoxLayout(grp)
        for label, attr in [("X:", "spn_x"), ("Y:", "spn_y"), ("W:", "spn_w"), ("H:", "spn_h")]:
            gl.addWidget(QLabel(label))
            spn = QSpinBox()
            spn.setRange(0, 99999)
            spn.valueChanged.connect(self._on_spin_changed)
            setattr(self, attr, spn)
            gl.addWidget(spn)
        gl.addWidget(QLabel("  Preset:"))
        self.cmb_aspect = QComboBox()
        self.cmb_aspect.addItems(["Free", "16:9", "9:16", "4:3", "1:1", "21:9"])
        self.cmb_aspect.currentTextChanged.connect(self._apply_preset)
        gl.addWidget(self.cmb_aspect)
        layout.addWidget(grp)

        rf_grp = QGroupBox("Rotate / Flip")
        rf_layout = QHBoxLayout(rf_grp)
        self.cmb_rotate = QComboBox()
        self.cmb_rotate.addItems(["No Rotation", "90 CW", "90 CCW", "180"])
        rf_layout.addWidget(QLabel("Rotate:"))
        rf_layout.addWidget(self.cmb_rotate)
        self.chk_hflip = QCheckBox("Horizontal Flip")
        self.chk_vflip = QCheckBox("Vertical Flip")
        rf_layout.addWidget(self.chk_hflip)
        rf_layout.addWidget(self.chk_vflip)
        rf_layout.addStretch()
        layout.addWidget(rf_grp)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = QHBoxLayout()
        self.btn_reset_defaults = QPushButton("Reset to Defaults")
        self.btn_reset_defaults.setToolTip("Reset crop region and rotation to defaults")
        self.btn_reset_defaults.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self.btn_reset_defaults)
        btn_row.addStretch()
        self.btn_crop = QPushButton("Crop / Transform Video")
        self.btn_crop.setObjectName("primaryBtn")
        self.btn_crop.setEnabled(False)
        self.btn_crop.clicked.connect(self._do_crop)
        btn_row.addWidget(self.btn_crop)
        layout.addLayout(btn_row)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_crop.setEnabled(bool(FFMPEG))
        w = info.get("width", 0) if info else 0
        h = info.get("height", 0) if info else 0
        for spn, mx, val in [(self.spn_x, w, 0), (self.spn_y, h, 0),
                              (self.spn_w, w, w), (self.spn_h, h, h)]:
            spn.blockSignals(True)
            spn.setMaximum(mx)
            spn.setValue(val)
            spn.blockSignals(False)
        pix = extract_frame(filepath, 0)
        if pix:
            self.crop_view.set_image(pix)

    def _reset_to_defaults(self):
        """Reset crop and rotation to defaults."""
        if self._info:
            w = self._info.get("width", 0)
            h = self._info.get("height", 0)
            self.spn_x.setValue(0)
            self.spn_y.setValue(0)
            self.spn_w.setValue(w)
            self.spn_h.setValue(h)
        self.cmb_rotate.setCurrentText("No Rotation")
        self.cmb_aspect.setCurrentText("Free")
        self.chk_hflip.setChecked(False)
        self.chk_vflip.setChecked(False)
        self.requestToast.emit("Crop settings reset to defaults", C["blue"])

    def _on_spin_changed(self):
        self.crop_view.set_crop_rect(self.spn_x.value(), self.spn_y.value(),
                                     self.spn_w.value(), self.spn_h.value())

    def _apply_preset(self, preset):
        if not self._info or preset == "Free":
            return
        vw, vh = self._info.get("width", 0), self._info.get("height", 0)
        if vh == 0 or vw == 0:
            return
        ratios = {"16:9": (16, 9), "9:16": (9, 16), "4:3": (4, 3), "1:1": (1, 1), "21:9": (21, 9)}
        rw, rh = ratios.get(preset, (16, 9))
        if vw / vh > rw / rh:
            new_h = vh
            new_w = int(vh * rw / rh)
        else:
            new_w = vw
            new_h = int(vw * rh / rw)
        new_w -= new_w % 2
        new_h -= new_h % 2
        self.spn_x.setValue((vw - new_w) // 2)
        self.spn_y.setValue((vh - new_h) // 2)
        self.spn_w.setValue(new_w)
        self.spn_h.setValue(new_h)

    def _do_crop(self):
        if not self._filepath or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Cropped Video", str(src.parent / f"{src.stem}_cropped{src.suffix}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi);;All Files (*)")
        if not out_path or not _confirm_overwrite(self, out_path, self._filepath):
            return
        overwrite = os.path.exists(out_path)
        x, y, w, h = self.spn_x.value(), self.spn_y.value(), self.spn_w.value(), self.spn_h.value()
        duration = self._info.get("duration", 0) if self._info else 0
        vf_parts = [f"crop={w}:{h}:{x}:{y}"]
        rot = self.cmb_rotate.currentText()
        if rot == "90 CW":
            vf_parts.append("transpose=1")
        elif rot == "90 CCW":
            vf_parts.append("transpose=2")
        elif rot == "180":
            vf_parts.append("transpose=1,transpose=1")
        if self.chk_hflip.isChecked():
            vf_parts.append("hflip")
        if self.chk_vflip.isChecked():
            vf_parts.append("vflip")
        cmd = [FFMPEG, "-y", "-i", self._filepath,
               "-vf", ",".join(vf_parts),
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-c:a", "copy", out_path]
        self.progress.setValue(0)
        self.btn_crop.setEnabled(False)
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

    def _on_done(self, ok, msg, out_path):
        self.btn_crop.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Crop complete  ({size})", C["green"])
        else:
            self.requestToast.emit(f"Crop failed: {msg}", C["red"])
