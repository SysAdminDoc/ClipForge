"""Optional libmpv-backed QWidget preview spike."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget


_DLL_DIRECTORY_HANDLES = []
MPV_NATIVE_MIN_VERSION = (0, 41, 0)


@dataclass(frozen=True)
class MpvCapability:
    available: bool
    wrapper_version: str | None = None
    reason: str = ""
    library_path: str | None = None
    library_file: str | None = None
    native_version: str | None = None
    native_version_status: str = "unknown"


def _version_tuple(value):
    if not value:
        return None
    parts = str(value).split(".")
    try:
        return tuple(int(part) for part in parts[:3])
    except ValueError:
        return None


def native_version_status(version):
    parsed = _version_tuple(version)
    if parsed is None:
        return "unknown"
    padded = (*parsed, 0, 0)[:3]
    return "outdated" if padded < MPV_NATIVE_MIN_VERSION else "supported"


def find_native_library():
    """Find the native libmpv file without loading it."""
    candidates = []
    configured = os.environ.get("CLIPFORGE_LIBMPV_DIR")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path(__file__).resolve().parent.parent,
            Path(sys.executable).resolve().parent,
        ]
    )
    library_names = (
        ("mpv-2.dll", "libmpv-2.dll")
        if sys.platform == "win32"
        else ("libmpv.so", "libmpv.dylib", "libmpv.so.2")
    )
    seen = set()
    for directory in candidates:
        directory = directory.expanduser()
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        for name in library_names:
            path = resolved / name
            if path.is_file():
                return str(path)
    return None


def _native_file_version(path):
    if not path or sys.platform != "win32":
        return None
    try:
        import ctypes

        class _FixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("signature", ctypes.c_uint32),
                ("struct_version", ctypes.c_uint32),
                ("file_version_ms", ctypes.c_uint32),
                ("file_version_ls", ctypes.c_uint32),
            ]

        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return None
        value = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version.VerQueryValueW(
            buffer, "\\", ctypes.byref(value), ctypes.byref(length)
        ):
            return None
        info = ctypes.cast(value, ctypes.POINTER(_FixedFileInfo)).contents
        return ".".join(
            (
                str(info.file_version_ms >> 16),
                str(info.file_version_ms & 0xFFFF),
                str(info.file_version_ls >> 16),
                str(info.file_version_ls & 0xFFFF),
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _prepare_windows_dll_search():
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return None
    native = find_native_library()
    if native:
        candidate = Path(native).parent
        handle = os.add_dll_directory(str(candidate))
        _DLL_DIRECTORY_HANDLES.append(handle)
        current_path = os.environ.get("PATH", "")
        entries = current_path.split(os.pathsep)
        if str(candidate) not in entries:
            os.environ["PATH"] = str(candidate) + os.pathsep + current_path
        return str(candidate)
    return None


def probe_mpv():
    library_path = _prepare_windows_dll_search()
    library_file = find_native_library()
    if library_path is None and library_file:
        library_path = str(Path(library_file).parent)
    try:
        import mpv  # noqa: F401 - importing the wrapper validates native loading

        version = importlib.metadata.version("mpv")
        native_version = _native_file_version(library_file)
        native_status = native_version_status(native_version)
        if native_status == "outdated":
            return MpvCapability(
                available=False,
                wrapper_version=version,
                reason=(
                    f"Native libmpv {native_version} is below the supported "
                    "0.41.0 minimum; Qt Multimedia fallback is active"
                ),
                library_path=library_path,
                library_file=library_file,
                native_version=native_version,
                native_version_status=native_status,
            )
        return MpvCapability(
            available=True,
            wrapper_version=version,
            library_path=library_path,
            library_file=library_file,
            native_version=native_version,
            native_version_status=native_status,
        )
    except (ImportError, OSError, RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        native_version = _native_file_version(library_file)
        return MpvCapability(
            available=False,
            reason=str(error),
            library_path=library_path,
            library_file=library_file,
            native_version=native_version,
            native_version_status=native_version_status(native_version),
        )


class MpvWidget(QWidget):
    """Native child window rendered by libmpv through python-mpv."""

    positionChanged = pyqtSignal(float)
    durationChanged = pyqtSignal(float)
    pausedChanged = pyqtSignal(bool)
    playbackError = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mpvVideoWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setStyleSheet("background: #000;")
        self._player = None

    def ensure_initialized(self):
        """Create libmpv only after Qt has assigned the final native widget size."""
        if self._player is None:
            self._initialize()

    def _initialize(self):
        capability = probe_mpv()
        if not capability.available:
            raise RuntimeError(capability.reason or "python-mpv/libmpv is unavailable")
        import mpv

        window_id = int(self.winId())
        if sys.platform == "win32":
            window_id &= 0xFFFFFFFF
        self._player = mpv.MPV(
            wid=str(window_id),
            vo="gpu-next",
            hwdec="auto-safe",
            keep_open="yes",
            idle="yes",
            osc=False,
            terminal=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            log_handler=self._on_log,
        )
        self._player.observe_property("time-pos", self._on_time)
        self._player.observe_property("duration", self._on_duration)
        self._player.observe_property("pause", self._on_pause)

    def _on_log(self, level, component, message):
        if str(level).lower() in {"error", "fatal"}:
            self.playbackError.emit(
                f"mpv {component}: {str(message).strip()}"
            )

    def _on_time(self, _name, value):
        if value is not None:
            self.positionChanged.emit(float(value))

    def _on_duration(self, _name, value):
        if value is not None:
            self.durationChanged.emit(float(value))

    def _on_pause(self, _name, value):
        if value is not None:
            self.pausedChanged.emit(bool(value))

    def load(self, path, start=None):
        self.ensure_initialized()
        if self._player:
            self._player.pause = True
            command = ["loadfile", os.fspath(Path(path).resolve()), "replace"]
            if start is not None and float(start) > 0:
                command.extend(["-1", f"start={float(start):.6f}"])
            self._player.command(*command)

    def play(self):
        if self._player:
            self._player.pause = False

    def pause(self):
        if self._player:
            self._player.pause = True

    def stop(self):
        if self._player:
            self._player.command("stop")

    def seek(self, seconds):
        if self._player:
            self._player.command("seek", max(float(seconds), 0), "absolute+exact")

    def frame_step(self, direction=1):
        if self._player:
            self._player.command("frame-step" if direction >= 0 else "frame-back-step")

    def set_speed(self, speed):
        if self._player:
            self._player.speed = float(speed)

    def set_volume(self, volume):
        if self._player:
            self._player.volume = max(0.0, min(float(volume), 100.0))

    def position(self):
        if not self._player:
            return 0.0
        return float(self._player.time_pos or 0.0)

    def is_paused(self):
        return True if not self._player else bool(self._player.pause)

    def shutdown(self):
        player, self._player = self._player, None
        if player:
            try:
                player.terminate()
            except (OSError, RuntimeError):
                pass
