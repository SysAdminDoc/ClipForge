"""MainWindow class and application entry point."""

import sys
import os
import time
import json
from collections import deque
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStackedWidget, QTextEdit, QSplitter,
    QListWidget, QListWidgetItem, QScrollArea, QStatusBar,
    QComboBox, QFileDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QAction, QColor, QPalette, QDragEnterEvent, QDropEvent

from . import APP_NAME, APP_VERSION
from .constants import (
    C,
    C_HIGH_CONTRAST,
    C_MOCHA,
    WINDOW_TITLE,
    VIDEO_EXTS,
)
from .theme import stylesheet_for
from .i18n import catalog_for_environment, localize_widget_tree
from .project import (
    PROJECT_EXTENSION,
    ProjectError,
    build_project,
    load_project,
    resolve_project_input,
    save_project,
)
from .settings import (
    consume_persistence_notices,
    load_recent,
    load_settings,
    save_settings,
)
from .tools import (
    FFMPEG,
    HW_ENCODERS,
    HW_ENCODER_CAPABILITIES,
    _confirm_overwrite,
)
from . import tools as tools_module
from .diagnostics import DIAGNOSTICS, classify_severity
from .widgets import (
    Toast,
    FileInfoBar,
    VideoPlayer,
    ensure_accessible_control_names,
)
from .panels.trim import TrimPanel
from .panels.crop import CropPanel
from .panels.upscale import UpscalePanel
from .panels.convert import ConvertPanel
from .panels.filters import FiltersPanel
from .panels.audio import AudioPanel
from .panels.streams import StreamsPanel
from .panels.batch import BatchPanel
from .workers import CapabilityProbeWorker


