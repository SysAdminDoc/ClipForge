"""MainWindow class and application entry point."""

import sys
import os
from collections import deque
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStackedWidget, QTextEdit, QSplitter,
    QListWidget, QListWidgetItem, QScrollArea, QStatusBar,
    QComboBox, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette, QDragEnterEvent, QDropEvent

from . import APP_NAME, APP_VERSION
from .constants import (
    C,
    C_HIGH_CONTRAST,
    C_MOCHA,
    WINDOW_TITLE,
    VIDEO_EXTS,
)
from .theme import stylesheet_for
from .settings import (
    consume_persistence_notices,
    load_recent,
    load_settings,
    save_settings,
)
from .tools import FFMPEG, HW_ENCODERS, _confirm_overwrite
from .diagnostics import DIAGNOSTICS, classify_severity
from .widgets import Toast, FileInfoBar, VideoPlayer
from .panels.trim import TrimPanel
from .panels.crop import CropPanel
from .panels.upscale import UpscalePanel
from .panels.convert import ConvertPanel
from .panels.filters import FiltersPanel
from .panels.audio import AudioPanel
from .panels.streams import StreamsPanel
from .panels.batch import BatchPanel


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
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(1150, 780)
        self._settings = load_settings()
        # Restore window geometry
        w = self._settings.get("window_width", 1340)
        h = self._settings.get("window_height", 860)
        self.resize(w, h)
        self.setAcceptDrops(True)
        self._setup_ui()
        self._check_deps()
        self._load_recent()
        self._show_persistence_notices()

    def _setup_ui(self):
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
        if HW_ENCODERS:
            hw_names = ", ".join(HW_ENCODERS.keys())
            self.lbl_hw_status.setText(f"GPU: {len(HW_ENCODERS)} encoder(s)")
            self.lbl_hw_status.setToolTip(hw_names)
            self.lbl_hw_status.setStyleSheet(f"color: {C['green']};")
        else:
            self.lbl_hw_status.setText("GPU: No HW encoders")
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
            scroll = QScrollArea()
            scroll.setWidget(panel)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.stack.addWidget(scroll)

        top_splitter.addWidget(self.stack)
        top_splitter.setStretchFactor(0, 2)
        top_splitter.setStretchFactor(1, 3)

        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(console_container)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 1)

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

        # Default to Trim panel
        self._switch_panel(0)

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
                for f in files:
                    self.batch_panel._items.append(f)
                    self.batch_panel.file_list.addItem(Path(f).name)
            else:
                self.file_bar.load_file(files[0])

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.player.release()
        active_workers = []
        seen_workers = set()
        for panel in self._panels:
            for value in vars(panel).values():
                if (
                    id(value) not in seen_workers
                    and hasattr(value, "isRunning")
                    and value.isRunning()
                ):
                    seen_workers.add(id(value))
                    active_workers.append(value)
        for worker in active_workers:
            if hasattr(worker, "cancel"):
                worker.cancel()
        for worker in active_workers:
            worker.wait(5000)
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
