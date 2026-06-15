#!/usr/bin/env python3
"""
ClipForge Desktop v0.4.0 - All-in-One Video Editor
Trim, Crop, Upscale, Convert, Filters, Batch, Audio, Presets - One Tool, Zero Hassle
"""

import sys
import os
import subprocess
import json
import shutil
import tempfile
import re
import time as _time
import glob as _glob
import atexit
from pathlib import Path

_active_temp_dirs = []

def _register_temp_dir(path):
    _active_temp_dirs.append(path)

def _unregister_temp_dir(path):
    if path in _active_temp_dirs:
        _active_temp_dirs.remove(path)

def _cleanup_temp_dirs():
    for d in list(_active_temp_dirs):
        shutil.rmtree(d, ignore_errors=True)
    _active_temp_dirs.clear()
    for d in Path(tempfile.gettempdir()).glob("clipforge_*"):
        if d.is_dir():
            try:
                shutil.rmtree(d)
            except OSError:
                pass

atexit.register(_cleanup_temp_dirs)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _bootstrap():
    deps = {
        "PyQt6": "PyQt6",
        "PyQt6.QtMultimedia": "PyQt6-Multimedia",
        "PyQt6.QtMultimediaWidgets": "PyQt6-Multimedia",
    }
    missing = []
    for mod, pkg in deps.items():
        try:
            __import__(mod)
        except ImportError:
            if pkg not in missing:
                missing.append(pkg)
    if missing:
        print(f"[ClipForge] Installing: {', '.join(missing)}")
        pip_args = [sys.executable, "-m", "pip", "install", *missing]
        in_venv = (hasattr(sys, "real_prefix")
                   or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix))
        if not in_venv:
            pip_args.append("--user")
        subprocess.check_call(
            pip_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

_bootstrap()

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QComboBox, QSpinBox, QFileDialog,
    QStackedWidget, QTextEdit, QProgressBar, QSplitter,
    QGroupBox, QCheckBox, QDoubleSpinBox,
    QGraphicsView, QGraphicsScene,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QScrollArea, QStatusBar, QGridLayout, QLineEdit,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QUrl, QTimer, QPointF, QSize,
    QPropertyAnimation, QEasingCurve, QRect,
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QPixmap,
    QDragEnterEvent, QDropEvent, QPalette,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "ClipForge"
APP_VERSION = "0.4.0"
WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"
CONFIG_DIR = Path.home() / ".clipforge"
RECENT_FILE = CONFIG_DIR / "recent.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PRESETS_DIR = CONFIG_DIR / "presets"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv",
              ".m4v", ".ts", ".mpg", ".mpeg", ".gif", ".m2ts", ".vob")
AUDIO_EXTS = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus")
SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

C_MOCHA = {
    "crust":    "#11111b", "mantle":   "#181825", "base":     "#1e1e2e",
    "surface0": "#313244", "surface1": "#45475a", "surface2": "#585b70",
    "overlay0": "#6c7086", "overlay1": "#7f849c", "text":     "#cdd6f4",
    "subtext0": "#a6adc8", "subtext1": "#bac2de", "blue":     "#89b4fa",
    "green":    "#a6e3a1", "red":      "#f38ba8", "mauve":    "#cba6f7",
    "peach":    "#fab387", "yellow":   "#f9e2af", "teal":     "#94e2d5",
    "lavender": "#b4befe", "pink":     "#f5c2e7", "sky":      "#89dceb",
    "flamingo": "#f2cdcd", "rosewater":"#f5e0dc", "sapphire": "#74c7ec",
}

C_HIGH_CONTRAST = {
    "crust":    "#000000", "mantle":   "#0a0a0a", "base":     "#1a1a1a",
    "surface0": "#2a2a2a", "surface1": "#3a3a3a", "surface2": "#4a4a4a",
    "overlay0": "#6a6a6a", "overlay1": "#8a8a8a", "text":     "#ffffff",
    "subtext0": "#d0d0d0", "subtext1": "#e0e0e0", "blue":     "#5dade2",
    "green":    "#58d68d", "red":      "#ec7063", "mauve":    "#bb8fce",
    "peach":    "#f0b27a", "yellow":   "#f7dc6f", "teal":     "#76d7c4",
    "lavender": "#a9cce3", "pink":     "#f5b7b1", "sky":      "#85c1e9",
    "flamingo": "#fadbd8", "rosewater":"#fdebd0", "sapphire": "#5499c7",
}

def _load_theme():
    try:
        s = load_settings()
        if s.get("high_contrast"):
            return C_HIGH_CONTRAST
    except (OSError, ValueError):
        pass
    return C_MOCHA

C = _load_theme()

# Built-in social media / device presets
BUILTIN_PRESETS = {
    "YouTube 1080p": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                      "crf": 18, "preset": "medium", "resolution": "1920x1080 (1080p)",
                      "fps": "Original", "speed": 1.0},
    "YouTube 4K": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                   "crf": 18, "preset": "medium", "resolution": "3840x2160 (4K)",
                   "fps": "Original", "speed": 1.0},
    "Instagram Reel (9:16)": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                              "crf": 20, "preset": "medium", "resolution": "1080x1920",
                              "fps": "30", "speed": 1.0},
    "TikTok": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
               "crf": 20, "preset": "medium", "resolution": "1080x1920",
               "fps": "30", "speed": 1.0},
    "Discord (8MB)": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                      "crf": 28, "preset": "medium", "resolution": "1280x720 (720p)",
                      "fps": "30", "speed": 1.0},
    "Discord (50MB)": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                       "crf": 22, "preset": "medium", "resolution": "1920x1080 (1080p)",
                       "fps": "Original", "speed": 1.0},
    "Twitter/X": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                  "crf": 22, "preset": "medium", "resolution": "1920x1080 (1080p)",
                  "fps": "30", "speed": 1.0},
    "Archive (Lossless)": {"container": "MKV", "vcodec": "H.264 (libx264)", "acodec": "FLAC",
                           "crf": 0, "preset": "veryslow", "resolution": "Original",
                           "fps": "Original", "speed": 1.0},
    "Web Optimized": {"container": "MP4", "vcodec": "H.264 (libx264)", "acodec": "AAC",
                      "crf": 23, "preset": "fast", "resolution": "1280x720 (720p)",
                      "fps": "30", "speed": 1.0},
    "GIF": {"container": "GIF", "vcodec": "H.264 (libx264)", "acodec": "None (remove audio)",
            "crf": 18, "preset": "medium", "resolution": "Original", "fps": "15", "speed": 1.0},
    "Web AV1": {"container": "MP4", "vcodec": "SVT-AV1 (libsvtav1)", "acodec": "Opus",
                "crf": 30, "preset": "6", "resolution": "1920x1080 (1080p)",
                "fps": "Original", "speed": 1.0},
    "AV1 High Quality": {"container": "MKV", "vcodec": "SVT-AV1 (libsvtav1)", "acodec": "Opus",
                         "crf": 24, "preset": "4", "resolution": "Original",
                         "fps": "Original", "speed": 1.0},
}

# ---------------------------------------------------------------------------
# FFmpeg / Tool detection
# ---------------------------------------------------------------------------

def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    common = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        Path("C:/ffmpeg/bin"), Path("C:/Program Files/ffmpeg/bin"),
        Path(os.environ.get("USERPROFILE", "")) / "scoop" / "shims",
    ]
    for d in common:
        for f in [d / f"{name}.exe", d / name]:
            if f.exists():
                return str(f)
    for d in common:
        if d.exists():
            for f in d.rglob(f"{name}.exe"):
                return str(f)
    return None

def find_realesrgan():
    name = "realesrgan-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None

def find_rife():
    name = "rife-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None

def find_span():
    name = "span-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None

FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")

def detect_hw_encoders():
    """Detect available hardware encoders from FFmpeg."""
    if not FFMPEG:
        return {}
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        hw = {}
        for name, label in [
            ("h264_nvenc", "H.264 NVENC (NVIDIA)"),
            ("hevc_nvenc", "H.265 NVENC (NVIDIA)"),
            ("h264_qsv", "H.264 QSV (Intel)"),
            ("hevc_qsv", "H.265 QSV (Intel)"),
            ("h264_amf", "H.264 AMF (AMD)"),
            ("hevc_amf", "H.265 AMF (AMD)"),
            ("av1_nvenc", "AV1 NVENC (NVIDIA)"),
            ("av1_qsv", "AV1 QSV (Intel)"),
            ("av1_amf", "AV1 AMF (AMD)"),
        ]:
            if name in result.stdout:
                hw[label] = name
        return hw
    except (OSError, subprocess.TimeoutExpired):
        return {}

HW_ENCODERS = detect_hw_encoders()

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def load_settings():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}

def save_settings(settings):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except OSError:
        pass

# ---------------------------------------------------------------------------
# Preset persistence
# ---------------------------------------------------------------------------

def load_user_presets():
    try:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        presets = {}
        for f in PRESETS_DIR.glob("*.json"):
            try:
                presets[f.stem] = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        return presets
    except OSError:
        return {}

def save_user_preset(name, data):
    try:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_preset_name(name)
        (PRESETS_DIR / f"{safe_name}.json").write_text(json.dumps(data, indent=2))
        return safe_name
    except OSError:
        return None

def delete_user_preset(name):
    try:
        p = PRESETS_DIR / f"{name}.json"
        if p.exists():
            p.unlink()
    except OSError:
        pass

# ---------------------------------------------------------------------------
# Recent files
# ---------------------------------------------------------------------------

def load_recent():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if RECENT_FILE.exists():
            return json.loads(RECENT_FILE.read_text())[:10]
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return []

def save_recent(paths):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps(paths[:10]))
    except OSError:
        pass

