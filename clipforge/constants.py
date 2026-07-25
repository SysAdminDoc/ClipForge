"""Application constants, paths, extension lists, themes, and built-in presets."""

import json
from pathlib import Path

from . import APP_NAME, APP_VERSION

# ---------------------------------------------------------------------------
# Window / paths
# ---------------------------------------------------------------------------

WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"
CONFIG_DIR = Path.home() / ".clipforge"
RECENT_FILE = CONFIG_DIR / "recent.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PRESETS_DIR = CONFIG_DIR / "presets"

# ---------------------------------------------------------------------------
# Supported extensions
# ---------------------------------------------------------------------------

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv",
              ".m4v", ".ts", ".mpg", ".mpeg", ".gif", ".m2ts", ".vob")
AUDIO_EXTS = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus")
SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

# ---------------------------------------------------------------------------
# Color themes
# ---------------------------------------------------------------------------

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
    """Load theme by reading settings JSON directly (avoids circular import)."""
    try:
        settings_path = CONFIG_DIR / "settings.json"
        if settings_path.exists():
            s = json.loads(settings_path.read_text())
            if s.get("high_contrast"):
                return dict(C_HIGH_CONTRAST)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return dict(C_MOCHA)


C = _load_theme()

# ---------------------------------------------------------------------------
# Built-in social media / device presets
# ---------------------------------------------------------------------------

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
