"""Deterministic, atomic proxy-media cache management."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .constants import CONFIG_DIR


PROXY_PROFILE = {
    "version": 1,
    "max_width": 1280,
    "video_codec": "libx264",
    "crf": 28,
    "audio_bitrate": "128k",
}


class ProxyCache:
    """Map immutable source fingerprints to validated local preview proxies."""

    def __init__(self, root=None):
        self.root = Path(root) if root else CONFIG_DIR / "proxies"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cleanup_incomplete()

    @staticmethod
    def source_fingerprint(source):
        path = Path(source).resolve()
        stat = path.stat()
        return {
            "path": os.fspath(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    def key_for(self, source):
        payload = {
            "source": self.source_fingerprint(source),
            "profile": PROXY_PROFILE,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def paths_for(self, source):
        key = self.key_for(source)
        return (
            self.root / f"{key}.mp4",
            self.root / f"{key}.json",
        )

    @staticmethod
    def estimate_size(info):
        duration = max(float((info or {}).get("duration") or 0), 0)
        width = int((info or {}).get("width") or 0)
        if width >= 3840:
            video_bitrate = 4_000_000
        elif width >= 1920:
            video_bitrate = 2_500_000
        else:
            video_bitrate = 1_200_000
        return int(duration * (video_bitrate + 128_000) / 8)

    def command(self, ffmpeg, source, output):
        return [
            ffmpeg,
            "-hide_banner",
            "-i",
            os.fspath(Path(source).resolve()),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            r"scale=w=min(1280\,iw):h=-2",
            "-c:v",
            PROXY_PROFILE["video_codec"],
            "-preset",
            "veryfast",
            "-crf",
            str(PROXY_PROFILE["crf"]),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            PROXY_PROFILE["audio_bitrate"],
            "-movflags",
            "+faststart",
            "-y",
            os.fspath(output),
        ]

    def record(self, source, proxy_path):
        source_data = self.source_fingerprint(source)
        expected_proxy, manifest_path = self.paths_for(source)
        proxy_path = Path(proxy_path)
        if proxy_path.resolve() != expected_proxy.resolve():
            raise ValueError("Proxy path does not match the current source fingerprint")
        if not proxy_path.is_file() or proxy_path.stat().st_size <= 0:
            raise ValueError("Proxy output is missing or empty")
        payload = {
            "schema": "clipforge.proxy",
            "version": 1,
            "source": source_data,
            "proxy": {
                "filename": proxy_path.name,
                "size": proxy_path.stat().st_size,
            },
            "profile": PROXY_PROFILE,
        }
        staged = manifest_path.with_suffix(".json.partial")
        staged.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(staged, manifest_path)
        return proxy_path

    def lookup(self, source):
        try:
            proxy_path, manifest_path = self.paths_for(source)
            if not proxy_path.is_file() or not manifest_path.is_file():
                return None
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("schema") != "clipforge.proxy":
                return None
            if payload.get("source") != self.source_fingerprint(source):
                return None
            if payload.get("profile") != PROXY_PROFILE:
                return None
            expected_size = int(payload.get("proxy", {}).get("size") or 0)
            if expected_size <= 0 or proxy_path.stat().st_size != expected_size:
                return None
            return proxy_path
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def cleanup_incomplete(self):
        for pattern in ("*.partial", ".*.clipforge-*"):
            for path in self.root.glob(pattern):
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass

    def prune(self, max_bytes=5 * 1024**3):
        entries = sorted(
            (path for path in self.root.glob("*.mp4") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in entries)
        removed = []
        for proxy_path in entries:
            if total <= max_bytes:
                break
            size = proxy_path.stat().st_size
            try:
                proxy_path.unlink()
                proxy_path.with_suffix(".json").unlink(missing_ok=True)
                total -= size
                removed.append(proxy_path)
            except OSError:
                continue
        return removed