def add_recent(filepath):
    recent = load_recent()
    if filepath in recent:
        recent.remove(filepath)
    recent.insert(0, filepath)
    save_recent(recent[:10])

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {C['base']};
    color: {C['text']};
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    font-size: 13px;
}}

/* ── Sidebar ── */
#sidebar {{
    background-color: {C['mantle']};
    border-right: 1px solid {C['surface0']};
    min-width: 220px;
    max-width: 220px;
}}
#sidebarTitle {{
    color: {C['lavender']};
    font-size: 17px;
    font-weight: 700;
    padding: 18px 16px 14px 16px;
    letter-spacing: 0.3px;
}}
#sectionLabel {{
    color: {C['overlay0']};
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    padding: 16px 16px 6px 16px;
    letter-spacing: 1.2px;
}}
.navBtn {{
    background: transparent;
    color: {C['subtext0']};
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0px;
    padding: 9px 16px 9px 13px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
    margin: 0px 0px;
}}
.navBtn:hover {{
    background-color: {C['surface0']};
    color: {C['text']};
    border-left: 3px solid {C['surface1']};
}}
.navBtn:focus {{
    border-left: 3px solid {C['blue']};
    color: {C['text']};
    outline: none;
}}
.navBtn[active="true"] {{
    background-color: {C['surface0']};
    color: {C['lavender']};
    font-weight: 600;
    border-left: 3px solid {C['lavender']};
}}

/* ── Cards / GroupBox ── */
QGroupBox {{
    border: 1px solid {C['surface0']};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px;
    padding-top: 30px;
    font-weight: 600;
    color: {C['subtext1']};
    background-color: transparent;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: {C['lavender']};
    font-size: 12px;
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: {C['surface1']};
    border-color: {C['surface2']};
}}
QPushButton:pressed {{
    background-color: {C['surface2']};
}}
QPushButton:focus {{
    border-color: {C['blue']};
    outline: none;
}}
QPushButton:disabled {{
    background-color: {C['surface0']};
    color: {C['overlay0']};
    border-color: {C['surface0']};
}}
QPushButton#primaryBtn {{
    background-color: {C['blue']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#primaryBtn:hover {{
    background-color: {C['lavender']};
}}
QPushButton#primaryBtn:pressed {{
    background-color: {C['sapphire']};
}}
QPushButton#primaryBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton#dangerBtn {{
    background-color: {C['red']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
}}
QPushButton#dangerBtn:hover {{
    background-color: {C['flamingo']};
}}
QPushButton#dangerBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton#successBtn {{
    background-color: {C['green']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
}}
QPushButton#successBtn:hover {{
    background-color: #b8eeac;
}}
QPushButton#successBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton.playerBtn {{
    background-color: transparent;
    border: none;
    color: {C['subtext0']};
    padding: 6px 10px;
    font-size: 13px;
    font-weight: 600;
    min-width: 34px;
    border-radius: 6px;
}}
QPushButton.playerBtn:hover {{
    color: {C['text']};
    background-color: {C['surface0']};
}}
QPushButton.playerBtn:focus {{
    color: {C['lavender']};
    outline: none;
}}

/* ── Inputs ── */
QComboBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 28px;
    font-size: 12px;
}}
QComboBox:focus {{
    border-color: {C['blue']};
}}
QComboBox:disabled {{
    color: {C['overlay0']};
    background-color: {C['mantle']};
    border-color: {C['surface0']};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C['subtext0']};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    selection-background-color: {C['surface1']};
    selection-color: {C['lavender']};
    outline: none;
    padding: 4px;
}}
QSpinBox, QDoubleSpinBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 28px;
    font-size: 12px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {C['blue']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {C['surface1']};
    border: none;
    width: 20px;
    border-radius: 3px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {C['surface2']};
}}
QLineEdit {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 28px;
    font-size: 12px;
    selection-background-color: {C['blue']};
    selection-color: {C['crust']};
}}
QLineEdit:focus {{
    border-color: {C['blue']};
}}
QLineEdit:disabled {{
    color: {C['overlay0']};
    background-color: {C['mantle']};
}}

/* ── Sliders ── */
QSlider::groove:horizontal {{
    background: {C['surface0']};
    height: 5px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C['lavender']};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {C['blue']};
}}
QSlider::handle:horizontal:pressed {{
    background: {C['sapphire']};
}}
QSlider::sub-page:horizontal {{
    background: {C['blue']};
    border-radius: 2px;
}}

/* ── Progress ── */
QProgressBar {{
    background-color: {C['surface0']};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {C['blue']};
    border-radius: 5px;
}}

/* ── Console ── */
#console {{
    background-color: {C['crust']};
    color: {C['overlay1']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
    padding: 10px;
}}

/* ── Labels ── */
QLabel {{ color: {C['text']}; }}
.dimLabel {{ color: {C['subtext0']}; font-size: 11px; }}
.accentLabel {{ color: {C['lavender']}; font-weight: 600; font-size: 13px; }}

/* ── Splitters ── */
QSplitter::handle {{ background-color: {C['surface0']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: transparent; width: 8px; border-radius: 4px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {C['surface1']}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {C['surface2']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 8px; border-radius: 4px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C['surface1']}; border-radius: 4px; min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {C['surface2']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Checkboxes ── */
QCheckBox {{ color: {C['text']}; spacing: 8px; font-size: 12px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 2px solid {C['surface2']}; background: {C['surface0']};
}}
QCheckBox::indicator:hover {{
    border-color: {C['overlay0']};
}}
QCheckBox::indicator:checked {{
    background: {C['blue']}; border-color: {C['blue']};
}}
QCheckBox::indicator:disabled {{
    background: {C['mantle']}; border-color: {C['surface0']};
}}

/* ── File Info Bar ── */
#fileInfoBar {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    padding: 10px 14px;
}}

/* ── Lists ── */
QListWidget {{
    background-color: {C['mantle']};
    color: {C['text']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    outline: none;
    padding: 4px;
    font-size: 12px;
}}
QListWidget::item {{
    padding: 7px 10px;
    border-radius: 5px;
}}
QListWidget::item:selected {{
    background-color: {C['surface0']};
    color: {C['lavender']};
}}
QListWidget::item:hover {{
    background-color: {C['surface0']};
}}

/* ── Toast ── */
#toast {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 10px;
    padding: 12px 24px;
    font-weight: 500;
    font-size: 13px;
}}

/* ── Status Bar ── */
QStatusBar {{
    background-color: {C['mantle']};
    color: {C['subtext0']};
    border-top: 1px solid {C['surface0']};
    font-size: 11px;
    padding: 3px 12px;
}}

/* ── Video Player ── */
#videoPlayer {{
    background-color: {C['crust']};
    border-radius: 8px;
}}
#playerControls {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    padding: 6px 10px;
}}
#thumbnailStrip {{
    background-color: {C['crust']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    min-height: 48px;
    max-height: 48px;
}}

/* ── Command Preview ── */
#cmdPreview {{
    background-color: {C['crust']};
    color: {C['teal']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
    padding: 10px;
    selection-background-color: {C['surface1']};
}}
#progressDetail {{
    color: {C['subtext0']};
    font-size: 11px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
}}

/* ── Streams ── */
#streamItem {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 8px 12px;
}}

/* ── Filter Sliders ── */
#filterSlider QSlider::groove:horizontal {{
    height: 4px;
}}

