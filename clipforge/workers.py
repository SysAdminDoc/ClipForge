"""Background worker threads for FFmpeg, thumbnails, upscale, and interpolation."""

import sys
import os
import re
import shutil
import threading
import time as _time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap

from clipforge_utils import format_duration_short

from .tools import (
    FFMPEG, FFPROBE,
    find_realesrgan, find_rife, find_span,
    probe_video, extract_frame,
    create_job_temp_dir, _unregister_temp_dir,
)
from .processes import (
    command_with_staging_output,
    run_managed_process,
    staging_output_path,
    terminate_process_tree,
    validate_output,
)

# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _kill_process_tree(proc):
    terminate_process_tree(proc)


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

    def __init__(
        self,
        cmd,
        duration=0,
        parse_progress=True,
        parent=None,
        *,
        output_path=None,
        overwrite=False,
        timeout=None,
    ):
        super().__init__(parent)
        self.cmd = [str(part) for part in cmd]
        self.duration = duration
        self.parse_progress = parse_progress
        self.output_path = output_path
        self.overwrite = overwrite
        self.timeout = timeout or max(3600, float(duration or 0) * 20)
        self._cancel_event = threading.Event()
        self._start_time = 0
        self._stderr_buffer = []
        self._progress_values = {}

    def cancel(self):
        self._cancel_event.set()

    def _progress_line(self, line):
        key, separator, value = line.strip().partition("=")
        if not separator:
            return
        self._progress_values[key] = value
        if key not in {"out_time_us", "out_time_ms"} or self.duration <= 0:
            return
        try:
            current = float(value) / 1_000_000
        except ValueError:
            return
        pct = min(current / self.duration * 100, 100)
        self.progress.emit(pct)
        elapsed = _time.time() - self._start_time
        speed = self._progress_values.get("speed", "")
        fps = self._progress_values.get("fps", "")
        size = self._progress_values.get("total_size", "")
        remaining = (
            (self.duration - current) / max(current / elapsed, 0.01)
            if elapsed > 0.5 and current > 0
            else 0
        )
        details = []
        if fps and fps != "N/A":
            details.append(f"{fps} fps")
        if speed and speed != "N/A":
            details.append(speed)
        if remaining > 0:
            details.append(f"ETA: {format_duration_short(remaining)}")
        if size.isdigit():
            details.append(f"{int(size) / (1024 * 1024):.1f} MiB")
        self.speed_info.emit(" | ".join(details))

    def _stderr_line(self, line):
        self.log_output.emit(line)
        self._stderr_buffer.append(line)

    def run(self):
        staged_path = None
        try:
            self._start_time = _time.time()
            command = list(self.cmd)
            final_path = Path(self.output_path) if self.output_path else None
            if final_path:
                if final_path.exists() and not self.overwrite:
                    self.finished_signal.emit(False, "Output already exists")
                    return
                final_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path = staging_output_path(final_path)
                command = command_with_staging_output(command, final_path, staged_path)
            if self.parse_progress and FFMPEG and Path(command[0]).name.lower().startswith("ffmpeg"):
                if "-progress" not in command:
                    command[1:1] = ["-progress", "pipe:1", "-nostats"]
            self.log_output.emit(f"$ {' '.join(command)}\n")
            outcome = run_managed_process(
                command,
                cancel_event=self._cancel_event,
                timeout=self.timeout,
                stdout_callback=self._progress_line if self.parse_progress else self.log_output.emit,
                stderr_callback=self._stderr_line,
            )
            elapsed = _time.time() - self._start_time
            if outcome.cancelled:
                self.finished_signal.emit(False, "Cancelled")
            elif outcome.timed_out:
                self.finished_signal.emit(False, "Process timed out")
            elif outcome.returncode == 0:
                if final_path:
                    valid, reason = validate_output(staged_path, ffprobe_path=FFPROBE)
                    if not valid:
                        self.finished_signal.emit(False, reason)
                        return
                    os.replace(staged_path, final_path)
                    staged_path = None
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
                    self.finished_signal.emit(
                        False,
                        hint or f"Process exited with code {outcome.returncode}",
                    )
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if staged_path:
                try:
                    Path(staged_path).unlink(missing_ok=True)
                except OSError:
                    pass


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

    def __init__(
        self,
        input_path,
        output_path,
        scale=2,
        model="realesrgan-x4plus",
        engine="realesrgan",
        parent=None,
        *,
        overwrite=False,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.scale = scale
        self.model = model
        self.engine = engine
        self.overwrite = overwrite
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

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
        staged_output = None
        try:
            final_output = Path(self.output_path)
            if final_output.exists() and not self.overwrite:
                self.finished_signal.emit(False, "Output already exists")
                return
            staged_output = staging_output_path(final_output)
            tmpdir = create_job_temp_dir("upscale")
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
            extract_result = run_managed_process(
                [FFMPEG, "-y", "-i", self.input_path, "-qscale:v", "2",
                 os.path.join(frames_dir, "frame_%06d.jpg")],
                cancel_event=self._cancel_event,
                timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
            )
            if extract_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if extract_result.returncode != 0:
                self.finished_signal.emit(False, "Frame extraction failed")
                return
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
            def upscaler_log(line):
                self.log_output.emit(line)
                match = re.search(r"(\d+\.\d+)%", line)
                if match:
                    self.progress.emit(10 + float(match.group(1)) * 0.7)

            upscale_result = run_managed_process(
                cmd_up,
                cancel_event=self._cancel_event,
                timeout=max(3600, total * 60),
                stdout_callback=upscaler_log,
                stderr_callback=upscaler_log,
            )
            if upscale_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if upscale_result.returncode != 0:
                self.finished_signal.emit(False, f"{upscaler_name} failed")
                return
            self.progress.emit(80)

            self.log_output.emit("[3/3] Reassembling video...\n")
            audio_path = os.path.join(tmpdir, "audio.aac")
            audio_result = run_managed_process(
                [FFMPEG, "-y", "-i", self.input_path, "-vn", "-acodec", "copy", audio_path],
                cancel_event=self._cancel_event,
                timeout=600,
            )
            has_audio = (
                audio_result.returncode == 0
                and os.path.exists(audio_path)
                and os.path.getsize(audio_path) > 0
            )
            cmd_re = [FFMPEG, "-y", "-framerate", str(fps),
                      "-i", os.path.join(upscaled_dir, "frame_%06d.jpg")]
            if has_audio:
                cmd_re += ["-i", audio_path]
            cmd_re += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd_re += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
            cmd_re.append(str(staged_output))
            reassemble_result = run_managed_process(
                cmd_re,
                cancel_event=self._cancel_event,
                timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
            )
            if reassemble_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if reassemble_result.returncode != 0:
                self.finished_signal.emit(False, "Video reassembly failed")
                return
            valid, reason = validate_output(staged_output, ffprobe_path=FFPROBE)
            if not valid:
                self.finished_signal.emit(False, reason)
                return
            os.replace(staged_output, final_output)
            staged_output = None
            self.progress.emit(100)
            self.finished_signal.emit(True, "Upscale complete")
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if 'tmpdir' in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)
                _unregister_temp_dir(tmpdir)
            if staged_output:
                try:
                    Path(staged_output).unlink(missing_ok=True)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# InterpolateWorker
