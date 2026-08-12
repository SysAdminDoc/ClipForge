"""Optional yt-dlp discovery, URL policy, and output validation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

YTDLP_MEDIA_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
}


def find_yt_dlp() -> str | None:
    """Locate a local yt-dlp executable; never install or download one."""

    candidates = []
    on_path = shutil.which("yt-dlp")
    if on_path:
        candidates.append(on_path)
    for variable in ("LOCALAPPDATA", "APPDATA", "PROGRAMFILES"):
        root = os.environ.get(variable)
        if root:
            candidates.extend([
                str(Path(root) / "Programs" / "Python" / "Python312" / "Scripts" / "yt-dlp.exe"),
                str(Path(root) / "yt-dlp.exe"),
            ])
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def validate_source_url(url: str) -> str:
    """Accept only explicit HTTP(S) URLs suitable for a user-requested import."""

    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter an http:// or https:// media URL")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not supported")
    return value


def build_yt_dlp_command(yt_dlp_path, url, output_dir):
    """Build a single-video, restricted-filename command in a chosen folder."""

    source_url = validate_source_url(url)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    return [
        str(yt_dlp_path),
        "--no-playlist",
        "--no-part",
        "--restrict-filenames",
        "--newline",
        "--paths",
        str(destination),
        "-o",
        "%(title).150B.%(ext)s",
        "--merge-output-format",
        "mp4",
        "--print",
        "after_move:filepath",
        source_url,
    ]


def validate_download_path(path, output_dir):
    """Ensure yt-dlp's reported output is a local supported media file."""

    candidate = Path(path).expanduser()
    destination = Path(output_dir).expanduser().resolve()
    try:
        resolved = candidate.resolve()
        resolved.relative_to(destination)
    except (OSError, ValueError):
        return None
    if resolved.suffix.lower() not in YTDLP_MEDIA_EXTENSIONS or not resolved.is_file():
        return None
    return resolved


__all__ = [
    "YTDLP_MEDIA_EXTENSIONS",
    "build_yt_dlp_command",
    "find_yt_dlp",
    "validate_download_path",
    "validate_source_url",
]