/* ── Tooltips ── */
QToolTip {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
}}
"""

# ---------------------------------------------------------------------------
# ffprobe cache
# ---------------------------------------------------------------------------

_probe_cache = {}

def _probe_cache_key(filepath):
    try:
        stat = os.stat(filepath)
        return (filepath, stat.st_size, stat.st_mtime)
    except OSError:
        return None

# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def probe_video(filepath):
    if not FFPROBE:
        return None
    cache_key = _probe_cache_key(filepath)
    if cache_key and cache_key in _probe_cache:
        return _probe_cache[cache_key]
    try:
        cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        data = json.loads(result.stdout)
        info = {"path": filepath, "streams": []}
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration", 0))
        info["size"] = int(fmt.get("size", 0))
        info["format_name"] = fmt.get("format_name", "unknown")
        info["bit_rate"] = int(fmt.get("bit_rate", 0))
        info["tags"] = fmt.get("tags", {})
        for s in data.get("streams", []):
            si = {
                "index": s.get("index", 0),
                "codec_type": s.get("codec_type"),
                "codec_name": s.get("codec_name"),
                "codec_long_name": s.get("codec_long_name", ""),
            }
            if s.get("codec_type") == "video":
                si["width"] = s.get("width", 0)
                si["height"] = s.get("height", 0)
                si["fps"] = _parse_fps(s.get("r_frame_rate", "0/1"))
                si["pix_fmt"] = s.get("pix_fmt", "")
                si["bit_rate"] = int(s.get("bit_rate", 0))
                si["profile"] = s.get("profile", "")
                si["color_space"] = s.get("color_space", "")
                si["color_transfer"] = s.get("color_transfer", "")
                info["width"] = si["width"]
                info["height"] = si["height"]
                info["fps"] = si["fps"]
                info["pix_fmt"] = si["pix_fmt"]
            elif s.get("codec_type") == "audio":
                si["sample_rate"] = s.get("sample_rate", "")
                si["channels"] = s.get("channels", 0)
                si["channel_layout"] = s.get("channel_layout", "")
                si["bit_rate"] = int(s.get("bit_rate", 0))
                info["audio_codec"] = s.get("codec_name", "")
                info["audio_channels"] = s.get("channels", 0)
                info["audio_sample_rate"] = s.get("sample_rate", "")
            elif s.get("codec_type") == "subtitle":
                si["language"] = s.get("tags", {}).get("language", "")
                si["title"] = s.get("tags", {}).get("title", "")
            info["streams"].append(si)
        if cache_key:
            _probe_cache[cache_key] = info
        return info
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError):
        return None

from clipforge_utils import (
    _parse_fps, format_duration, format_duration_short,
    format_size, format_bitrate, estimate_output_size,
    _sanitize_preset_name, validate_media_path,
)

def extract_frame(filepath, time_sec=0):
    if not FFMPEG:
        return None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        cmd = [FFMPEG, "-y", "-ss", str(time_sec), "-i", filepath,
               "-frames:v", "1", "-q:v", "2", tmp.name]
        subprocess.run(cmd, capture_output=True, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            pix = QPixmap(tmp.name)
            os.unlink(tmp.name)
            return pix
        os.unlink(tmp.name)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

def _kill_process_tree(proc):
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass

_FFMPEG_ERROR_PATTERNS = [
    (r"No such file or directory", "Input file not found"),
    (r"Permission denied", "Permission denied — check file/folder permissions"),
    (r"No space left on device", "Disk full — free space and retry"),
    (r"Unknown encoder", "Encoder not available — your FFmpeg build may lack this codec"),
    (r"Encoder .+ not found", "Encoder not available — your FFmpeg build may lack this codec"),
    (r"Unknown decoder", "Decoder not available for this input format"),
    (r"Invalid data found when processing input", "Input file is corrupt or unsupported"),
    (r"Output file is empty", "Encoding produced no output — check settings"),
    (r"does not support the audio codec", "Container does not support the chosen audio codec"),
    (r"does not support the video codec", "Container does not support the chosen video codec"),
    (r"Avi duration\|Error", "AVI container limit exceeded"),
]

def _parse_ffmpeg_error(stderr_text):
    for pattern, msg in _FFMPEG_ERROR_PATTERNS:
        if re.search(pattern, stderr_text, re.IGNORECASE):
            return msg
    return None

class FFmpegWorker(QThread):
    progress = pyqtSignal(float)
    log_output = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    speed_info = pyqtSignal(str)

    def __init__(self, cmd, duration=0, parse_progress=True, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.duration = duration
        self.parse_progress = parse_progress
        self._cancelled = False
        self._start_time = 0
        self._stderr_buffer = []

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._start_time = _time.time()
            self.log_output.emit(f"$ {' '.join(self.cmd)}\n")
            popen_kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(self.cmd, **popen_kwargs)
            ema_speed = 0
            for line in process.stderr:
                if self._cancelled:
                    _kill_process_tree(process)
                    self.finished_signal.emit(False, "Cancelled")
                    return
                self.log_output.emit(line)
                self._stderr_buffer.append(line)
                if not self.parse_progress:
                    continue
                match = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)", line)
                if match and self.duration > 0:
                    h, m, s = float(match.group(1)), float(match.group(2)), float(match.group(3))
                    current = h * 3600 + m * 60 + s
                    pct = min(current / self.duration * 100, 100)
                    self.progress.emit(pct)
                    # Speed + ETA calculation
                    elapsed = _time.time() - self._start_time
                    if elapsed > 0.5 and current > 0:
                        speed_x = current / elapsed
                        ema_speed = speed_x if ema_speed == 0 else ema_speed * 0.7 + speed_x * 0.3
                        remaining = (self.duration - current) / max(ema_speed, 0.01)
                        fps_match = re.search(r"fps=\s*([\d.]+)", line)
                        fps_str = f"{float(fps_match.group(1)):.1f} fps" if fps_match else ""
                        size_match = re.search(r"size=\s*(\d+\w+)", line)
                        size_str = size_match.group(1) if size_match else ""
                        eta_str = format_duration_short(remaining)
                        parts = [p for p in [fps_str, f"{ema_speed:.1f}x", f"ETA: {eta_str}", size_str] if p]
                        self.speed_info.emit(" | ".join(parts))
            process.wait()
            elapsed = _time.time() - self._start_time
            if process.returncode == 0:
                self.progress.emit(100)
                self.finished_signal.emit(True, f"Complete ({format_duration_short(elapsed)})")
            else:
                stderr_text = "".join(self._stderr_buffer)
                friendly = _parse_ffmpeg_error(stderr_text) if self.parse_progress else None
                if friendly:
                    self.finished_signal.emit(False, friendly)
                else:
                    last_lines = stderr_text.strip().split("\n")[-3:]
                    hint = " | ".join(l.strip() for l in last_lines if l.strip())[:200]
                    self.finished_signal.emit(False, hint or f"Process exited with code {process.returncode}")
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class ThumbnailWorker(QThread):
    """Extract thumbnail frames in background."""
    thumbnails_ready = pyqtSignal(list)  # list of QPixmap

    def __init__(self, filepath, count=12, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.count = count

    def run(self):
        if not FFMPEG:
            return
        info = probe_video(self.filepath)
        if not info:
            return
        duration = info.get("duration", 0)
        if duration <= 0:
            return
        thumbs = []
        for i in range(self.count):
            t = duration * i / self.count
            pix = extract_frame(self.filepath, t)
            if pix:
                thumbs.append(pix.scaledToHeight(44, Qt.TransformationMode.SmoothTransformation))
            else:
                thumbs.append(QPixmap())
        self.thumbnails_ready.emit(thumbs)


class UpscaleWorker(QThread):
    progress = pyqtSignal(float)
    log_output = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, input_path, output_path, scale=2, model="realesrgan-x4plus", engine="realesrgan", parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.scale = scale
        self.model = model
        self.engine = engine
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self.engine == "span":
            upscaler = find_span()
            upscaler_name = "SPAN"
            upscaler_url = "github.com/TNTwise/SPAN-ncnn-vulkan/releases"
        else:
            upscaler = find_realesrgan()
            upscaler_name = "Real-ESRGAN"
            upscaler_url = "github.com/xinntao/Real-ESRGAN/releases"
        if not upscaler:
            self.log_output.emit(
                f"[ERROR] {upscaler_name} not found.\n"
                f"Download: https://{upscaler_url}\n"
                f"Place in ClipForge directory or add to PATH.\n"
            )
            self.finished_signal.emit(False, f"{upscaler_name} not found")
            return
        if not FFMPEG:
            self.finished_signal.emit(False, "FFmpeg not found")
            return
        try:
            tmpdir = tempfile.mkdtemp(prefix="clipforge_upscale_")
            _register_temp_dir(tmpdir)
            frames_dir = os.path.join(tmpdir, "frames")
            upscaled_dir = os.path.join(tmpdir, "upscaled")
            os.makedirs(frames_dir)
            os.makedirs(upscaled_dir)
            info = probe_video(self.input_path)
            if not info:
                self.finished_signal.emit(False, "Could not probe video")
                return
            fps = info.get("fps", 30)

            self.log_output.emit("[1/3] Extracting frames...\n")
            subprocess.run(
                [FFMPEG, "-y", "-i", self.input_path, "-qscale:v", "2",
                 os.path.join(frames_dir, "frame_%06d.jpg")],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            frames = sorted(Path(frames_dir).glob("*.jpg"))
            total = len(frames)
            if total == 0:
                self.finished_signal.emit(False, "No frames extracted")
                return
            self.log_output.emit(f"  Extracted {total} frames\n")
            self.progress.emit(10)

            self.log_output.emit(f"[2/3] Upscaling with {upscaler_name}...\n")
            cmd_up = [upscaler, "-i", frames_dir, "-o", upscaled_dir,
                      "-n", self.model, "-s", str(self.scale), "-f", "jpg"]
            self.log_output.emit(f"$ {' '.join(cmd_up)}\n")
            proc = subprocess.Popen(
                cmd_up, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in proc.stderr:
                if self._cancelled:
                    _kill_process_tree(proc)
                    self.finished_signal.emit(False, "Cancelled")
                    return
                self.log_output.emit(line)
                m = re.search(r"(\d+\.\d+)%", line)
                if m:
                    self.progress.emit(10 + float(m.group(1)) * 0.7)
            proc.wait()
            if proc.returncode != 0:
                self.finished_signal.emit(False, f"{upscaler_name} failed")
                return
            self.progress.emit(80)

            self.log_output.emit("[3/3] Reassembling video...\n")
            audio_path = os.path.join(tmpdir, "audio.aac")
            subprocess.run(
                [FFMPEG, "-y", "-i", self.input_path, "-vn", "-acodec", "copy", audio_path],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            has_audio = os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
            cmd_re = [FFMPEG, "-y", "-framerate", str(fps),
                      "-i", os.path.join(upscaled_dir, "frame_%06d.jpg")]
            if has_audio:
                cmd_re += ["-i", audio_path]
            cmd_re += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd_re += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
            cmd_re.append(self.output_path)
            subprocess.run(cmd_re, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.progress.emit(100)
            if os.path.exists(self.output_path):
                self.finished_signal.emit(True, "Upscale complete")
            else:
                self.finished_signal.emit(False, "Output file not created")
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if 'tmpdir' in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)
                _unregister_temp_dir(tmpdir)


class InterpolateWorker(QThread):
    """Frame interpolation using rife-ncnn-vulkan."""
    progress = pyqtSignal(float)
    log_output = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, input_path, output_path, multiplier=2, model="rife-v4.25", parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.multiplier = multiplier
        self.model = model
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        rife = find_rife()
        if not rife:
            self.log_output.emit(
                "[ERROR] rife-ncnn-vulkan not found.\n"
                "Download: https://github.com/nihui/rife-ncnn-vulkan/releases\n"
                "Place in ClipForge directory or add to PATH.\n"
            )
            self.finished_signal.emit(False, "RIFE not found")
            return
        if not FFMPEG:
            self.finished_signal.emit(False, "FFmpeg not found")
            return
        try:
            tmpdir = tempfile.mkdtemp(prefix="clipforge_interp_")
            _register_temp_dir(tmpdir)
            frames_dir = os.path.join(tmpdir, "frames")
            interp_dir = os.path.join(tmpdir, "interpolated")
            os.makedirs(frames_dir)
            os.makedirs(interp_dir)
            info = probe_video(self.input_path)
            if not info:
                self.finished_signal.emit(False, "Could not probe video")
                return
            fps = info.get("fps", 30)
            new_fps = fps * self.multiplier

            self.log_output.emit("[1/3] Extracting frames...\n")
            subprocess.run(
                [FFMPEG, "-y", "-i", self.input_path, "-qscale:v", "2",
                 os.path.join(frames_dir, "frame_%06d.png")],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            frames = sorted(Path(frames_dir).glob("*.png"))
            if len(frames) == 0:
                self.finished_signal.emit(False, "No frames extracted")
                return
            self.log_output.emit(f"  Extracted {len(frames)} frames\n")
            self.progress.emit(15)

            self.log_output.emit(f"[2/3] Interpolating {self.multiplier}x with RIFE...\n")
            cmd_rife = [rife, "-i", frames_dir, "-o", interp_dir,
                        "-m", self.model, "-n", str(len(frames) * self.multiplier)]
            self.log_output.emit(f"$ {' '.join(cmd_rife)}\n")
            proc = subprocess.Popen(
                cmd_rife, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in proc.stderr:
                if self._cancelled:
                    _kill_process_tree(proc)
                    self.finished_signal.emit(False, "Cancelled")
                    return
                self.log_output.emit(line)
                m = re.search(r"(\d+\.\d+)%", line)
                if m:
                    self.progress.emit(15 + float(m.group(1)) * 0.65)
            proc.wait()
            if proc.returncode != 0:
                self.finished_signal.emit(False, "RIFE failed")
                return
            self.progress.emit(80)

            self.log_output.emit("[3/3] Reassembling video...\n")
            audio_path = os.path.join(tmpdir, "audio.aac")
            subprocess.run(
                [FFMPEG, "-y", "-i", self.input_path, "-vn", "-acodec", "copy", audio_path],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            has_audio = os.path.exists(audio_path) and os.path.getsize(audio_path) > 0

            interp_frames = sorted(Path(interp_dir).glob("*.png"))
            if not interp_frames:
                interp_frames = sorted(Path(interp_dir).glob("*.jpg"))
            ext = interp_frames[0].suffix if interp_frames else ".png"

            cmd_re = [FFMPEG, "-y", "-framerate", str(new_fps),
                      "-i", os.path.join(interp_dir, f"%06d{ext}")]
            if has_audio:
                cmd_re += ["-i", audio_path]
            cmd_re += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd_re += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
            cmd_re.append(self.output_path)
            subprocess.run(cmd_re, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            self.progress.emit(100)
            if os.path.exists(self.output_path):
                self.finished_signal.emit(True, f"Interpolation complete ({fps} -> {new_fps} fps)")
            else:
                self.finished_signal.emit(False, "Output file not created")
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if 'tmpdir' in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)
                _unregister_temp_dir(tmpdir)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class Toast(QLabel):
    """Auto-dismissing notification overlay with slide animation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(44)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text, color=None, duration=3000):
        c = color or C["green"]
        self.setText(text)
        self.setStyleSheet(
            f"background: {C['surface0']}; color: {c}; border: 1px solid {C['surface1']}; "
            f"border-radius: 8px; padding: 10px 24px; font-weight: 600; font-size: 13px;"
        )
        if self.parent():
            pw = self.parent().width()
            sidebar_w = 220
            content_w = pw - sidebar_w
            toast_w = min(content_w - 40, 500)
            self.setFixedWidth(toast_w)
            target_x = sidebar_w + (content_w - toast_w) // 2
            self.move(target_x, -50)
            self.show()
            self.raise_()
            # Slide-down animation
            self._anim = QPropertyAnimation(self, b"geometry")
            self._anim.setDuration(300)
            self._anim.setStartValue(QRect(target_x, -50, toast_w, 44))
            self._anim.setEndValue(QRect(target_x, 8, toast_w, 44))
            self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self._anim.start()
        else:
            self.show()
            self.raise_()
        self._timer.start(duration)

    def _fade_out(self):
        if self.parent():
            self._anim_out = QPropertyAnimation(self, b"geometry")
            self._anim_out.setDuration(200)
            self._anim_out.setStartValue(self.geometry())
            end = QRect(self.geometry())
            end.moveTop(-50)
            self._anim_out.setEndValue(end)
            self._anim_out.setEasingCurve(QEasingCurve.Type.InQuad)
            self._anim_out.finished.connect(self.hide)
            self._anim_out.start()
        else:
            self.hide()


