"""FFmpeg / tool detection, probing, and temp-dir management."""

import sys
import os
import subprocess
import json
import shutil
import tempfile
import atexit
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from clipforge_utils import _parse_fps
from .ai_tools import AIToolManager

# ---------------------------------------------------------------------------
# Temp-dir tracking
# ---------------------------------------------------------------------------

_active_temp_dirs = set()
_temp_dirs_lock = threading.Lock()


def _register_temp_dir(path):
    with _temp_dirs_lock:
        _active_temp_dirs.add(str(Path(path).resolve()))


def _unregister_temp_dir(path):
    with _temp_dirs_lock:
        _active_temp_dirs.discard(str(Path(path).resolve()))


def _cleanup_temp_dirs():
    with _temp_dirs_lock:
        owned_dirs = list(_active_temp_dirs)
        _active_temp_dirs.clear()
    for d in owned_dirs:
        shutil.rmtree(d, ignore_errors=True)


def create_job_temp_dir(prefix):
    path = tempfile.mkdtemp(prefix=f"clipforge_{os.getpid()}_{prefix}_")
    _register_temp_dir(path)
    return path


def write_concat_manifest(paths, manifest_path):
    """Write an ffconcat-safe absolute path list."""
    lines = []
    for path in paths:
        normalized = Path(path).resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{normalized}'")
    Path(manifest_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


atexit.register(_cleanup_temp_dirs)

# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

_AI_TOOL_MANAGER = AIToolManager()


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
    managed = _AI_TOOL_MANAGER.managed_path("realesrgan")
    if managed:
        return str(managed)
    name = "realesrgan-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).resolve().parent.parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None


def find_rife():
    managed = _AI_TOOL_MANAGER.managed_path("rife")
    if managed:
        return str(managed)
    name = "rife-ncnn-vulkan"
    path = shutil.which(name)
    if path:
        return path
    local = Path(__file__).resolve().parent.parent / name
    if sys.platform == "win32":
        local = local.with_suffix(".exe")
    return str(local) if local.exists() else None


def find_span():
    managed = _AI_TOOL_MANAGER.managed_path("span")
    if managed:
        return str(managed)
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

NVDEC_FIX_COMMIT = "4c6217477fc64305055b37d9d1d0d76d30e37f97"


def parse_ffmpeg_version(version_output):
    """Return an FFmpeg release tuple, or None for an unrecognized build."""
    match = re.search(
        r"\bffmpeg version (?:n)?(\d+)\.(\d+)(?:\.(\d+))?",
        str(version_output or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def nvdec_decode_is_safe(version_output):
    """Reject NVDEC releases affected by CVE-2026-64832.

    Unknown builds fail closed. Git builds can opt in only when their version
    banner carries the full upstream fix commit.
    """
    output = str(version_output or "")
    if NVDEC_FIX_COMMIT in output.lower():
        return True
    version = parse_ffmpeg_version(output)
    return version is not None and version > (8, 1, 2)


def read_ffmpeg_version(ffmpeg_path=None):
    path = ffmpeg_path or FFMPEG
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return (result.stdout or result.stderr or "").splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        return ""


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
FFMPEG_VERSION_OUTPUT = read_ffmpeg_version()
CUDA_NVDEC_SAFE = nvdec_decode_is_safe(FFMPEG_VERSION_OUTPUT)


def hardware_decode_args(video_encoder):
    """Return a safe hardware-decode prefix for a selected encoder."""
    encoder = str(video_encoder or "").lower()
    if "nvenc" in encoder:
        if CUDA_NVDEC_SAFE:
            return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        return []
    if "qsv" in encoder:
        return ["-hwaccel", "qsv"]
    if "amf" in encoder:
        return ["-hwaccel", "d3d11va"]
    return []

# ---------------------------------------------------------------------------
# ffprobe cache & probing
# ---------------------------------------------------------------------------

class StreamInfo(TypedDict, total=False):
    index: int
    codec_type: str
    codec_name: str
    codec_long_name: str
    time_base: str
    start_time: float
    duration: float
    disposition: dict[str, int]
    tags: dict[str, str]
    rotation: float
    width: int
    height: int
    fps: float
    avg_fps: float
    pix_fmt: str
    bit_rate: int
    profile: str
    color_range: str
    color_space: str
    color_transfer: str
    color_primaries: str
    field_order: str
    sample_rate: str
    sample_fmt: str
    channels: int
    channel_layout: str
    language: str
    title: str


class ChapterInfo(TypedDict, total=False):
    id: int
    time_base: str
    start_time: float
    end_time: float
    tags: dict[str, str]


class MediaInfo(TypedDict, total=False):
    path: str
    streams: list[StreamInfo]
    chapters: list[ChapterInfo]
    duration: float
    size: int
    format_name: str
    format_long_name: str
    bit_rate: int
    start_time: float
    tags: dict[str, str]
    width: int
    height: int
    fps: float
    pix_fmt: str
    rotation: float
    audio_codec: str
    audio_channels: int
    audio_sample_rate: str
    audio_channel_layout: str


@dataclass(frozen=True)
class ProbeError:
    code: str
    message: str
    details: str = ""


@dataclass(frozen=True)
class ProbeResult:
    info: MediaInfo | None = None
    error: ProbeError | None = None


_probe_cache: dict[tuple[Any, ...], ProbeResult] = {}


def _probe_cache_key(filepath):
    try:
        stat = os.stat(filepath)
        return (filepath, stat.st_size, stat.st_mtime)
    except OSError:
        return None


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stream_rotation(stream):
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return _safe_float(side_data.get("rotation"))
    return _safe_float(stream.get("tags", {}).get("rotate"))


def probe_media(filepath):
    """Return typed metadata or a stable, user-presentable probe error."""
    if not FFPROBE:
        return ProbeResult(
            error=ProbeError(
                "ffprobe_missing",
                "FFprobe is not installed or could not be found.",
            )
        )
    cache_key = _probe_cache_key(filepath)
    if cache_key and cache_key in _probe_cache:
        return _probe_cache[cache_key]
    try:
        cmd = [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            filepath,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            probe_result = ProbeResult(
                error=ProbeError(
                    "probe_failed",
                    "FFprobe could not read this media file.",
                    details[-1000:],
                )
            )
            if cache_key:
                _probe_cache[cache_key] = probe_result
            return probe_result
        data = json.loads(result.stdout)
        info: MediaInfo = {"path": filepath, "streams": [], "chapters": []}
        fmt = data.get("format", {})
        info["duration"] = _safe_float(fmt.get("duration"))
        info["size"] = _safe_int(fmt.get("size"))
        info["format_name"] = fmt.get("format_name", "unknown")
        info["format_long_name"] = fmt.get("format_long_name", "")
        info["bit_rate"] = _safe_int(fmt.get("bit_rate"))
        info["start_time"] = _safe_float(fmt.get("start_time"))
        info["tags"] = fmt.get("tags", {})
        for s in data.get("streams", []):
            tags = s.get("tags", {})
            si: StreamInfo = {
                "index": _safe_int(s.get("index")),
                "codec_type": s.get("codec_type") or "unknown",
                "codec_name": s.get("codec_name") or "unknown",
                "codec_long_name": s.get("codec_long_name", ""),
                "time_base": s.get("time_base", ""),
                "start_time": _safe_float(s.get("start_time")),
                "duration": _safe_float(s.get("duration")),
                "disposition": {
                    key: _safe_int(value)
                    for key, value in s.get("disposition", {}).items()
                },
                "tags": tags,
            }
            if s.get("codec_type") == "video":
                si["width"] = _safe_int(s.get("width"))
                si["height"] = _safe_int(s.get("height"))
                si["fps"] = _parse_fps(s.get("r_frame_rate", "0/1") or "0/1")
                si["avg_fps"] = _parse_fps(s.get("avg_frame_rate", "0/1") or "0/1")
                si["pix_fmt"] = s.get("pix_fmt", "")
                si["bit_rate"] = _safe_int(s.get("bit_rate"))
                si["profile"] = s.get("profile", "")
                si["color_range"] = s.get("color_range", "")
                si["color_space"] = s.get("color_space", "")
                si["color_transfer"] = s.get("color_transfer", "")
                si["color_primaries"] = s.get("color_primaries", "")
                si["field_order"] = s.get("field_order", "")
                si["rotation"] = _stream_rotation(s)
                info["width"] = si["width"]
                info["height"] = si["height"]
                info["fps"] = si["avg_fps"] or si["fps"]
                info["pix_fmt"] = si["pix_fmt"]
                info["rotation"] = si["rotation"]
            elif s.get("codec_type") == "audio":
                si["sample_rate"] = s.get("sample_rate", "")
                si["sample_fmt"] = s.get("sample_fmt", "")
                si["channels"] = _safe_int(s.get("channels"))
                si["channel_layout"] = s.get("channel_layout", "")
                si["bit_rate"] = _safe_int(s.get("bit_rate"))
                info["audio_codec"] = s.get("codec_name", "")
                info["audio_channels"] = si["channels"]
                info["audio_sample_rate"] = s.get("sample_rate", "")
                info["audio_channel_layout"] = s.get("channel_layout", "")
            elif s.get("codec_type") == "subtitle":
                si["language"] = tags.get("language", "")
                si["title"] = tags.get("title", "")
            info["streams"].append(si)
        for chapter in data.get("chapters", []):
            info["chapters"].append(
                {
                    "id": _safe_int(chapter.get("id")),
                    "time_base": chapter.get("time_base", ""),
                    "start_time": _safe_float(chapter.get("start_time")),
                    "end_time": _safe_float(chapter.get("end_time")),
                    "tags": chapter.get("tags", {}),
                }
            )
        probe_result = ProbeResult(info=info)
        if cache_key:
            _probe_cache[cache_key] = probe_result
        return probe_result
    except subprocess.TimeoutExpired:
        return ProbeResult(
            error=ProbeError(
                "probe_timeout", "FFprobe timed out after 15 seconds."
            )
        )
    except OSError as exc:
        return ProbeResult(
            error=ProbeError("probe_launch_failed", "FFprobe could not start.", str(exc))
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return ProbeResult(
            error=ProbeError(
                "invalid_probe_data",
                "FFprobe returned invalid metadata.",
                str(exc),
            )
        )


def probe_video(filepath):
    """Compatibility wrapper returning metadata or ``None``."""
    return probe_media(filepath).info


_COPY_CODECS = {
    "MP4": {
        "video": {"h264", "hevc", "av1", "mpeg4", "mjpeg"},
        "audio": {"aac", "mp3", "ac3", "eac3", "alac"},
        "subtitle": {"mov_text"},
    },
    "MOV": {
        "video": {"h264", "hevc", "mpeg4", "mjpeg", "prores"},
        "audio": {
            "aac", "mp3", "ac3", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
        },
        "subtitle": {"mov_text"},
    },
    "WEBM": {
        "video": {"vp8", "vp9", "av1"},
        "audio": {"vorbis", "opus"},
        "subtitle": {"webvtt"},
    },
    "AVI": {
        "video": {"h264", "mpeg4", "mjpeg"},
        "audio": {"mp3", "ac3", "pcm_s16le", "pcm_s24le"},
        "subtitle": set(),
    },
}


def stream_copy_issues(container, streams):
    """Explain selected streams that cannot be copied into a target container."""
    selected = list(streams)
    if not selected:
        return ["Select at least one stream."]
    container_name = container.upper()
    if container_name == "MKV":
        return []
    supported = _COPY_CODECS.get(container_name)
    if not supported:
        return [f"Copy compatibility is not defined for {container_name}."]
    issues = []
    for stream in selected:
        stream_type = stream.get("codec_type", "unknown")
        codec = stream.get("codec_name", "unknown")
        allowed = supported.get(stream_type)
        if allowed is None:
            issues.append(
                f"Stream #{stream.get('index', '?')} ({stream_type}/{codec}) "
                f"is not supported by {container_name}."
            )
        elif codec not in allowed:
            issues.append(
                f"Stream #{stream.get('index', '?')} codec {codec} cannot be "
                f"copied into {container_name}; choose MKV or re-encode it."
            )
    return issues


def extract_frame(filepath, time_sec=0):
    if not FFMPEG:
        return None
    tmp_name = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        tmp_name = tmp.name
        cmd = [FFMPEG, "-y", "-ss", str(time_sec), "-i", filepath,
               "-frames:v", "1", "-q:v", "2", tmp.name]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            return None
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(tmp.name)
            return pix
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return None


def _confirm_overwrite(parent, filepath, source_path=None):
    if source_path:
        try:
            if os.path.normcase(os.path.abspath(filepath)) == os.path.normcase(
                os.path.abspath(source_path)
            ):
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    parent,
                    "Invalid Output",
                    "The output path must be different from the input file.",
                )
                return False
        except OSError:
            return False
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
