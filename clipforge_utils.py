"""Pure utility functions for ClipForge — no PyQt6 dependency."""
import os
import re


def _parse_fps(rate_str):
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/")
            return round(int(num) / int(den), 2)
        return float(rate_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


def format_duration(seconds):
    if seconds <= 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def format_duration_short(seconds):
    if seconds <= 0:
        return "0:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_size(size_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_bitrate(bps):
    if bps <= 0:
        return "N/A"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    return f"{bps / 1_000:.0f} kbps"


def estimate_output_size(duration, crf, width, height, fps=30):
    pixels = width * height
    base_bpp = 0.15
    crf_factor = 2.0 ** ((18 - crf) / 6.0)
    bps = pixels * base_bpp * crf_factor * (fps / 30.0)
    return int(bps * duration / 8)


def _sanitize_preset_name(name):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name.strip())
    safe = safe.strip('. ')
    return safe[:100] or "preset"


def validate_media_path(filepath):
    if not filepath:
        return False
    if "\x00" in filepath:
        return False
    return os.path.isfile(filepath)


def _parse_metric_number(value):
    if value.lower() == "inf":
        return float("inf")
    try:
        return float(value)
    except ValueError:
        return None


def parse_vmaf_score(output_text):
    match = re.search(r"VMAF score:\s*([0-9.]+)", output_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(
        r'"vmaf"\s*:\s*\{[^}]*"mean"\s*:\s*([0-9.]+)',
        output_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return float(match.group(1))
    return None


def parse_psnr_average(output_text):
    match = re.search(r"\baverage:([0-9.]+|inf)\b", output_text, re.IGNORECASE)
    if not match:
        return None
    return _parse_metric_number(match.group(1))


def parse_ssim_all(output_text):
    match = re.search(r"\bAll:([0-9.]+)\b", output_text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))