class RangeSlider(QWidget):
    rangeChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = 0.0
        self._max = 1.0
        self._low = 0.0
        self._high = 1.0
        self._pressed = None
        self.setMinimumHeight(36)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_range(self, low, high):
        self._low = max(self._min, min(low, self._high))
        self._high = min(self._max, max(high, self._low))
        self.update()
        self.rangeChanged.emit(self._low, self._high)

    def low(self):
        return self._low

    def high(self):
        return self._high

    def _val_to_x(self, val):
        w = self.width() - 20
        return 10 + (val - self._min) / max(self._max - self._min, 0.001) * w

    def _x_to_val(self, x):
        w = self.width() - 20
        return self._min + (x - 10) / max(w, 1) * (self._max - self._min)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        track_y = h // 2 - 3
        track_h = 6
        p.setBrush(QColor(C["surface0"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(10, track_y, self.width() - 20, track_h, 3, 3)
        x_low = self._val_to_x(self._low)
        x_high = self._val_to_x(self._high)
        p.setBrush(QColor(C["blue"]))
        p.drawRoundedRect(int(x_low), track_y, int(x_high - x_low), track_h, 3, 3)
        for val in [self._low, self._high]:
            x = self._val_to_x(val)
            p.setBrush(QColor(C["lavender"]))
            p.setPen(QPen(QColor(C["surface0"]), 2))
            p.drawEllipse(QPointF(x, h / 2), 8, 8)
        p.end()

    def mousePressEvent(self, event):
        x = event.position().x()
        if abs(x - self._val_to_x(self._low)) < abs(x - self._val_to_x(self._high)):
            self._pressed = "low"
        else:
            self._pressed = "high"
        self._update_from_mouse(x)

    def mouseMoveEvent(self, event):
        if self._pressed:
            self._update_from_mouse(event.position().x())

    def mouseReleaseEvent(self, event):
        self._pressed = None

    def _update_from_mouse(self, x):
        val = max(self._min, min(self._max, self._x_to_val(x)))
        if self._pressed == "low":
            self._low = min(val, self._high - 0.001)
        elif self._pressed == "high":
            self._high = max(val, self._low + 0.001)
        self.update()
        self.rangeChanged.emit(self._low, self._high)


class ThumbnailStrip(QWidget):
    """Displays thumbnail filmstrip under the seek bar."""
    clicked = pyqtSignal(float)  # position ratio 0-1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("thumbnailStrip")
        self._thumbnails = []
        self._position = 0.0
        self.setMinimumHeight(48)
        self.setMaximumHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_thumbnails(self, thumbs):
        self._thumbnails = thumbs
        self.update()

    def set_position(self, ratio):
        self._position = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, event):
        if not self._thumbnails:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = self.width()
        h = self.height()
        n = len(self._thumbnails)
        tw = w / n
        for i, thumb in enumerate(self._thumbnails):
            if not thumb.isNull():
                x = int(i * tw)
                p.drawPixmap(x, 0, int(tw), h, thumb)
        # Draw position indicator
        px = int(self._position * w)
        p.setPen(QPen(QColor(C["lavender"]), 2))
        p.drawLine(px, 0, px, h)
        # Small triangle at top
        p.setBrush(QColor(C["lavender"]))
        p.setPen(Qt.PenStyle.NoPen)
        from PyQt6.QtGui import QPolygonF
        tri = QPolygonF([QPointF(px - 5, 0), QPointF(px + 5, 0), QPointF(px, 6)])
        p.drawPolygon(tri)
        p.end()

    def mousePressEvent(self, event):
        ratio = event.position().x() / max(self.width(), 1)
        self.clicked.emit(max(0.0, min(1.0, ratio)))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            ratio = event.position().x() / max(self.width(), 1)
            self.clicked.emit(max(0.0, min(1.0, ratio)))


class CropView(QGraphicsView):
    cropChanged = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = None
        self._crop_rect = None
        self._img_w = 0
        self._img_h = 0
        self.setStyleSheet(f"background: {C['crust']}; border: 1px solid {C['surface0']}; border-radius: 6px;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_image(self, pixmap):
        self._scene.clear()
        if pixmap and not pixmap.isNull():
            self._img_w = pixmap.width()
            self._img_h = pixmap.height()
            self._pixmap_item = self._scene.addPixmap(pixmap)
            self._scene.setSceneRect(0, 0, self._img_w, self._img_h)
            pen = QPen(QColor(C["blue"]), 2, Qt.PenStyle.DashLine)
            self._crop_rect = self._scene.addRect(0, 0, self._img_w, self._img_h, pen)
            self._crop_rect.setFlag(self._crop_rect.GraphicsItemFlag.ItemIsMovable, True)
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.update()

    def get_crop(self):
        if not self._crop_rect:
            return (0, 0, self._img_w, self._img_h)
        r = self._crop_rect.rect()
        pos = self._crop_rect.pos()
        x = max(0, int(pos.x() + r.x()))
        y = max(0, int(pos.y() + r.y()))
        w = min(int(r.width()), self._img_w - x)
        h = min(int(r.height()), self._img_h - y)
        return (x, y, w, h)

    def set_crop_rect(self, x, y, w, h):
        if self._crop_rect:
            self._crop_rect.setPos(0, 0)
            self._crop_rect.setRect(x, y, w, h)
            self.cropChanged.emit(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap_item:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class VideoPlayer(QWidget):
    """Embedded video player with enhanced playback controls."""
    positionChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoPlayer")
        self._duration = 0
        self._filepath = None
        self._thumb_worker = None
        self._fps = 30.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video display
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.video_widget.setStyleSheet(f"background: {C['crust']}; border-radius: 8px;")
        layout.addWidget(self.video_widget)

        # Thumbnail strip
        self.thumb_strip = ThumbnailStrip()
        self.thumb_strip.clicked.connect(self._on_thumb_click)
        layout.addWidget(self.thumb_strip)

        # Player backend
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)

        # Controls bar
        controls = QWidget()
        controls.setObjectName("playerControls")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(8, 4, 8, 4)
        cl.setSpacing(4)

        # Frame step back
        self.btn_frame_back = QPushButton("<<")
        self.btn_frame_back.setProperty("class", "playerBtn")
        self.btn_frame_back.setToolTip("Previous frame")
        self.btn_frame_back.setAccessibleName("Previous frame")
        self.btn_frame_back.setFixedWidth(36)
        self.btn_frame_back.clicked.connect(self._frame_back)
        cl.addWidget(self.btn_frame_back)

        self.btn_play = QPushButton("Play")
        self.btn_play.setFixedWidth(52)
        self.btn_play.clicked.connect(self._toggle_play)
        cl.addWidget(self.btn_play)

        # Frame step forward
        self.btn_frame_fwd = QPushButton(">>")
        self.btn_frame_fwd.setProperty("class", "playerBtn")
        self.btn_frame_fwd.setToolTip("Next frame")
        self.btn_frame_fwd.setAccessibleName("Next frame")
        self.btn_frame_fwd.setFixedWidth(36)
        self.btn_frame_fwd.clicked.connect(self._frame_forward)
        cl.addWidget(self.btn_frame_fwd)

        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setProperty("class", "dimLabel")
        self.lbl_time.setFixedWidth(120)
        cl.addWidget(self.lbl_time)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 10000)
        self.seek_slider.sliderMoved.connect(self._seek)
        cl.addWidget(self.seek_slider, 1)

        # Playback speed
        self.cmb_speed = QComboBox()
        self.cmb_speed.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x", "4x"])
        self.cmb_speed.setCurrentText("1x")
        self.cmb_speed.setFixedWidth(65)
        self.cmb_speed.currentTextChanged.connect(self._on_speed_change)
        cl.addWidget(self.cmb_speed)

        # A-B loop
        self.btn_ab_loop = QPushButton("A-B")
        self.btn_ab_loop.setProperty("class", "playerBtn")
        self.btn_ab_loop.setToolTip("Set A-B loop points")
        self.btn_ab_loop.setFixedWidth(36)
        self.btn_ab_loop.setCheckable(True)
        self.btn_ab_loop.clicked.connect(self._toggle_ab_loop)
        cl.addWidget(self.btn_ab_loop)
        self._loop_a = -1
        self._loop_b = -1
        self._loop_active = False

        self.lbl_vol = QLabel("Vol:")
        self.lbl_vol.setProperty("class", "dimLabel")
        cl.addWidget(self.lbl_vol)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        cl.addWidget(self.vol_slider)

        layout.addWidget(controls)

        # Timecode display
        self.lbl_timecode = QLabel("00:00:00.000 | Frame: 0")
        self.lbl_timecode.setProperty("class", "dimLabel")
        self.lbl_timecode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_timecode)

    def load(self, filepath, info=None):
        self._filepath = filepath
        if info:
            self._fps = info.get("fps", 30.0) or 30.0
        else:
            self._fps = 30.0
        self.player.setSource(QUrl.fromLocalFile(filepath))
        self.btn_play.setText("Play")
        self._thumb_worker = ThumbnailWorker(filepath, 16)
        self._thumb_worker.thumbnails_ready.connect(self.thumb_strip.set_thumbnails)
        self._thumb_worker.start()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def _frame_back(self):
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = max(0, self.player.position() - step)
            self.player.setPosition(pos)

    def _frame_forward(self):
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = min(self._duration, self.player.position() + step)
            self.player.setPosition(pos)

    def _on_speed_change(self, text):
        speed = float(text.replace("x", ""))
        self.player.setPlaybackRate(speed)

    def _toggle_ab_loop(self, checked):
        if checked:
            if self._loop_a < 0:
                self._loop_a = self.player.position()
                self.btn_ab_loop.setText("B?")
                self.btn_ab_loop.setToolTip("Click to set B point")
            elif self._loop_b < 0:
                self._loop_b = self.player.position()
                self._loop_active = True
                self.btn_ab_loop.setText("A-B")
                self.btn_ab_loop.setToolTip("A-B loop active (click to clear)")
        else:
            self._loop_a = -1
            self._loop_b = -1
            self._loop_active = False
            self.btn_ab_loop.setText("A-B")
            self.btn_ab_loop.setToolTip("Set A-B loop points")

    def _on_position(self, pos_ms):
        if self._duration > 0:
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(int(pos_ms / self._duration * 10000))
            self.seek_slider.blockSignals(False)
            self.thumb_strip.set_position(pos_ms / self._duration)
        self.lbl_time.setText(
            f"{format_duration_short(pos_ms / 1000)} / {format_duration_short(self._duration / 1000)}"
        )
        sec = pos_ms / 1000
        frame = int(sec * self._fps)
        self.lbl_timecode.setText(f"{format_duration(sec)} | Frame: {frame}")
        self.positionChanged.emit(pos_ms / 1000)
        # A-B loop
        if self._loop_active and self._loop_b > 0 and pos_ms >= self._loop_b:
            self.player.setPosition(self._loop_a)

    def _on_duration(self, dur_ms):
        self._duration = dur_ms

    def _seek(self, value):
        if self._duration > 0:
            self.player.setPosition(int(value / 10000 * self._duration))

    def _on_thumb_click(self, ratio):
        if self._duration > 0:
            self.player.setPosition(int(ratio * self._duration))

    def stop(self):
        self.player.stop()
        self.btn_play.setText("Play")

    def get_position_sec(self):
        return self.player.position() / 1000

    def get_fps(self):
        return self._fps


# ---------------------------------------------------------------------------
# File Info Bar
# ---------------------------------------------------------------------------

class FileInfoBar(QWidget):
    fileLoaded = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fileInfoBar")
        self._info = None
        self._filepath = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.btn_open = QPushButton("Open Video")
        self.btn_open.setObjectName("primaryBtn")
        self.btn_open.setFixedWidth(120)
        self.btn_open.setAccessibleName("Open video file")
        self.btn_open.clicked.connect(self._open_file)

        self.lbl_name = QLabel("Open a video to get started")
        self.lbl_name.setProperty("class", "dimLabel")
        self.lbl_info = QLabel("")
        self.lbl_info.setProperty("class", "dimLabel")

        layout.addWidget(self.btn_open)
        layout.addWidget(self.lbl_name, 1)
        layout.addWidget(self.lbl_info)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", str(Path.home() / "Videos"),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mpg *.mpeg);;All Files (*)"
        )
        if path:
            self.load_file(path)

    def load_file(self, path):
        if not validate_media_path(path):
            self.lbl_name.setText("Invalid file path")
            return
        self._filepath = path
        self._info = probe_video(path)
        add_recent(path)
        name = Path(path).name
        if len(name) > 50:
            name = name[:47] + "..."
        self.lbl_name.setText(name)
        self.lbl_name.setToolTip(path)
        if self._info:
            w = self._info.get("width", "?")
            h = self._info.get("height", "?")
            dur = format_duration(self._info.get("duration", 0))
            fps = self._info.get("fps", "?")
            size = format_size(self._info.get("size", 0))
            br = format_bitrate(self._info.get("bit_rate", 0))
            self.lbl_info.setText(f"{w}x{h}  |  {fps} fps  |  {dur}  |  {size}  |  {br}")
        else:
            self.lbl_info.setText("Could not read metadata")
        self.fileLoaded.emit(path, self._info or {})

    def filepath(self):
        return self._filepath

    def info(self):
        return self._info