def apply_application_theme(app, high_contrast=False):
    colors = C_HIGH_CONTRAST if high_contrast else C_MOCHA
    C.clear()
    C.update(colors)
    app.setStyleSheet(stylesheet_for(C))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(C["base"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(C["mantle"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(C["surface0"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(C["surface0"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(C["blue"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(C["crust"]))
    app.setPalette(palette)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._i18n = catalog_for_environment()
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(1150, 780)
        self._settings = load_settings()
        self._project_path = None
        self._project_dirty = False
        self._pending_project_state = None
        self._project_signature = None
        # Restore window geometry
        w = self._settings.get("window_width", 1340)
        h = self._settings.get("window_height", 860)
        self.resize(w, h)
        self.setAcceptDrops(True)
        self._setup_ui()
        localize_widget_tree(self, self._i18n)
        self._project_autosave_timer = QTimer(self)
        self._project_autosave_timer.setInterval(15000)
        self._project_autosave_timer.timeout.connect(self._autosave_project_if_needed)
        self._project_autosave_timer.start()
        self._check_deps()
        self._start_capability_probe()
        self._load_recent()
        self._show_persistence_notices()

    def _setup_ui(self):
        self._setup_project_menu()
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -- Sidebar --
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        title = QLabel(APP_NAME)
        title.setObjectName("sidebarTitle")
        sb_layout.addWidget(title)

        edit_label = QLabel("EDIT")
        edit_label.setObjectName("sectionLabel")
        sb_layout.addWidget(edit_label)

        self._nav_buttons = []
        nav_items = [
            ("Trim", "Cut segments from video"),
            ("Crop & Rotate", "Crop, rotate, flip video"),
            ("AI Enhance", "Upscale resolution, boost frame rate"),
            ("Convert", "Codec, format, resolution, speed"),
            ("Filters", "Color, stabilize, denoise, subtitles"),
            ("Audio", "Extract, replace, or remove audio"),
            ("Streams", "Inspect media info, remux streams"),
            ("Batch", "Process multiple files at once"),
        ]

        for i, (name, tooltip) in enumerate(nav_items):
            if i == 5:
                tools_label = QLabel("TOOLS")
                tools_label.setObjectName("sectionLabel")
                sb_layout.addWidget(tools_label)
            btn = QPushButton(f"  {name}")
            btn.setProperty("class", "navBtn")
            btn.setToolTip(tooltip)
            btn.setAccessibleName(f"{name} panel")
            btn.setAccessibleDescription(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._switch_panel(idx))
            sb_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        # Recent files
        recent_label = QLabel("RECENT")
        recent_label.setObjectName("sectionLabel")
        sb_layout.addWidget(recent_label)

        self.recent_list = QListWidget()
        self.recent_list.setAccessibleName("Recent files")
        self.recent_list.setMaximumHeight(150)
        self.recent_list.setStyleSheet(f"font-size: 11px; border: none; background: {C['mantle']};")
        self.recent_list.itemDoubleClicked.connect(self._on_recent_clicked)
        sb_layout.addWidget(self.recent_list)

        sb_layout.addStretch()

        self.btn_high_contrast = QPushButton("High contrast")
        self.btn_high_contrast.setCheckable(True)
        self.btn_high_contrast.setChecked(
            bool(self._settings.get("high_contrast", False))
        )
        self.btn_high_contrast.setAccessibleName("Toggle high contrast theme")
        self.btn_high_contrast.setToolTip(
            "Switch between the standard and high-contrast color themes"
        )
        self.btn_high_contrast.toggled.connect(self._toggle_high_contrast)
        sb_layout.addWidget(self.btn_high_contrast)

        # Dep status
        self.lbl_ffmpeg_status = QLabel()
        self.lbl_ffmpeg_status.setProperty("class", "dimLabel")
        self.lbl_ffmpeg_status.setContentsMargins(16, 0, 16, 2)
        sb_layout.addWidget(self.lbl_ffmpeg_status)

        # HW encoder status
        self.lbl_hw_status = QLabel()
        self.lbl_hw_status.setProperty("class", "dimLabel")
        self.lbl_hw_status.setContentsMargins(16, 0, 16, 4)
        sb_layout.addWidget(self.lbl_hw_status)
        self.lbl_hw_status.setText("GPU: Checking capabilities…")
        self.lbl_hw_status.setStyleSheet(f"color: {C['overlay0']};")

        main_layout.addWidget(sidebar)

        # -- Content area --
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 8, 12, 8)
        content_layout.setSpacing(6)

        # File info bar
        self.file_bar = FileInfoBar()
        self.file_bar.fileLoaded.connect(self._on_file_loaded)
        self.file_bar.fileLoadFailed.connect(self._on_file_load_failed)
        content_layout.addWidget(self.file_bar)

        # Main splitter: top (player + panels) / bottom (console)
        main_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top area: player left, panel right
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Video player
        self.player = VideoPlayer()
        self.player.setMinimumWidth(360)
        self.player.playbackError.connect(self._on_playback_error)
        top_splitter.addWidget(self.player)

        # Panel stack — Console with log filtering
        console_container = QWidget()
        console_vl = QVBoxLayout(console_container)
        console_vl.setContentsMargins(0, 0, 0, 0)
        console_vl.setSpacing(2)

        console_toolbar = QHBoxLayout()
        console_toolbar.setContentsMargins(4, 2, 4, 0)
        console_toolbar.setSpacing(6)
        lbl_log = QLabel("Console")
        lbl_log.setProperty("class", "dimLabel")
        console_toolbar.addWidget(lbl_log)
        self.cmb_log_filter = QComboBox()
        self.cmb_log_filter.addItems(["All", "Info", "Warning", "Error"])
        self.cmb_log_filter.setFixedWidth(90)
        self.cmb_log_filter.setToolTip("Filter log messages by level")
        self.cmb_log_filter.currentTextChanged.connect(self._filter_console)
        console_toolbar.addWidget(self.cmb_log_filter)
        console_toolbar.addStretch()
        self.btn_cancel_jobs = QPushButton("Cancel active jobs")
        self.btn_cancel_jobs.setToolTip(
            "Cancel every active media, inspection, preview, or install job"
        )
        self.btn_cancel_jobs.setAccessibleName("Cancel all active jobs")
        self.btn_cancel_jobs.setEnabled(False)
        self.btn_cancel_jobs.setFixedHeight(24)
        self.btn_cancel_jobs.clicked.connect(self._cancel_active_jobs)
        console_toolbar.addWidget(self.btn_cancel_jobs)
        btn_export_diagnostics = QPushButton("Export Diagnostics")
        btn_export_diagnostics.setToolTip(
            "Save bounded job diagnostics with file paths redacted; media is never included"
        )
        btn_export_diagnostics.clicked.connect(self._export_diagnostics)
        btn_export_diagnostics.setFixedHeight(24)
        console_toolbar.addWidget(btn_export_diagnostics)
        btn_copy_log_md = QPushButton("Copy as Markdown")
        btn_copy_log_md.setToolTip("Copy console output formatted as Markdown (for bug reports)")
        btn_copy_log_md.clicked.connect(self._copy_log_as_markdown)
        btn_copy_log_md.setFixedHeight(24)
        console_toolbar.addWidget(btn_copy_log_md)
        btn_clear_log = QPushButton("Clear")
        btn_clear_log.setToolTip("Clear console output")
        btn_clear_log.clicked.connect(lambda: (self.console.clear(), self._console_lines.clear()))
        btn_clear_log.setFixedHeight(24)
        console_toolbar.addWidget(btn_clear_log)
        console_vl.addLayout(console_toolbar)

        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)
        self.console.setPlaceholderText("FFmpeg output will appear here")
        self.console.document().setMaximumBlockCount(2000)
        console_vl.addWidget(self.console)

        # Store raw lines for filtering
        self._console_lines = deque(maxlen=2000)
        self._original_console_append = self.console.append

        def _tracked_append(text):
            text = str(text)
            severity = classify_severity(text)
            self._console_lines.append((severity, text))
            DIAGNOSTICS.event(severity, text)
            level_filter = self.cmb_log_filter.currentText()
            if self._line_matches_filter(text, level_filter):
                self._original_console_append(text)

        self.console.append = _tracked_append
        self.player.proxyLog.connect(self.console.append)
        self.player.proxyStatus.connect(self._on_proxy_status)

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(440)
        self.trim_panel = TrimPanel(self.console, self.player)
        self.crop_panel = CropPanel(self.console)
        self.upscale_panel = UpscalePanel(self.console)
        self.convert_panel = ConvertPanel(self.console)
        self.filters_panel = FiltersPanel(self.console)
        self.audio_panel = AudioPanel(self.console)
        self.streams_panel = StreamsPanel(self.console, self.player)
        self.batch_panel = BatchPanel(self.console)

        self._panels = [
            self.trim_panel,
            self.crop_panel,
            self.upscale_panel,
            self.convert_panel,
            self.filters_panel,
            self.audio_panel,
            self.streams_panel,
            self.batch_panel,
        ]
        for panel in self._panels:
            panel.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            ensure_accessible_control_names(panel)
            scroll = QScrollArea()
            scroll.setWidget(panel)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.stack.addWidget(scroll)

        top_splitter.addWidget(self.stack)
        top_splitter.setStretchFactor(0, 2)
        top_splitter.setStretchFactor(1, 3)
        top_splitter.setCollapsible(0, False)
        top_splitter.setCollapsible(1, False)
        top_splitter.setSizes([480, 520])
        self.top_splitter = top_splitter

        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(console_container)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 1)
        self.main_splitter = main_splitter

        content_layout.addWidget(main_splitter)
        main_layout.addWidget(content, 1)

        # Toast
        self.toast = Toast(self)

        # Connect toast signals
        for panel in self._panels:
            panel.requestToast.connect(self.toast.show_message)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"Ready  •  v{APP_VERSION}")
        ensure_accessible_control_names(self.player)
        ensure_accessible_control_names(self)
        self._worker_status_timer = QTimer(self)
        self._worker_status_timer.setInterval(200)
        self._worker_status_timer.timeout.connect(self._refresh_worker_status)
        self._worker_status_timer.start()

        # Default to Trim panel
        self._switch_panel(0)

    def _setup_project_menu(self):
        menu = self.menuBar().addMenu("Project")
        open_action = QAction("Open .cfproj…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_project)
        menu.addAction(open_action)
        save_action = QAction("Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        menu.addAction(save_action)
        save_as_action = QAction("Save Project As…", self)
        save_as_action.triggered.connect(self._save_project_as)
        menu.addAction(save_as_action)
        menu.addSeparator()
        policy_action = QAction("Project files store external media references", self)
        policy_action.setEnabled(False)
        menu.addAction(policy_action)

    def _project_payload(self):
        source = self.file_bar.filepath()
        inputs = [source] if source else []
        return build_project(
            inputs,
            project_path=self._project_path,
            media_info=self.file_bar.info() or {},
            trim=self.trim_panel.project_state(),
            filters=self.filters_panel.project_state(),
            preset=self.convert_panel.project_state(),
            active_panel=self.stack.currentIndex(),
            name=Path(self._project_path).stem if self._project_path else None,
        )

    @staticmethod
    def _project_signature_for(payload):
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    def _update_project_title(self):
        marker = "* " if self._project_dirty else ""
        name = Path(self._project_path).stem if self._project_path else "Untitled Project"
        self.setWindowTitle(f"{marker}{name} — {APP_NAME} v{APP_VERSION}")

    def _mark_project_dirty(self):
        self._project_dirty = True
        self._update_project_title()

    def _save_project_to(self, path):
        try:
            payload = self._project_payload()
            target = save_project(path, payload)
        except (OSError, ProjectError, ValueError) as exc:
            self.toast.show_message(f"Project save failed: {exc}", C["red"], 6000)
            return False
        self._project_path = target
        self._project_signature = self._project_signature_for(payload)
        self._project_dirty = False
        self._update_project_title()
        self.status_bar.showMessage(f"Project saved: {target.name}", 5000)
        self.toast.show_message(f"Project saved: {target.name}", C["green"], 4000)
        return True

    def _save_project(self):
        if self._project_path:
            return self._save_project_to(self._project_path)
        return self._save_project_as()

    def _save_project_as(self):
        source = self.file_bar.filepath()
        default_name = (
            Path(source).with_suffix(PROJECT_EXTENSION).name
            if source else f"ClipForge-project{PROJECT_EXTENSION}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save ClipForge Project",
            str(Path(source).parent / default_name) if source else default_name,
            "ClipForge Projects (*.cfproj);;Legacy ClipForge Projects (*.clipforge);;All Files (*)",
        )
        if not path:
            return False
        if Path(path).suffix.lower() not in {PROJECT_EXTENSION, ".clipforge"}:
            path += PROJECT_EXTENSION
        return self._save_project_to(path)

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open ClipForge Project",
            str(Path.home()),
            "ClipForge Projects (*.cfproj *.clipforge);;All Files (*)",
        )
        if path:
            return self._load_project_file(path)
        return False

    def _load_project_file(self, path):
        try:
            payload = load_project(path)
        except (OSError, ProjectError, ValueError) as exc:
            self.toast.show_message(f"Project open failed: {exc}", C["red"], 6000)
            return False
        if not payload.get("inputs"):
            self._project_path = Path(path).expanduser().resolve()
            self._pending_project_state = None
            self._project_dirty = False
            self._project_signature = self._project_signature_for(payload)
            self._update_project_title()
            self.status_bar.showMessage("Empty project opened", 5000)
            return True
        source = resolve_project_input(payload, path, payload.get("active_input", 0))
        if source is None:
            reference = payload.get("inputs", [{}])[payload.get("active_input", 0)]
            source, _ = QFileDialog.getOpenFileName(
                self,
                f"Relink {reference.get('name', 'project media')}",
                str(Path(path).parent),
                "Media Files (*.*);;All Files (*)",
            )
            if not source:
                self.toast.show_message(
                    "Project opened, but its media source is missing; use Open Video to relink it.",
                    C["yellow"],
                    6000,
                )
                return False
            source = Path(source)
        self._project_path = Path(path).expanduser().resolve()
        self._pending_project_state = payload
        self._project_dirty = False
        self._update_project_title()
        self.file_bar.load_file(str(source))
        return True

    def _apply_pending_project_state(self):
        payload = self._pending_project_state
        if not payload:
            return
        self.trim_panel.restore_project_state(payload.get("trim"))
        self.filters_panel.restore_project_state(payload.get("filters"))
        self.convert_panel.restore_project_state(payload.get("preset"))
        panel = payload.get("active_panel")
        if isinstance(panel, int) and 0 <= panel < len(self._panels):
            self._switch_panel(panel)
        self._pending_project_state = None
        self._project_dirty = False
        self._project_signature = self._project_signature_for(self._project_payload())
        self._update_project_title()

    def _autosave_project_if_needed(self):
        if not self._project_path or not self.file_bar.filepath():
            return
        try:
            payload = self._project_payload()
            signature = self._project_signature_for(payload)
        except (TypeError, ValueError):
            return
        if signature == self._project_signature:
            return
        if self._save_project_to(self._project_path):
            self.console.append("[INFO] Project autosaved with a recoverable backup.\n")

    @staticmethod
    def _line_matches_filter(text, level_filter):
        """Match exactly one structured severity, or all severities."""
        return level_filter == "All" or classify_severity(text) == level_filter.lower()

    def _filter_console(self, level):
        """Re-render console with only lines matching the selected level."""
        self.console.blockSignals(True)
        self.console.clear()
        for severity, line in self._console_lines:
            if level == "All" or severity == level.lower():
                self._original_console_append(line)
        self.console.blockSignals(False)

    def _copy_log_as_markdown(self):
        """Copy console output formatted as Markdown code block."""
        text = (
            "\n".join(line for _severity, line in self._console_lines)
            if self._console_lines
            else self.console.toPlainText()
        )
        md = f"```\n{text}\n```"
        QApplication.clipboard().setText(md)
        self.toast.show_message("Log copied as Markdown")

    def _export_diagnostics(self):
        default_path = (
            Path.home()
            / "Documents"
            / f"ClipForge-{APP_VERSION}-diagnostics.json"
        )
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Redacted Diagnostics",
            str(default_path),
            "JSON Diagnostics (*.json);;All Files (*)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".json"):
            output_path += ".json"
        if not _confirm_overwrite(self, output_path):
            return
        try:
            DIAGNOSTICS.event(
                "info",
                "Exported privacy-safe support diagnostics.",
                context={"paths_redacted": True, "media_contents_included": False},
            )
            DIAGNOSTICS.export(output_path, redact=True)
        except (OSError, TypeError, ValueError) as exc:
            self.toast.show_message(f"Diagnostics export failed: {exc}", C["red"], 5000)
            return
        self.toast.show_message(
            f"Redacted diagnostics saved: {Path(output_path).name}", C["green"], 5000
        )

    def _switch_panel(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setProperty("active", "true" if i == idx else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _show_persistence_notices(self):
        notices = consume_persistence_notices()
        for notice in notices:
            level = notice["level"].upper()
            message = notice["message"]
            self.console.append(f"[{level}] {message}\n")
        if notices:
            latest = notices[-1]
            color = C["red"] if latest["level"] == "error" else C["yellow"]
            self.toast.show_message(latest["message"], color, 8000)

    def _toggle_high_contrast(self, enabled):
        self._settings["high_contrast"] = bool(enabled)
        if not save_settings(self._settings):
            self._show_persistence_notices()
        apply_application_theme(QApplication.instance(), enabled)
        self._check_deps()
        self.toast.show_message(
            "High-contrast theme enabled" if enabled else "Standard theme enabled",
            C["green"],
        )

    def _check_deps(self):
        if FFMPEG:
            self.lbl_ffmpeg_status.setText("FFmpeg: Found")
            self.lbl_ffmpeg_status.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_ffmpeg_status.setText("FFmpeg: Missing")
            self.lbl_ffmpeg_status.setStyleSheet(f"color: {C['red']};")
            self.lbl_ffmpeg_status.setToolTip("FFmpeg is required for all operations")
            self.console.append(
                "FFmpeg was not found on this system.\n"
                "Most features require FFmpeg to be installed.\n\n"
                "Install options:\n"
                "  Windows:  winget install ffmpeg\n"
                "  macOS:    brew install ffmpeg\n"
                "  Linux:    sudo apt install ffmpeg\n\n"
                "Or download from https://ffmpeg.org/download.html\n\n"
            )

    def _start_capability_probe(self):
        if not FFMPEG:
            self.lbl_hw_status.setText("GPU: FFmpeg unavailable")
            return
        self._capability_worker = CapabilityProbeWorker(self, timeout=10)
        self._capability_worker.finished_signal.connect(
            self._on_capabilities_ready
        )
        self._capability_worker.start()

    def _on_capabilities_ready(self, result):
        if result.get("cancelled"):
            self.lbl_hw_status.setText("GPU: Capability check cancelled")
            self.lbl_hw_status.setStyleSheet(f"color: {C['yellow']};")
            return
        HW_ENCODERS.clear()
        HW_ENCODERS.update(result.get("encoders") or {})
        HW_ENCODER_CAPABILITIES.clear()
        HW_ENCODER_CAPABILITIES.update(result.get("encoder_capabilities") or {})
        tools_module.FFMPEG_VERSION_OUTPUT = result.get("version") or ""
        tools_module.CUDA_NVDEC_SAFE = bool(result.get("nvdec_safe"))
        self.convert_panel.refresh_hw_encoders()
        usable = [
            label for label, encoder in HW_ENCODERS.items()
            if HW_ENCODER_CAPABILITIES.get(encoder, {}).get("status") == "usable"
        ]
        if usable:
            self.lbl_hw_status.setText(
                f"GPU: {len(usable)}/{len(HW_ENCODERS)} encoder(s) usable"
            )
            self.lbl_hw_status.setToolTip(", ".join(usable))
            self.lbl_hw_status.setStyleSheet(f"color: {C['green']};")
        elif HW_ENCODERS:
            reasons = [
                f"{label}: {HW_ENCODER_CAPABILITIES.get(encoder, {}).get('reason', 'probe unavailable')}"
                for label, encoder in HW_ENCODERS.items()
            ]
            self.lbl_hw_status.setText("GPU: advertised encoders unavailable")
            self.lbl_hw_status.setToolTip("\n".join(reasons))
            self.lbl_hw_status.setStyleSheet(f"color: {C['yellow']};")
        else:
            self.lbl_hw_status.setText("GPU: No advertised encoders")
            self.lbl_hw_status.setToolTip(
                "FFmpeg did not advertise a supported hardware encoder"
            )
            self.lbl_hw_status.setStyleSheet(f"color: {C['overlay0']};")

    @staticmethod
    def _threads_in(value):
        if isinstance(value, QThread):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from MainWindow._threads_in(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from MainWindow._threads_in(item)

    def _active_workers(self):
        owners = [self, self.file_bar, self.player, *self._panels]
        workers = []
        seen = set()
        for owner in owners:
            for value in vars(owner).values():
                for worker in self._threads_in(value):
                    if id(worker) in seen or not worker.isRunning():
                        continue
                    seen.add(id(worker))
                    workers.append(worker)
        return workers

    def _refresh_worker_status(self):
        count = len(self._active_workers())
        self.btn_cancel_jobs.setEnabled(count > 0)
        self.btn_cancel_jobs.setText(
            f"Cancel {count} active job{'s' if count != 1 else ''}"
            if count
            else "Cancel active jobs"
        )

    def _cancel_active_jobs(self):
        workers = self._active_workers()
        for worker in workers:
            if hasattr(worker, "cancel"):
                worker.cancel()
            else:
                worker.requestInterruption()
        if workers:
            self.btn_cancel_jobs.setEnabled(False)
            self.status_bar.showMessage(
                f"Cancelling {len(workers)} active job(s)…",
                8000,
            )
            self.console.append(
                f"[INFO] Cancellation requested for {len(workers)} active job(s).\n"
            )

    def _load_recent(self):
        self.recent_list.clear()
        for path in load_recent():
            name = Path(path).name
            if len(name) > 28:
                name = name[:25] + "..."
            item = QListWidgetItem(name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.recent_list.addItem(item)

    def _on_recent_clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.exists(path):
            self.file_bar.load_file(path)

    def _on_file_loaded(self, filepath, info):
        self.player.load(filepath, info)
        self.trim_panel.load_file(filepath, info)
        self.crop_panel.load_file(filepath, info)
        self.upscale_panel.load_file(filepath, info)
        self.convert_panel.load_file(filepath, info)
        self.filters_panel.load_file(filepath, info)
        self.audio_panel.load_file(filepath, info)
        self.streams_panel.load_file(filepath, info)
        self.console.append(f"Loaded: {filepath}\n")
        self.status_bar.showMessage(f"Loaded: {Path(filepath).name}")
        if self._pending_project_state:
            self._apply_pending_project_state()
        else:
            self._mark_project_dirty()
        self._load_recent()
        self._show_persistence_notices()

    def _on_file_load_failed(self, filepath, message):
        name = Path(filepath).name if filepath else "media"
        self.console.append(f"[ERROR] Could not load {name}: {message}\n")
        self.status_bar.showMessage(f"Load failed: {name}")
        self.toast.show_message(f"Could not load {name}", C["red"], 5000)

    def _on_playback_error(self, message):
        self.console.append(f"[WARNING] Preview unavailable: {message}\n")
        self.status_bar.showMessage("Preview unavailable; FFmpeg tools remain available")
        self.toast.show_message("Preview unavailable; see player details", C["yellow"], 5000)

    def _on_proxy_status(self, ok, message):
        severity = "INFO" if ok else "WARNING"
        self.console.append(f"[{severity}] {message}\n")
        self.status_bar.showMessage(message, 8000)
        self.toast.show_message(
            "Proxy ready" if ok else "Proxy generation stopped",
            C["green"] if ok else C["yellow"],
            5000,
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in VIDEO_EXTS:
                        event.acceptProposedAction()
                        return

    def dropEvent(self, event: QDropEvent):
        files = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                f = url.toLocalFile()
                if Path(f).suffix.lower() in VIDEO_EXTS:
                    files.append(f)
        if files:
            if len(files) > 1 or self.stack.currentIndex() == 7:  # batch panel index
                self._switch_panel(7)
                self.batch_panel.add_paths(files)
            else:
                self.file_bar.load_file(files[0])

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def closeEvent(self, event):
        if self._project_path:
            self._autosave_project_if_needed()
        if hasattr(self, "_project_autosave_timer"):
            self._project_autosave_timer.stop()
        self._worker_status_timer.stop()
        self.player.release()
        active_workers = self._active_workers()
        for worker in active_workers:
            if hasattr(worker, "cancel"):
                worker.cancel()
            else:
                worker.requestInterruption()
        deadline = time.monotonic() + 8
        for worker in active_workers:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms:
                worker.wait(remaining_ms)
        stubborn = [worker for worker in active_workers if worker.isRunning()]
        for worker in stubborn:
            message = (
                f"Forced cleanup for unresponsive {type(worker).__name__} "
                "after cancellation timeout"
            )
            DIAGNOSTICS.event(
                "error",
                message,
                context={"component": "shutdown", "forced_cleanup": True},
            )
            self.console.append(f"[ERROR] {message}\n")
            worker.terminate()
            worker.wait(2000)
        still_running = [worker for worker in stubborn if worker.isRunning()]
        if still_running:
            self._worker_status_timer.start()
            self.toast.show_message(
                "Could not stop every background job; close cancelled",
                C["red"],
                8000,
            )
            event.ignore()
            return
        # Save window geometry
        self._settings["window_width"] = self.width()
        self._settings["window_height"] = self.height()
        if not save_settings(self._settings):
            for notice in consume_persistence_notices():
                DIAGNOSTICS.event(
                    notice["level"],
                    notice["message"],
                    context={"component": "settings"},
                )
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    apply_application_theme(app, load_settings().get("high_contrast", False))

    window = MainWindow()
    window.show()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.file_bar.load_file(sys.argv[1])

    sys.exit(app.exec())
