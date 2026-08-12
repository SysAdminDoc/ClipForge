"""FFmpeg / tool detection, probing, and temp-dir management."""

import sys
import os
import subprocess
import json
import shutil
import tempfile
import atexit
import threading
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from clipforge_utils import _parse_fps
from .ai_tools import AIToolManager
from .runtime_policy import (
    NVDEC_FIX_COMMIT,  # noqa: F401 - retained as a public compatibility constant
    evaluate_nvdec,
    parse_ffmpeg_version as _parse_ffmpeg_version,
)

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


def escape_ffmpeg_filter_value(value):
    """Escape a literal value for FFmpeg's filtergraph parser."""
    normalized = str(value).replace("\\", "/")
    normalized = normalized.replace("\\", "\\\\").replace(":", "\\:")
    # FFmpeg parses filtergraph and filter-option quoting separately, so a
    # literal apostrophe needs three backslashes between adjacent quotes.
    normalized = normalized.replace("'", r"'\\\''")
    return f"'{normalized}'"


def escape_ffmetadata_value(value):
    """Escape a value written to an FFMETADATA file."""
    escaped = str(value).replace("\\", "\\\\")
    for char in ("=", ";", "#"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped.replace("\r", "").replace("\n", "\\\n")


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

HW_ENCODER_LABELS = {
    "h264_nvenc": "H.264 NVENC (NVIDIA)",
    "hevc_nvenc": "H.265 NVENC (NVIDIA)",
    "h264_qsv": "H.264 QSV (Intel)",
    "hevc_qsv": "H.265 QSV (Intel)",
    "h264_amf": "H.264 AMF (AMD)",
    "hevc_amf": "H.265 AMF (AMD)",
    "av1_nvenc": "AV1 NVENC (NVIDIA)",
    "av1_qsv": "AV1 QSV (Intel)",
    "av1_amf": "AV1 AMF (AMD)",
}
_HW_CAPABILITY_CACHE = {}
_HW_CAPABILITY_CACHE_LOCK = threading.RLock()

def parse_ffmpeg_version(version_output):
    """Return an FFmpeg release tuple, or None for an unrecognized build."""
    return _parse_ffmpeg_version(version_output)


def nvdec_decode_is_safe(version_output):
    """Return whether the conservative, reviewed NVDEC policy allows decode."""
    return evaluate_nvdec(version_output).accepted


def read_ffmpeg_version(ffmpeg_path=None, *, cancel_event=None, timeout=10):
    path = ffmpeg_path or FFMPEG
    if not path:
        return ""
    try:
        from .processes import run_managed_process

        result = run_managed_process(
            [path, "-version"],
            cancel_event=cancel_event,
            timeout=timeout,
        )
        if result.cancelled or result.timed_out:
            return ""
        return (result.stdout or result.stderr or "").splitlines()[0]
    except OSError:
        return ""


def detect_hw_encoders(ffmpeg_path=None, *, cancel_event=None, timeout=10):
    """Detect available hardware encoders from FFmpeg."""
    ffmpeg_path = ffmpeg_path or FFMPEG
    if not ffmpeg_path:
        return {}
    try:
        from .processes import run_managed_process

        result = run_managed_process(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            cancel_event=cancel_event,
            timeout=timeout,
        )
        if result.cancelled or result.timed_out or result.returncode != 0:
            return {}
        hw = {}
        for name, label in HW_ENCODER_LABELS.items():
            if any(name in line.split() for line in result.stdout.splitlines() if line.strip()):
                hw[label] = name
        return hw
    except OSError:
        return {}


HW_ENCODERS = {}
HW_ENCODER_CAPABILITIES = {}
FFMPEG_VERSION_OUTPUT = ""
CUDA_NVDEC_SAFE = False


def _hardware_cache_key(ffmpeg_path, version, encoder):
    try:
        path = Path(ffmpeg_path).resolve()
        stat = path.stat()
        binary = (os.fspath(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        binary = (str(ffmpeg_path), None, None)
    device = (platform.system(), platform.machine())
    return binary, str(version or ""), device, str(encoder)


def _hardware_probe_args(ffmpeg_path, encoder):
    args = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:r=1",
        "-frames:v",
        "2",
        "-an",
    ]
    if "qsv" in encoder or "amf" in encoder:
        args.extend(["-vf", "format=nv12"])
    else:
        args.extend(["-pix_fmt", "yuv420p"])
    args.extend(["-c:v", encoder, "-f", "null", "-"])
    return args


def probe_hw_encoder(
    encoder,
    ffmpeg_path=None,
    *,
    version="",
    cancel_event=None,
    timeout=15,
):
    """Run a bounded real encode probe for one advertised hardware encoder."""
    ffmpeg_path = ffmpeg_path or FFMPEG
    if not ffmpeg_path:
        return {
            "encoder": encoder,
            "status": "unavailable",
            "reason": "FFmpeg is unavailable",
            "cached": False,
        }
    key = _hardware_cache_key(ffmpeg_path, version, encoder)
    with _HW_CAPABILITY_CACHE_LOCK:
        cached = _HW_CAPABILITY_CACHE.get(key)
    if cached:
        return {**cached, "cached": True}
    try:
        from .processes import run_managed_process

        result = run_managed_process(
            _hardware_probe_args(ffmpeg_path, encoder),
            cancel_event=cancel_event,
            timeout=timeout,
        )
        if result.cancelled:
            payload = {
                "encoder": encoder,
                "status": "cancelled",
                "reason": "Hardware capability probe was cancelled",
                "cached": False,
            }
        elif result.timed_out:
            payload = {
                "encoder": encoder,
                "status": "unavailable",
                "reason": f"Probe timed out after {timeout:g} seconds",
                "cached": False,
            }
        elif result.returncode == 0:
            payload = {
                "encoder": encoder,
                "status": "usable",
                "reason": "Real FFmpeg encode probe succeeded",
                "cached": False,
            }
        else:
            details = (result.stderr or result.stdout or "probe failed").strip()
            payload = {
                "encoder": encoder,
                "status": "unavailable",
                "reason": details[-800:],
                "cached": False,
            }
    except OSError as error:
        payload = {
            "encoder": encoder,
            "status": "unavailable",
            "reason": str(error),
            "cached": False,
        }
    if payload["status"] != "cancelled":
        with _HW_CAPABILITY_CACHE_LOCK:
            _HW_CAPABILITY_CACHE[key] = dict(payload)
    return payload


def probe_hw_encoders(
    encoders,
    ffmpeg_path=None,
    *,
    version="",
    cancel_event=None,
    timeout=15,
):
    """Probe every advertised encoder without blocking the GUI thread."""
    results = {}
    for encoder in dict.fromkeys(encoders):
        if cancel_event and cancel_event.is_set():
            break
        results[encoder] = probe_hw_encoder(
            encoder,
            ffmpeg_path,
            version=version,
            cancel_event=cancel_event,
            timeout=timeout,
        )
    return results


def clear_hw_capability_cache():
    with _HW_CAPABILITY_CACHE_LOCK:
        _HW_CAPABILITY_CACHE.clear()


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


def probe_media(filepath, *, timeout=15, cancel_event=None):
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
        from .processes import run_managed_process

        result = run_managed_process(
            cmd,
            cancel_event=cancel_event,
            timeout=timeout,
        )
        if result.cancelled:
            return ProbeResult(
                error=ProbeError("probe_cancelled", "Media inspection was cancelled.")
            )
        if result.timed_out:
            return ProbeResult(
                error=ProbeError(
                    "probe_timeout",
                    f"FFprobe timed out after {timeout:g} seconds.",
                )
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


def extract_frame(filepath, time_sec=0, *, timeout=10, cancel_event=None):
    if not FFMPEG:
        return None
    tmp_name = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        tmp_name = tmp.name
        cmd = [FFMPEG, "-y", "-ss", str(time_sec), "-i", filepath,
               "-frames:v", "1", "-q:v", "2", tmp.name]
        from .processes import run_managed_process

        result = run_managed_process(
            cmd,
            cancel_event=cancel_event,
            timeout=timeout,
        )
        if result.returncode != 0 or result.cancelled or result.timed_out:
            return None
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(tmp.name)
            return pix
    except OSError:
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
