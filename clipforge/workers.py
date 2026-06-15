"""Background worker threads for FFmpeg, thumbnails, upscale, and interpolation."""

import sys
import os
import subprocess
import re
import shutil
import tempfile
import time as _time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap

from clipforge_utils import format_duration_short

from .tools import (
    FFMPEG, FFPROBE,
    find_realesrgan, find_rife, find_span,
    probe_video, extract_frame,
    _register_temp_dir, _unregister_temp_dir,
)

# ---------------------------------------------------------------------------
# Process helpers
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
    (r"Permission denied", "Permission denied -- check file/folder permissions"),
    (r"No space left on device", "Disk full -- free space and retry"),
    (r"Unknown encoder", "Encoder not available -- your FFmpeg build may lack this codec"),
    (r"Encoder .+ not found", "Encoder not available -- your FFmpeg build may lack this codec"),
    (r"Unknown decoder", "Decoder not available for this input format"),
    (r"Invalid data found when processing input", "Input file is corrupt or unsupported"),
    (r"Output file is empty", "Encoding produced no output -- check settings"),
    (r"does not support the audio codec", "Container does not support the chosen audio codec"),
    (r"does not support the video codec", "Container does not support the chosen video codec"),
    (r"Avi duration\|Error", "AVI container limit exceeded"),
]


def _parse_ffmpeg_error(stderr_text):
    for pattern, msg in _FFMPEG_ERROR_PATTERNS:
        if re.search(pattern, stderr_text, re.IGNORECASE):
            return msg
    return None


# ---------------------------------------------------------------------------
# FFmpegWorker
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ThumbnailWorker
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# UpscaleWorker
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# InterpolateWorker
# ---------------------------------------------------------------------------


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
