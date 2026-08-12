"""Verified AI tool installation, status, and reusable frame-cache support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .constants import CONFIG_DIR


@dataclass(frozen=True)
class AIToolSpec:
    tool_id: str
    name: str
    version: str
    license: str
    license_url: str
    url: str
    archive_size: int
    unpacked_size: int
    sha256: str
    executable: str
    models: tuple[str, ...]


AI_TOOL_SPECS = {
    "realesrgan": AIToolSpec(
        tool_id="realesrgan",
        name="Real-ESRGAN NCNN Vulkan",
        version="v0.2.5.0-20220424",
        license="BSD-3-Clause",
        license_url="https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE",
        url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip",
        archive_size=45_474_481,
        unpacked_size=53_434_410,
        sha256="abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d",
        executable="realesrgan-ncnn-vulkan.exe",
        models=(
            "realesrgan-x4plus",
            "realesrgan-x4plus-anime",
            "realesr-animevideov3",
        ),
    ),
    "span": AIToolSpec(
        tool_id="span",
        name="SPAN NCNN Vulkan",
        version="20240831-055257",
        license="AGPL-3.0",
        license_url="https://github.com/TNTwise/SPAN-ncnn-vulkan/blob/master/LICENSE",
        url="https://github.com/TNTwise/SPAN-ncnn-vulkan/releases/download/20240831-055257/span-ncnn-vulkan-20240831-055257-windows.zip",
        archive_size=16_553_410,
        unpacked_size=23_314_727,
        sha256="ce72105410046e78fccd5a04498427538b0a20d8d30a1bc0f9f476bb9c8bfb6f",
        executable="span-ncnn-vulkan.exe",
        models=(
            "spanx2_ch48",
            "spanx4_ch48",
        ),
    ),
    "rife": AIToolSpec(
        tool_id="rife",
        name="RIFE NCNN Vulkan",
        version="20250112",
        license="MIT",
        license_url="https://github.com/TNTwise/rife-ncnn-vulkan/blob/master/LICENSE",
        url="https://github.com/TNTwise/rife-ncnn-vulkan/releases/download/20250112/windows.zip",
        archive_size=826_923_873,
        unpacked_size=896_285_231,
        sha256="42ed35e115b026f222386648920218cb8a9c7ae1e23698a7363bdd2e1455aba3",
        executable="rife-ncnn-vulkan.exe",
        models=(
            "rife-v4.25",
            "rife-v4.25-lite",
            "rife-v4.22",
            "rife-v4.6",
            "rife-v4",
        ),
    ),
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_remove_tree(path, allowed_root):
    path = Path(path).resolve()
    root = Path(allowed_root).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"Refusing to remove path outside AI tool root: {path}")
    shutil.rmtree(path, ignore_errors=True)


class AIToolManager:
    def __init__(self, root=None):
        self.root = Path(root) if root else CONFIG_DIR / "ai-tools"
        self.downloads = self.root / "downloads"
        self.installs = self.root / "installed"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.installs.mkdir(parents=True, exist_ok=True)

    def spec(self, tool_id):
        try:
            return AI_TOOL_SPECS[tool_id]
        except KeyError as error:
            raise ValueError(f"Tool is not in the ClipForge install manifest: {tool_id}") from error

    def install_dir(self, tool_id):
        spec = self.spec(tool_id)
        return self.installs / tool_id / spec.version

    def managed_path(self, tool_id):
        return self.verified_install_path(tool_id, self.install_dir(tool_id))

    def verified_install_path(self, tool_id, install_dir):
        spec = self.spec(tool_id)
        install_dir = Path(install_dir)
        manifest_path = install_dir / "install.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            executable = install_dir / manifest["executable"]
            if (
                manifest.get("tool_id") != spec.tool_id
                or manifest.get("version") != spec.version
                or manifest.get("archive_sha256") != spec.sha256
                or not executable.is_file()
                or _sha256(executable) != manifest.get("executable_sha256")
            ):
                return None
            return executable
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def status(self, tool_id, discovered_path=None):
        spec = self.spec(tool_id)
        managed = self.managed_path(tool_id)
        path = managed or (Path(discovered_path) if discovered_path else None)
        install_dir = self.install_dir(tool_id)
        manifest_path = install_dir / "install.json"
        invalid_install = manifest_path.is_file() and not managed
        if managed:
            availability = "verified-managed"
        elif invalid_install:
            availability = "invalid_managed_install"
        elif path:
            availability = "external-unverified"
        else:
            availability = "unavailable"
        return {
            "tool_id": tool_id,
            "name": spec.name,
            "version": spec.version if managed else ("external/unverified" if path else spec.version),
            "license": spec.license,
            "license_url": spec.license_url,
            "path": os.fspath(path) if path else None,
            "managed": bool(managed),
            "verified": bool(managed),
            "archive_sha256": spec.sha256,
            "archive_size": spec.archive_size,
            "unpacked_size": spec.unpacked_size,
            "models": list(spec.models),
            "install_supported": sys.platform == "win32",
            "availability": availability,
            "install_manifest_sha256": (
                _sha256(manifest_path) if manifest_path.is_file() else None
            ),
            "executable_sha256": _sha256(path) if path and path.is_file() else None,
        }

    def archive_path(self, tool_id):
        spec = self.spec(tool_id)
        return self.downloads / f"{tool_id}-{spec.version}.zip"


class AIToolInstallWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, manager, tool_id, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.tool_id = tool_id
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def _download(self, spec, archive_path):
        partial = archive_path.with_suffix(".zip.part")
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "ClipForge-AI-Tool-Manager"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(spec.url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            if existing and getattr(response, "status", 200) != 206:
                existing = 0
                partial.unlink(missing_ok=True)
            mode = "ab" if existing else "wb"
            with partial.open(mode) as stream:
                downloaded = existing
                while True:
                    if self._cancel_event.is_set():
                        raise InterruptedError("Download cancelled; partial file kept for resume")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(min(70, int(downloaded / spec.archive_size * 70)))
        if partial.stat().st_size != spec.archive_size:
            raise ValueError(
                f"Download size mismatch: expected {spec.archive_size}, got {partial.stat().st_size}"
            )
        os.replace(partial, archive_path)

    @staticmethod
    def _safe_extract(archive_path, destination, cancel_event, progress):
        destination = Path(destination).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            for index, entry in enumerate(entries, 1):
                if cancel_event.is_set():
                    raise InterruptedError("Installation cancelled")
                target = (destination / entry.filename).resolve()
                if target != destination and destination not in target.parents:
                    raise ValueError(f"Unsafe archive path: {entry.filename}")
                mode = (entry.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError(f"Archive symlinks are not allowed: {entry.filename}")
                archive.extract(entry, destination)
                progress(80 + int(index / max(len(entries), 1) * 15))

    def _activate_staged_install(self, staging, final_dir):
        """Atomically activate a verified install and restore its predecessor on failure."""
        staging = Path(staging)
        final_dir = Path(final_dir)
        if not self.manager.verified_install_path(self.tool_id, staging):
            raise ValueError("Staged tool failed pre-activation verification")

        backup = None
        activated = False
        if final_dir.exists():
            backup = final_dir.parent / (
                f".{final_dir.name}.backup-{uuid.uuid4().hex}"
            )
        try:
            if backup:
                os.replace(final_dir, backup)
            os.replace(staging, final_dir)
            activated = True
            if not self.manager.managed_path(self.tool_id):
                raise ValueError("Installed tool failed post-install verification")
        except Exception as activation_error:
            rollback_error = None
            try:
                if activated and final_dir.exists():
                    _safe_remove_tree(final_dir, self.manager.root)
                    if final_dir.exists():
                        raise OSError("Could not remove failed replacement")
                if backup and backup.exists():
                    os.replace(backup, final_dir)
            except Exception as error:
                rollback_error = error
            if rollback_error:
                raise RuntimeError(
                    f"{activation_error}; rollback failed: {rollback_error}"
                ) from activation_error
            raise

        if backup and backup.exists():
            _safe_remove_tree(backup, self.manager.root)
        for stale_backup in final_dir.parent.glob(f".{final_dir.name}.backup-*"):
            _safe_remove_tree(stale_backup, self.manager.root)

    def run(self):
        staging = None
        try:
            if sys.platform != "win32":
                raise RuntimeError("Managed AI tool installation is currently available on Windows only")
            spec = self.manager.spec(self.tool_id)
            archive_path = self.manager.archive_path(self.tool_id)
            self.status.emit(f"Downloading {spec.name} ({spec.archive_size / 1024**2:.1f} MiB)…")
            if not archive_path.is_file() or archive_path.stat().st_size != spec.archive_size:
                self._download(spec, archive_path)
            self.status.emit("Verifying SHA-256 checksum…")
            self.progress.emit(75)
            actual_sha = _sha256(archive_path)
            if actual_sha != spec.sha256:
                archive_path.unlink(missing_ok=True)
                raise ValueError("Downloaded archive checksum did not match the ClipForge manifest")

            tool_root = self.manager.installs / spec.tool_id
            tool_root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".install-", dir=tool_root))
            self.status.emit("Extracting verified package…")
            self._safe_extract(archive_path, staging, self._cancel_event, self.progress.emit)
            executable = next(staging.rglob(spec.executable), None)
            if not executable or not executable.is_file():
                raise ValueError(f"Verified package did not contain {spec.executable}")
            relative_executable = executable.relative_to(staging)
            manifest = {
                "schema": "clipforge.ai-tool",
                "version": spec.version,
                "tool_id": spec.tool_id,
                "license": spec.license,
                "source_url": spec.url,
                "archive_sha256": spec.sha256,
                "executable": relative_executable.as_posix(),
                "executable_sha256": _sha256(executable),
                "models": list(spec.models),
            }
            (staging / "install.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
            final_dir = self.manager.install_dir(spec.tool_id)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            self.status.emit("Activating verified package with rollback protection…")
            self._activate_staged_install(staging, final_dir)
            staging = None
            archive_path.unlink(missing_ok=True)
            managed_path = self.manager.managed_path(spec.tool_id)
            self.progress.emit(100)
            self.finished_signal.emit(
                True,
                f"{spec.name} {spec.version} installed and verified",
                self.manager.status(spec.tool_id),
            )
        except InterruptedError as error:
            self.finished_signal.emit(False, str(error), None)
        except Exception as error:
            self.finished_signal.emit(False, str(error), None)
        finally:
            if staging:
                _safe_remove_tree(staging, self.manager.root)


class AIFrameCache:
    """Persistent source-frame cache shared by upscale and interpolation."""

    def __init__(self, root=None):
        self.root = Path(root) if root else CONFIG_DIR / "ai-frame-cache"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def fingerprint(source):
        path = Path(source).resolve()
        stat = path.stat()
        return {
            "path": os.fspath(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "format": "png-v1",
        }

    def key_for(self, source):
        payload = json.dumps(
            self.fingerprint(source),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def lookup(self, source):
        entry = self.root / self.key_for(source)
        manifest_path = entry / "frames.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            count = int(manifest.get("count") or 0)
            if manifest.get("source") != self.fingerprint(source) or count <= 0:
                return None
            if not (entry / "frame_000001.png").is_file():
                return None
            if not (entry / f"frame_{count:06d}.png").is_file():
                return None
            return entry
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def staging_dir(self, source):
        return Path(
            tempfile.mkdtemp(
                prefix=f".{self.key_for(source)}-",
                dir=self.root,
            )
        )

    def commit(self, source, staging, count):
        staging = Path(staging)
        payload = {
            "schema": "clipforge.ai-frame-cache",
            "source": self.fingerprint(source),
            "count": int(count),
        }
        (staging / "frames.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        final = self.root / self.key_for(source)
        if final.exists():
            existing = self.lookup(source)
            if existing:
                _safe_remove_tree(staging, self.root)
                return existing
            _safe_remove_tree(final, self.root)
        os.replace(staging, final)
        return final

    @staticmethod
    def estimate_required_bytes(info):
        width = max(int((info or {}).get("width") or 0), 1)
        height = max(int((info or {}).get("height") or 0), 1)
        fps = max(float((info or {}).get("fps") or 0), 1)
        duration = max(float((info or {}).get("duration") or 0), 0)
        return int(width * height * 3 * fps * duration * 0.6)
