"""FFmpeg / tool detection, probing, and temp-dir management."""

import sys
import os
import subprocess
import json
import shutil
import tempfile
import atexit
from pathlib import Path

from clipforge_utils import _parse_fps

# ---------------------------------------------------------------------------
# Temp-dir tracking
# ---------------------------------------------------------------------------

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
# Tool detection
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
    local = Path(__file__).resolve().parent.parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None


def find_rife():
    name = "rife-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).resolve().parent.parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None


def find_span():
    name = "span-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).resolve().parent.parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")

# ---------------------------------------------------------------------------
# Hardware encoder detection
# ---------------------------------------------------------------------------


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
# ffprobe cache & probing
# ---------------------------------------------------------------------------

_probe_cache = {}


def _probe_cache_key(filepath):
    try:
        stat = os.stat(filepath)
        return (filepath, stat.st_size, stat.st_mtime)
    except OSError:
        return None


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
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(tmp.name)
            os.unlink(tmp.name)
            return pix
        os.unlink(tmp.name)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _confirm_overwrite(parent, filepath):
    if not os.path.exists(filepath):
        return True
    from PyQt6.QtWidgets import QMessageBox
    result = QMessageBox.question(
        parent, "Overwrite File?",
        f"'{Path(filepath).name}' already exists.\n\nOverwrite it?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return result == QMessageBox.StandardButton.Yes