# ---------------------------------------------------------------------------


class InterpolateWorker(QThread):
    """Frame interpolation using rife-ncnn-vulkan."""
    progress = pyqtSignal(float)
    log_output = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        input_path,
        output_path,
        multiplier=2,
        model="rife-v4.25",
        parent=None,
        *,
        overwrite=False,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.multiplier = multiplier
        self.model = model
        self.overwrite = overwrite
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

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
        staged_output = None
        try:
            final_output = Path(self.output_path)
            if final_output.exists() and not self.overwrite:
                self.finished_signal.emit(False, "Output already exists")
                return
            staged_output = staging_output_path(final_output)
            tmpdir = create_job_temp_dir("interpolate")
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
            extract_result = run_managed_process(
                [FFMPEG, "-y", "-i", self.input_path, "-qscale:v", "2",
                 os.path.join(frames_dir, "frame_%06d.png")],
                cancel_event=self._cancel_event,
                timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
            )
            if extract_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if extract_result.returncode != 0:
                self.finished_signal.emit(False, "Frame extraction failed")
                return
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
            def rife_log(line):
                self.log_output.emit(line)
                match = re.search(r"(\d+\.\d+)%", line)
                if match:
                    self.progress.emit(15 + float(match.group(1)) * 0.65)

            interpolate_result = run_managed_process(
                cmd_rife,
                cancel_event=self._cancel_event,
                timeout=max(3600, len(frames) * self.multiplier * 60),
                stdout_callback=rife_log,
                stderr_callback=rife_log,
            )
            if interpolate_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if interpolate_result.returncode != 0:
                self.finished_signal.emit(False, "RIFE failed")
                return
            self.progress.emit(80)

            self.log_output.emit("[3/3] Reassembling video...\n")
            audio_path = os.path.join(tmpdir, "audio.aac")
            audio_result = run_managed_process(
                [FFMPEG, "-y", "-i", self.input_path, "-vn", "-acodec", "copy", audio_path],
                cancel_event=self._cancel_event,
                timeout=600,
            )
            has_audio = (
                audio_result.returncode == 0
                and os.path.exists(audio_path)
                and os.path.getsize(audio_path) > 0
            )

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
            cmd_re.append(str(staged_output))
            reassemble_result = run_managed_process(
                cmd_re,
                cancel_event=self._cancel_event,
                timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
            )
            if reassemble_result.cancelled:
                self.finished_signal.emit(False, "Cancelled")
                return
            if reassemble_result.returncode != 0:
                self.finished_signal.emit(False, "Video reassembly failed")
                return
            valid, reason = validate_output(staged_output, ffprobe_path=FFPROBE)
            if not valid:
                self.finished_signal.emit(False, reason)
                return
            os.replace(staged_output, final_output)
            staged_output = None
            self.progress.emit(100)
            self.finished_signal.emit(
                True,
                f"Interpolation complete ({fps} -> {new_fps} fps)",
            )
        except Exception as e:
            self.finished_signal.emit(False, str(e))
        finally:
            if 'tmpdir' in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)
                _unregister_temp_dir(tmpdir)
            if staged_output:
                try:
                    Path(staged_output).unlink(missing_ok=True)
                except OSError:
                    pass