# ---------------------------------------------------------------------------
# Panel: Trim
# ---------------------------------------------------------------------------

class TrimPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, player, parent=None):
        super().__init__(parent)
        self.console = console
        self.player = player
        self._filepath = None
        self._info = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grp = QGroupBox("Trim Range")
        gl = QVBoxLayout(grp)
        self.range_slider = RangeSlider()
        self.range_slider.rangeChanged.connect(self._on_range_changed)
        gl.addWidget(self.range_slider)

        times_row = QHBoxLayout()
        self.lbl_start = QLabel("Start: 00:00:00.000")
        self.lbl_end = QLabel("End: 00:00:00.000")
        self.lbl_duration = QLabel("Duration: 00:00:00.000")
        self.lbl_duration.setProperty("class", "accentLabel")
        times_row.addWidget(self.lbl_start)
        times_row.addStretch()
        times_row.addWidget(self.lbl_duration)
        times_row.addStretch()
        times_row.addWidget(self.lbl_end)
        gl.addLayout(times_row)

        marker_row = QHBoxLayout()
        self.btn_set_in = QPushButton("Set In (current)")
        self.btn_set_in.clicked.connect(self._set_in_from_player)
        self.btn_set_out = QPushButton("Set Out (current)")
        self.btn_set_out.clicked.connect(self._set_out_from_player)
        marker_row.addWidget(self.btn_set_in)
        marker_row.addWidget(self.btn_set_out)
        marker_row.addStretch()
        gl.addLayout(marker_row)
        layout.addWidget(grp)

        opts = QGroupBox("Cut Mode")
        ol = QVBoxLayout(opts)
        mode_row = QHBoxLayout()
        self.chk_lossless = QCheckBox("Lossless (keyframe-aligned, fastest)")
        self.chk_lossless.setChecked(True)
        self.chk_lossless.toggled.connect(self._on_mode_changed)
        self.chk_smart = QCheckBox("Smart Cut (re-encode edges only)")
        self.chk_smart.toggled.connect(self._on_mode_changed)
        self.chk_reencode = QCheckBox("Full re-encode (frame-accurate)")
        self.chk_reencode.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.chk_lossless)
        mode_row.addWidget(self.chk_smart)
        mode_row.addWidget(self.chk_reencode)
        ol.addLayout(mode_row)
        fmt_row = QHBoxLayout()
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["Same as source", "MP4", "MKV", "MOV", "WebM"])
        fmt_row.addWidget(QLabel("Format:"))
        fmt_row.addWidget(self.cmb_format)
        fmt_row.addStretch()
        ol.addLayout(fmt_row)
        layout.addWidget(opts)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = QHBoxLayout()
        self.btn_trim = QPushButton("Trim Video")
        self.btn_trim.setObjectName("primaryBtn")
        self.btn_trim.setEnabled(False)
        self.btn_trim.clicked.connect(self._do_trim)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_trim)
        layout.addLayout(btn_row)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_trim.setEnabled(bool(FFMPEG))
        duration = info.get("duration", 0) if info else 0
        self.range_slider._max = duration
        self.range_slider.set_range(0, duration)

    def _on_range_changed(self, low, high):
        self.lbl_start.setText(f"Start: {format_duration(low)}")
        self.lbl_end.setText(f"End: {format_duration(high)}")
        self.lbl_duration.setText(f"Duration: {format_duration(high - low)}")

    def _set_in_from_player(self):
        pos = self.player.get_position_sec()
        self.range_slider.set_range(pos, self.range_slider.high())

    def _set_out_from_player(self):
        pos = self.player.get_position_sec()
        self.range_slider.set_range(self.range_slider.low(), pos)

    def _on_mode_changed(self, checked):
        if not checked:
            return
        sender = self.sender()
        for chk in (self.chk_lossless, self.chk_smart, self.chk_reencode):
            if chk is not sender:
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)

    def _find_prev_keyframe(self, filepath, time_sec):
        if not FFPROBE:
            return time_sec
        try:
            cmd = [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
                   "-show_entries", "packet=pts_time,flags",
                   "-of", "csv=p=0", "-read_intervals", f"%{time_sec+0.5}",
                   filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            last_kf = 0
            for line in result.stdout.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        pts = float(parts[0])
                    except ValueError:
                        continue
                    if "K" in parts[1] and pts <= time_sec:
                        last_kf = pts
            return last_kf
        except (OSError, subprocess.TimeoutExpired):
            return time_sec

    def _do_trim(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"Same as source": "", "MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov", "WebM": ".webm"}
        fmt = self.cmb_format.currentText()
        src = Path(self._filepath)
        ext = ext_map.get(fmt, "") or src.suffix
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Trimmed Video", str(src.parent / f"{src.stem}_trimmed{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi);;All Files (*)")
        if not out_path:
            return
        start = self.range_slider.low()
        end = self.range_slider.high()
        if self.chk_smart.isChecked():
            self._do_smart_cut(start, end, out_path)
            return
        elif self.chk_lossless.isChecked():
            cmd = [FFMPEG, "-y", "-ss", str(start), "-i", self._filepath,
                   "-t", str(end - start), "-c", "copy", "-avoid_negative_ts", "make_zero"]
        else:
            cmd = [FFMPEG, "-y", "-i", self._filepath, "-ss", str(start), "-to", str(end),
                   "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k"]
        cmd.append(out_path)
        self.btn_trim.setEnabled(False)
        if self.chk_lossless.isChecked():
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self._worker = FFmpegWorker(cmd, end - start)
        self._worker.progress.connect(lambda v: self.progress.setValue(int(v)))
        self._worker.speed_info.connect(self.lbl_progress_detail.setText)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_done(ok, msg, out_path))
        self._worker.start()

    def _do_smart_cut(self, start, end, out_path):
        self.btn_trim.setEnabled(False)
        self.progress.setRange(0, 0)
        self.console.append("[Smart Cut] Finding keyframes and preparing segments...\n")
        prev_kf = self._find_prev_keyframe(self._filepath, start)
        next_kf_after_start = start
        tmpdir = tempfile.mkdtemp(prefix="clipforge_smartcut_")
        _register_temp_dir(tmpdir)
        self._smart_tmpdir = tmpdir
        head_seg = os.path.join(tmpdir, "head.mp4")
        cmd_head = [FFMPEG, "-y", "-i", self._filepath,
                    "-ss", str(prev_kf), "-to", str(start),
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", head_seg]
        mid_seg = os.path.join(tmpdir, "mid.mp4")
        cmd_mid = [FFMPEG, "-y", "-ss", str(start), "-i", self._filepath,
                   "-t", str(end - start), "-c", "copy",
                   "-avoid_negative_ts", "make_zero", mid_seg]
        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w") as f:
            f.write(f"file '{mid_seg}'\n")
        cmd_concat = [FFMPEG, "-y", "-f", "concat", "-safe", "0",
                      "-i", concat_list, "-c", "copy", out_path]
        self._smart_steps = [
            ("Copying middle (lossless)...", cmd_mid),
            ("Joining segments...", cmd_concat),
        ]
        self._smart_out_path = out_path
        self._smart_step_idx = 0
        self._run_next_smart_step()

    def _run_next_smart_step(self):
        if self._smart_step_idx >= len(self._smart_steps):
            if hasattr(self, '_smart_tmpdir'):
                shutil.rmtree(self._smart_tmpdir, ignore_errors=True)
                _unregister_temp_dir(self._smart_tmpdir)
            self._on_done(True, "Smart Cut complete", self._smart_out_path)
            return
        label, cmd = self._smart_steps[self._smart_step_idx]
        self.console.append(f"[Smart Cut] {label}\n")
        self._worker = FFmpegWorker(cmd, 0, parse_progress=False)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_smart_step_done)
        self._worker.start()

    def _on_smart_step_done(self, ok, msg):
        if not ok:
            if hasattr(self, '_smart_tmpdir'):
                shutil.rmtree(self._smart_tmpdir, ignore_errors=True)
                _unregister_temp_dir(self._smart_tmpdir)
            self._on_done(False, f"Smart Cut failed: {msg}", self._smart_out_path)
            return
        self._smart_step_idx += 1
        self._run_next_smart_step()

    def _on_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_trim.setEnabled(True)
        self.lbl_progress_detail.setText("")
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Trim complete  ({size})", C["green"])
        else:
            self.console.append(f"\n[ERROR] {msg}\n")
            self.requestToast.emit(f"Trim failed: {msg}", C["red"])

# ---------------------------------------------------------------------------
# Panel: Crop
# ---------------------------------------------------------------------------

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
        self.btn_crop = QPushButton("Crop / Transform Video")
        self.btn_crop.setObjectName("primaryBtn")
        self.btn_crop.setEnabled(False)
        self.btn_crop.clicked.connect(self._do_crop)
        btn_row.addStretch()
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
        if not out_path:
            return
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
        self._worker = FFmpegWorker(cmd, duration)
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

# ---------------------------------------------------------------------------
# Panel: Upscale + Interpolate
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Panel: Convert (with presets, HW encoding, cmd preview)
# ---------------------------------------------------------------------------

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
        self.spn_crf.valueChanged.connect(self._update_cmd_preview)
        self.spn_speed.valueChanged.connect(self._update_cmd_preview)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.lbl_progress_detail = QLabel("")
        self.lbl_progress_detail.setObjectName("progressDetail")
        layout.addWidget(self.lbl_progress_detail)
        btn_row = QHBoxLayout()
        self.btn_convert = QPushButton("Convert Video")
        self.btn_convert.setObjectName("primaryBtn")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self._do_convert)
        btn_row.addStretch()
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

    def _delete_preset(self):
        text = self.cmb_preset_select.currentText()
        if text.startswith("[Custom] "):
            name = text.replace("[Custom] ", "")
            delete_user_preset(name)
            self._refresh_presets()
            self.requestToast.emit(f"Preset '{name}' deleted", C["yellow"])

    def _on_container_changed(self, container):
        if container == "WebM":
            self.cmb_vcodec.setCurrentText("VP9")
            self.cmb_acodec.setCurrentText("Opus")
        elif container == "GIF":
            self.cmb_acodec.setCurrentText("None (remove audio)")

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
        if not out_path:
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

# ---------------------------------------------------------------------------
# Panel: Filters
# ---------------------------------------------------------------------------

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
        self.cmb_whisper_model.addItems(["tiny", "base", "small", "medium", "large"])
        self.cmb_whisper_model.setCurrentText("base")
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
        if not out_path:
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
        model = self.cmb_whisper_model.currentText()
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
        if not out_path:
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

# ---------------------------------------------------------------------------
# Panel: Audio
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Panel: Streams (media info + stream management)
# ---------------------------------------------------------------------------

class StreamsPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, player=None, parent=None):
        super().__init__(parent)
        self.console = console
        self._player = player
        self._filepath = None
        self._info = None
        self._worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Full media info
        info_grp = QGroupBox("Media Information")
        il = QVBoxLayout(info_grp)
        self.txt_media_info = QTextEdit()
        self.txt_media_info.setObjectName("cmdPreview")
        self.txt_media_info.setReadOnly(True)
        self.txt_media_info.setMaximumHeight(200)
        il.addWidget(self.txt_media_info)
        btn_copy_info = QPushButton("Copy Info")
        btn_copy_info.clicked.connect(lambda: QApplication.clipboard().setText(self.txt_media_info.toPlainText()))
        il.addWidget(btn_copy_info, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(info_grp)

        # Stream list with toggles
        stream_grp = QGroupBox("Streams (toggle for remux)")
        self._stream_layout = QVBoxLayout(stream_grp)
        self.lbl_no_streams = QLabel("Open a video to inspect streams")
        self.lbl_no_streams.setProperty("class", "dimLabel")
        self._stream_layout.addWidget(self.lbl_no_streams)
        self._stream_checks = []
        layout.addWidget(stream_grp)

        # Remux
        remux_grp = QGroupBox("Remux / Extract")
        rl = QHBoxLayout(remux_grp)
        rl.addWidget(QLabel("Container:"))
        self.cmb_remux_container = QComboBox()
        self.cmb_remux_container.addItems(["MP4", "MKV", "MOV", "WebM"])
        rl.addWidget(self.cmb_remux_container)
        self.btn_remux = QPushButton("Remux (no re-encode)")
        self.btn_remux.setObjectName("primaryBtn")
        self.btn_remux.setEnabled(False)
        self.btn_remux.clicked.connect(self._do_remux)
        rl.addStretch()
        rl.addWidget(self.btn_remux)
        layout.addWidget(remux_grp)

        # Snapshot
        snap_grp = QGroupBox("Frame Export")
        sl = QHBoxLayout(snap_grp)
        sl.addWidget(QLabel("Export current frame at full resolution"))
        self.btn_snapshot = QPushButton("Snapshot (PNG)")
        self.btn_snapshot.setObjectName("primaryBtn")
        self.btn_snapshot.setEnabled(False)
        self.btn_snapshot.clicked.connect(self._do_snapshot)
        sl.addStretch()
        sl.addWidget(self.btn_snapshot)
        layout.addWidget(snap_grp)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        layout.addStretch()

    def load_file(self, filepath, info):
        self._filepath = filepath
        self._info = info
        self.btn_remux.setEnabled(bool(FFMPEG))
        self.btn_snapshot.setEnabled(bool(FFMPEG))
        self._update_info()

    def _update_info(self):
        if not self._info:
            self.txt_media_info.setText("Open a video to see media information")
            return
        lines = []
        lines.append(f"File: {self._info.get('path', 'N/A')}")
        lines.append(f"Format: {self._info.get('format_name', 'N/A')}")
        lines.append(f"Duration: {format_duration(self._info.get('duration', 0))}")
        lines.append(f"Size: {format_size(self._info.get('size', 0))}")
        lines.append(f"Bitrate: {format_bitrate(self._info.get('bit_rate', 0))}")
        lines.append("")
        # Clear old stream checkboxes
        for chk in self._stream_checks:
            chk.setParent(None)
        self._stream_checks.clear()
        if self.lbl_no_streams.parent():
            self.lbl_no_streams.setParent(None)

        for s in self._info.get("streams", []):
            idx = s.get("index", 0)
            codec_type = s.get("codec_type", "unknown")
            codec_name = s.get("codec_name", "unknown")
            detail = f"Stream #{idx}: {codec_type} ({codec_name})"
            if codec_type == "video":
                detail += f" - {s.get('width', '?')}x{s.get('height', '?')}, {s.get('fps', '?')} fps"
                detail += f", {s.get('pix_fmt', '')}, {s.get('profile', '')}"
                if s.get('color_space'):
                    detail += f", {s['color_space']}"
            elif codec_type == "audio":
                detail += f" - {s.get('channels', '?')}ch, {s.get('sample_rate', '?')} Hz"
                detail += f", {s.get('channel_layout', '')}"
            elif codec_type == "subtitle":
                lang = s.get("language", "")
                title = s.get("title", "")
                detail += f" - {lang} {title}"
            lines.append(detail)
            chk = QCheckBox(detail)
            chk.setChecked(True)
            chk.setObjectName("streamItem")
            self._stream_layout.addWidget(chk)
            self._stream_checks.append(chk)

        # Tags
        tags = self._info.get("tags", {})
        if tags:
            lines.append("")
            lines.append("Metadata:")
            for k, v in tags.items():
                lines.append(f"  {k}: {v}")

        self.txt_media_info.setText("\n".join(lines))

    def _do_remux(self):
        if not self._filepath or not FFMPEG:
            return
        ext_map = {"MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov", "WebM": ".webm"}
        container = self.cmb_remux_container.currentText()
        ext = ext_map.get(container, ".mkv")
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Remuxed Video", str(src.parent / f"{src.stem}_remux{ext}"),
            "Video Files (*.mp4 *.mkv *.mov *.webm);;All Files (*)")
        if not out_path:
            return
        cmd = [FFMPEG, "-y", "-i", self._filepath]
        # Map selected streams
        for i, chk in enumerate(self._stream_checks):
            if chk.isChecked():
                cmd += ["-map", f"0:{i}"]
        cmd += ["-c", "copy"]
        if container == "MP4":
            cmd += ["-movflags", "+faststart"]
        cmd.append(out_path)
        duration = self._info.get("duration", 0) if self._info else 0
        self.progress.setRange(0, 0)
        self.btn_remux.setEnabled(False)
        self._worker = FFmpegWorker(cmd, duration)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(lambda ok, msg: self._on_remux_done(ok, msg, out_path))
        self._worker.start()

    def _on_remux_done(self, ok, msg, out_path):
        self.progress.setRange(0, 100)
        self.btn_remux.setEnabled(True)
        if ok:
            self.progress.setValue(100)
            size = format_size(os.path.getsize(out_path)) if os.path.exists(out_path) else ""
            self.requestToast.emit(f"Remux complete ({size})", C["green"])
        else:
            self.requestToast.emit(f"Remux failed: {msg}", C["red"])

    def _do_snapshot(self):
        if not self._filepath or not FFMPEG:
            return
        src = Path(self._filepath)
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", str(src.parent / f"{src.stem}_snapshot.png"),
            "Images (*.png *.jpg);;All Files (*)")
        if not out_path:
            return
        seek_sec = 0
        if self._player:
            seek_sec = self._player.get_position_sec()
        cmd = [FFMPEG, "-y", "-ss", str(seek_sec), "-i", self._filepath, "-frames:v", "1", "-q:v", "1", out_path]
        subprocess.run(cmd, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if os.path.exists(out_path):
            size = format_size(os.path.getsize(out_path))
            self.requestToast.emit(f"Snapshot saved ({size})", C["green"])
        else:
            self.requestToast.emit("Snapshot failed", C["red"])

# ---------------------------------------------------------------------------
# Panel: Batch Processing
# ---------------------------------------------------------------------------

class BatchPanel(QWidget):
    requestToast = pyqtSignal(str, str)

    def __init__(self, console, parent=None):
        super().__init__(parent)
        self.console = console
        self._items = []
        self._worker = None
        self._current_idx = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grp = QGroupBox("File Queue (drag & drop or browse)")
        gl = QVBoxLayout(grp)
        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(140)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        gl.addWidget(self.file_list)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add Files")
        self.btn_add.clicked.connect(self._add_files)
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_add_folder.clicked.connect(self._add_folder)
        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.clicked.connect(self._clear_files)
        self.btn_remove_sel = QPushButton("Remove Selected")
        self.btn_remove_sel.clicked.connect(self._remove_selected)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addWidget(self.btn_remove_sel)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        gl.addLayout(btn_row)
        layout.addWidget(grp)

        op_grp = QGroupBox("Batch Operation")
        ol = QHBoxLayout(op_grp)
        ol.addWidget(QLabel("Operation:"))
        self.cmb_operation = QComboBox()
        self.cmb_operation.addItems([
            "Convert to MP4 (H.264)", "Convert to MKV (H.265)", "Convert to WebM (VP9)",
            "Downscale to 1080p", "Downscale to 720p",
            "Extract Audio (MP3)", "Extract Audio (AAC)",
            "Remove Audio", "Lossless Trim (first 30s)",
        ])
        ol.addWidget(self.cmb_operation, 1)
        layout.addWidget(op_grp)

        # Output naming template
        name_grp = QGroupBox("Output Naming")
        nl = QHBoxLayout(name_grp)
        nl.addWidget(QLabel("Template:"))
        self.txt_name_template = QLineEdit("{name}{suffix}{ext}")
        self.txt_name_template.setToolTip("Variables: {name}, {suffix}, {ext}, {date}, {index}")
        nl.addWidget(self.txt_name_template, 1)
        self.lbl_name_preview = QLabel("")
        self.lbl_name_preview.setProperty("class", "dimLabel")
        nl.addWidget(self.lbl_name_preview)
        self.txt_name_template.textChanged.connect(self._update_name_preview)
        layout.addWidget(name_grp)

        out_grp = QGroupBox("Output")
        outl = QHBoxLayout(out_grp)
        self.lbl_out_dir = QLabel("Same as source (with suffix)")
        self.lbl_out_dir.setProperty("class", "dimLabel")
        self.chk_custom_dir = QCheckBox("Custom output directory:")
        self.chk_custom_dir.toggled.connect(self._toggle_custom_dir)
        self.btn_out_dir = QPushButton("Browse")
        self.btn_out_dir.setEnabled(False)
        self.btn_out_dir.clicked.connect(self._browse_out_dir)
        outl.addWidget(self.chk_custom_dir)
        outl.addWidget(self.lbl_out_dir, 1)
        outl.addWidget(self.btn_out_dir)
        layout.addWidget(out_grp)

        # Post-completion
        post_grp = QGroupBox("After Completion")
        post_l = QHBoxLayout(post_grp)
        self.cmb_post_action = QComboBox()
        self.cmb_post_action.addItems(["Do nothing", "Open output folder", "Play notification sound"])
        post_l.addWidget(QLabel("Action:"))
        post_l.addWidget(self.cmb_post_action)
        post_l.addStretch()
        layout.addWidget(post_grp)

        self.lbl_batch_status = QLabel("")
        self.lbl_batch_status.setProperty("class", "accentLabel")
        layout.addWidget(self.lbl_batch_status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        action_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("dangerBtn")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_start = QPushButton("Start Batch")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.clicked.connect(self._start_batch)
        action_row.addStretch()
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_start)
        layout.addLayout(action_row)
        layout.addStretch()

        self._out_dir = None

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Videos", str(Path.home() / "Videos"),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts);;All Files (*)")
        for p in paths:
            self._items.append(p)
            self.file_list.addItem(Path(p).name)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with Videos")
        if folder:
            for ext in VIDEO_EXTS:
                for f in Path(folder).glob(f"*{ext}"):
                    self._items.append(str(f))
                    self.file_list.addItem(f.name)

    def _clear_files(self):
        self._items.clear()
        self.file_list.clear()

    def _remove_selected(self):
        for item in sorted(self.file_list.selectedIndexes(), reverse=True):
            idx = item.row()
            self.file_list.takeItem(idx)
            if idx < len(self._items):
                self._items.pop(idx)

    def _toggle_custom_dir(self, checked):
        self.btn_out_dir.setEnabled(checked)
        if not checked:
            self.lbl_out_dir.setText("Same as source (with suffix)")
            self._out_dir = None

    def _browse_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if d:
            self._out_dir = d
            self.lbl_out_dir.setText(d)

    def _update_name_preview(self):
        template = self.txt_name_template.text()
        import datetime
        preview = template.format(
            name="example_video", suffix="_h264", ext=".mp4",
            date=datetime.date.today().isoformat(), index="001"
        )
        self.lbl_name_preview.setText(f"Preview: {preview}")

    def _get_output_path(self, src_path, operation):
        src = Path(src_path)
        suffix_map = {
            "Convert to MP4 (H.264)": ("_h264", ".mp4"),
            "Convert to MKV (H.265)": ("_h265", ".mkv"),
            "Convert to WebM (VP9)": ("_vp9", ".webm"),
            "Downscale to 1080p": ("_1080p", src.suffix),
            "Downscale to 720p": ("_720p", src.suffix),
            "Extract Audio (MP3)": ("", ".mp3"),
            "Extract Audio (AAC)": ("", ".aac"),
            "Remove Audio": ("_noaudio", src.suffix),
            "Lossless Trim (first 30s)": ("_30s", src.suffix),
        }
        name_suffix, ext = suffix_map.get(operation, ("_out", src.suffix))
        out_dir = Path(self._out_dir) if self._out_dir else src.parent

        template = self.txt_name_template.text().strip()
        if template and template != "{name}{suffix}{ext}":
            import datetime
            try:
                fname = template.format(
                    name=src.stem, suffix=name_suffix, ext=ext,
                    date=datetime.date.today().isoformat(),
                    index=f"{self._current_idx + 1:03d}"
                )
                if not fname.endswith(ext):
                    fname += ext
                return str(out_dir / fname)
            except (KeyError, ValueError):
                pass
        return str(out_dir / f"{src.stem}{name_suffix}{ext}")

    def _build_cmd(self, src_path, out_path, operation):
        if not FFMPEG:
            return None
        cmd = [FFMPEG, "-y", "-i", src_path]
        if operation == "Convert to MP4 (H.264)":
            cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
        elif operation == "Convert to MKV (H.265)":
            cmd += ["-c:v", "libx265", "-crf", "22", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", out_path]
        elif operation == "Convert to WebM (VP9)":
            cmd += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k", out_path]
        elif operation == "Downscale to 1080p":
            cmd += ["-vf", "scale=1920:-2", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", out_path]
        elif operation == "Downscale to 720p":
            cmd += ["-vf", "scale=1280:-2", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", out_path]
        elif operation == "Extract Audio (MP3)":
            cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k", out_path]
        elif operation == "Extract Audio (AAC)":
            cmd += ["-vn", "-c:a", "aac", "-b:a", "192k", out_path]
        elif operation == "Remove Audio":
            cmd += ["-c:v", "copy", "-an", out_path]
        elif operation == "Lossless Trim (first 30s)":
            cmd = [FFMPEG, "-y", "-i", src_path, "-t", "30", "-c", "copy",
                   "-avoid_negative_ts", "make_zero", out_path]
        return cmd

    def _start_batch(self):
        if not self._items or not FFMPEG:
            return
        out_dir = self._out_dir or (str(Path(self._items[0]).parent) if self._items else "")
        if out_dir:
            try:
                usage = shutil.disk_usage(out_dir)
                estimated_needed = 0
                operation = self.cmb_operation.currentText()
                for p in self._items:
                    if not os.path.exists(p):
                        continue
                    info = probe_video(p)
                    if info and "Downscale" in operation or "Convert" in operation:
                        w = info.get("width", 1920)
                        h = info.get("height", 1080)
                        dur = info.get("duration", 0)
                        estimated_needed += estimate_output_size(dur, 18, w, h)
                    else:
                        estimated_needed += int(os.path.getsize(p) * 1.2)
                if usage.free < estimated_needed:
                    self.requestToast.emit(
                        f"Low disk space: {format_size(usage.free)} free, ~{format_size(estimated_needed)} needed",
                        C["yellow"])
            except OSError:
                pass
        self._current_idx = 0
        self.btn_start.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self._process_next()

    def _process_next(self):
        if self._current_idx >= len(self._items):
            self.btn_start.setEnabled(True)
            self.btn_cancel.setVisible(False)
            self.lbl_batch_status.setText(f"Batch complete: {len(self._items)} files processed")
            self.requestToast.emit(f"Batch complete: {len(self._items)} files", C["green"])
            self._post_completion()
            return

        src = self._items[self._current_idx]
        operation = self.cmb_operation.currentText()
        out_path = self._get_output_path(src, operation)
        cmd = self._build_cmd(src, out_path, operation)

        self.lbl_batch_status.setText(
            f"Processing {self._current_idx + 1}/{len(self._items)}: {Path(src).name}"
        )
        self.progress.setValue(0)

        item = self.file_list.item(self._current_idx)
        if item:
            item.setText(f"⟳  {Path(src).name}")

        info = probe_video(src)
        duration = info.get("duration", 0) if info else 0

        self._worker = FFmpegWorker(cmd, duration)
        self._worker.progress.connect(self._on_item_progress)
        self._worker.log_output.connect(self.console.append)
        self._worker.finished_signal.connect(self._on_item_done)
        self._worker.start()

    def _on_item_progress(self, pct):
        total = len(self._items)
        overall = (self._current_idx / total + pct / 100 / total) * 100
        self.progress.setValue(int(overall))

    def _on_item_done(self, ok, msg):
        item = self.file_list.item(self._current_idx)
        if item:
            if ok:
                item.setText(f"✓  {Path(self._items[self._current_idx]).name}")
            else:
                item.setText(f"✗  {Path(self._items[self._current_idx]).name}")
                self.console.append(f"[ERROR] {msg}\n")
        self._current_idx += 1
        self._process_next()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self.btn_start.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.lbl_batch_status.setText("Batch cancelled")

    def _post_completion(self):
        action = self.cmb_post_action.currentText()
        if action == "Open output folder":
            out_dir = self._out_dir or (str(Path(self._items[0]).parent) if self._items else "")
            if out_dir:
                if sys.platform == "win32":
                    os.startfile(out_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", out_dir])
                else:
                    subprocess.Popen(["xdg-open", out_dir])
        elif action == "Play notification sound":
            try:
                if sys.platform == "win32":
                    import winsound
                    winsound.MessageBeep(winsound.MB_OK)
            except (ImportError, OSError):
                pass

# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

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
            short = hw_names[:30] + "..." if len(hw_names) > 30 else hw_names
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
        content_layout.addWidget(self.file_bar)

        # Main splitter: top (player + panels) / bottom (console)
        main_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top area: player left, panel right
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Video player
        self.player = VideoPlayer()
        top_splitter.addWidget(self.player)

        # Panel stack
        self.console = QTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)
        self.console.setPlaceholderText("FFmpeg output will appear here")

        self.stack = QStackedWidget()
        self.trim_panel = TrimPanel(self.console, self.player)
        self.crop_panel = CropPanel(self.console)
        self.upscale_panel = UpscalePanel(self.console)
        self.convert_panel = ConvertPanel(self.console)
        self.filters_panel = FiltersPanel(self.console)
        self.audio_panel = AudioPanel(self.console)
        self.streams_panel = StreamsPanel(self.console, self.player)
        self.batch_panel = BatchPanel(self.console)

        for panel in [self.trim_panel, self.crop_panel, self.upscale_panel,
                      self.convert_panel, self.filters_panel, self.audio_panel,
                      self.streams_panel, self.batch_panel]:
            scroll = QScrollArea()
            scroll.setWidget(panel)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.stack.addWidget(scroll)

        top_splitter.addWidget(self.stack)
        top_splitter.setStretchFactor(0, 2)
        top_splitter.setStretchFactor(1, 3)

        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(self.console)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 1)

        content_layout.addWidget(main_splitter)
        main_layout.addWidget(content, 1)

        # Toast
        self.toast = Toast(self)

        # Connect toast signals
        for panel in [self.trim_panel, self.crop_panel, self.upscale_panel,
                      self.convert_panel, self.filters_panel, self.audio_panel,
                      self.streams_panel, self.batch_panel]:
            panel.requestToast.connect(self.toast.show_message)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"Ready  •  v{APP_VERSION}")

        # Default to Trim panel
        self._switch_panel(0)

    def _switch_panel(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setProperty("active", "true" if i == idx else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

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
        # Save window geometry
        self._settings["window_width"] = self.width()
        self._settings["window_height"] = self.height()
        save_settings(self._settings)
        super().closeEvent(event)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyleSheet(STYLESHEET)

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

    window = MainWindow()
    window.show()

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.file_bar.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
