"""Background worker threads for FFmpeg, thumbnails, upscale, and interpolation."""

import sys
import os
import re
import shutil
import threading
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap

from clipforge_utils import (
    format_duration_short,
    parse_psnr_average,
    parse_ssim_all,
    parse_vmaf_score,
)

from .tools import (
    FFMPEG, FFPROBE,
    find_realesrgan, find_rife, find_span,
    probe_video,
    create_job_temp_dir, _unregister_temp_dir,
)
from .processes import (
    command_with_staging_output,
    run_managed_process,
    staging_output_path,
    terminate_process_tree,
    validate_output,
)
from .diagnostics import DIAGNOSTICS
from .ai_tools import AIFrameCache

# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _kill_process_tree(proc):
    terminate_process_tree(proc)


def _start_worker_diagnostics(worker, kind, command, *, context=None):
    runner = command[0] if command else None
    tools = {
        "ffmpeg": DIAGNOSTICS.tool_version("ffmpeg", FFMPEG),
        "ffprobe": DIAGNOSTICS.tool_version("ffprobe", FFPROBE),
    }
    if runner and (Path(str(runner)).is_file() or shutil.which(str(runner))):
        tools[Path(str(runner)).name] = DIAGNOSTICS.tool_version("runner", runner)
    job_id = DIAGNOSTICS.start_job(
        kind,
        command,
        tools=tools,
        context=context,
    )
    if hasattr(worker, "log_output"):
        worker.log_output.connect(
            lambda message: DIAGNOSTICS.log(job_id, message),
            Qt.ConnectionType.DirectConnection,
        )

    def finish_diagnostics(ok, message, *_args):
        DIAGNOSTICS.finish(job_id, ok, message)
        if hasattr(worker, "log_output"):
            severity = "INFO" if ok else "ERROR"
            worker.log_output.emit(f"[{severity}] Job {job_id}: {message}\n")

    worker.finished_signal.connect(
        finish_diagnostics,
        Qt.ConnectionType.DirectConnection,
    )
    if hasattr(worker, "log_output"):
        worker.log_output.emit(f"[INFO] Job {job_id} started ({kind})\n")
    return job_id


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

    def run(self):
        staged_path = None
        diagnostic_id = _start_worker_diagnostics(
            self,
            "media-process",
            self.cmd,
            context={
                "output_path": self.output_path,
                "timeout_seconds": self.timeout,
                "output_validation": bool(self.output_path),
            },
        )
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
            DIAGNOSTICS.update(
                diagnostic_id,
                exit_code=outcome.returncode,
                cancelled=outcome.cancelled,
                timed_out=outcome.timed_out,
                stdout_truncated=outcome.stdout_truncated,
                stderr_truncated=outcome.stderr_truncated,
            )
            elapsed = _time.time() - self._start_time
            if outcome.cancelled:
                self.finished_signal.emit(False, "Cancelled")
            elif outcome.timed_out:
                self.finished_signal.emit(False, "Process timed out")
            elif outcome.returncode == 0:
                if final_path:
                    valid, reason = validate_output(staged_path, ffprobe_path=FFPROBE)
                    DIAGNOSTICS.update(
                        diagnostic_id,
                        output_valid=valid,
                        output_validation_message=reason,
                    )
                    if not valid:
                        self.finished_signal.emit(False, reason)
                        return
                    os.replace(staged_path, final_path)
                    staged_path = None
                self.progress.emit(100)
                self.finished_signal.emit(True, f"Complete ({format_duration_short(elapsed)})")
            else:
                stderr_text = outcome.stderr
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
            DIAGNOSTICS.update(diagnostic_id, exception=type(e).__name__)
            self.finished_signal.emit(False, str(e))
        finally:
            if staged_path:
                try:
                    Path(staged_path).unlink(missing_ok=True)
                except OSError:
                    pass


class QualityMetricsWorker(QThread):
    """Compute synchronized VMAF, PSNR, and SSIM diagnostics."""

    progress = pyqtSignal(float)
    log_output = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, object)

    def __init__(
        self,
        reference_path,
        encoded_path,
        reference_info=None,
        encoded_info=None,
        sync_offset=0.0,
        parent=None,
        *,
        metric_timeout=None,
    ):
        super().__init__(parent)
        self.reference_path = reference_path
        self.encoded_path = encoded_path
        self.reference_info = reference_info or {}
        self.encoded_info = encoded_info or {}
        self.sync_offset = float(sync_offset)
        duration = min(
            value for value in (
                float(self.reference_info.get("duration") or 0),
                float(self.encoded_info.get("duration") or 0),
            ) if value > 0
        ) if any(
            float(info.get("duration") or 0) > 0
            for info in (self.reference_info, self.encoded_info)
        ) else 0
        self.metric_timeout = float(metric_timeout or max(120, duration * 10))
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def _comparison_duration(self):
        reference_duration = float(self.reference_info.get("duration") or 0)
        encoded_duration = float(self.encoded_info.get("duration") or 0)
        reference_start = max(-self.sync_offset, 0.0)
        encoded_start = max(self.sync_offset, 0.0)
        remaining = [
            duration - start
            for duration, start in (
                (reference_duration, reference_start),
                (encoded_duration, encoded_start),
            )
            if duration > 0
        ]
        return max(min(remaining), 0.0) if remaining else 0.0

    def _filter_for(self, metric_filter):
        width = int(self.reference_info.get("width") or 0)
        height = int(self.reference_info.get("height") or 0)
        duration = self._comparison_duration()
        encoded_start = max(self.sync_offset, 0.0)
        reference_start = max(-self.sync_offset, 0.0)

        def prep(label, start, scale):
            chain = []
            trim = []
            if start > 0:
                trim.append(f"start={start:.6f}")
            if duration > 0:
                trim.append(f"duration={duration:.6f}")
            if trim:
                chain.append(f"trim={':'.join(trim)}")
            chain.extend(("settb=AVTB", "setpts=PTS-STARTPTS"))
            if scale and width > 0 and height > 0:
                chain.append(f"scale={width}:{height}:flags=bicubic")
            chain.append("format=yuv420p")
            return ",".join(chain) + f"[{label}]"

        return (
            f"[0:v]{prep('dist', encoded_start, True)};"
            f"[1:v]{prep('ref', reference_start, False)};"
            f"[dist][ref]{metric_filter}"
        )

    def _run_metric(self, key, label, metric_filter, parser):
        cmd = [
            FFMPEG, "-hide_banner", "-nostdin",
            "-i", self.encoded_path,
            "-i", self.reference_path,
            "-lavfi", self._filter_for(metric_filter),
            "-an", "-sn", "-f", "null", "-",
        ]
        self.log_output.emit(f"[Quality] {label}\n")
        self.log_output.emit(f"$ {' '.join(cmd)}\n")
        try:
            outcome = run_managed_process(
                cmd,
                cancel_event=self._cancel_event,
                timeout=self.metric_timeout,
            )
        except OSError as exc:
            return {
                "status": "failed",
                "message": str(exc),
                "command": cmd,
            }

        output = outcome.stdout + outcome.stderr
        for line in output.splitlines()[-12:]:
            self.log_output.emit(line + "\n")

        if outcome.cancelled:
            self._cancel_event.set()
            return {
                "status": "cancelled",
                "message": "Cancelled by user",
                "command": cmd,
            }
        if outcome.timed_out:
            return {
                "status": "timed_out",
                "message": f"Timed out after {self.metric_timeout:.0f} seconds",
                "command": cmd,
            }
        if outcome.returncode != 0:
            lowered = output.lower()
            if key == "vmaf" and ("no such filter" in lowered or "libvmaf" in lowered):
                return {
                    "status": "unavailable",
                    "message": "FFmpeg was built without libvmaf",
                    "command": cmd,
                }
            tail = " | ".join(line.strip() for line in output.splitlines()[-3:] if line.strip())
            return {
                "status": "failed",
                "message": tail[:180] or f"{label} failed",
                "command": cmd,
            }

        value = parser(output)
        if value is None:
            return {
                "status": "failed",
                "message": f"{label} score was not found in FFmpeg output",
                "command": cmd,
            }
        return {"status": "succeeded", "value": value, "command": cmd}

    def _ffmpeg_version(self):
        try:
            outcome = run_managed_process(
                [FFMPEG, "-version"],
                cancel_event=self._cancel_event,
                timeout=10,
            )
        except OSError as exc:
            return f"unavailable: {exc}"
        first_line = (outcome.stdout or outcome.stderr).splitlines()
        return first_line[0] if first_line else "unavailable"

    def run(self):
        diagnostic_id = _start_worker_diagnostics(
            self,
            "quality-comparison",
            ["quality-comparison", self.reference_path, self.encoded_path],
            context={
                "sync_offset_seconds": self.sync_offset,
                "metric_timeout_seconds": self.metric_timeout,
            },
        )
        if not FFMPEG:
            self.finished_signal.emit(False, "FFmpeg not found", {})
            return
        results = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reference": self.reference_path,
            "encoded": self.encoded_path,
            "reference_info": self.reference_info,
            "encoded_info": self.encoded_info,
            "sync_offset_seconds": self.sync_offset,
            "sync_policy": (
                "Positive offsets trim the encoded input; negative offsets trim "
                "the reference. Both timelines then start at zero, the encoded "
                "video is scaled to the reference dimensions, and comparison "
                "stops at the shorter remaining duration."
            ),
            "comparison_duration_seconds": self._comparison_duration(),
            "ffmpeg_version": self._ffmpeg_version(),
            "metric_timeout_seconds": self.metric_timeout,
            "metrics": {},
        }
        metrics = [
            ("vmaf", "VMAF", "libvmaf", parse_vmaf_score),
            ("psnr", "PSNR", "psnr", parse_psnr_average),
            ("ssim", "SSIM", "ssim", parse_ssim_all),
        ]
        try:
            for idx, (key, label, metric_filter, parser) in enumerate(metrics, start=1):
                if self._cancel_event.is_set():
                    results["metrics"][key] = {
                        "status": "cancelled",
                        "message": "Cancelled before metric started",
                    }
                    continue
                results["metrics"][key] = self._run_metric(
                    key, label, metric_filter, parser
                )
                self.progress.emit(idx / len(metrics) * 100)
            statuses = [
                results["metrics"].get(key, {}).get("status", "failed")
                for key in ("vmaf", "psnr", "ssim")
            ]
            succeeded = statuses.count("succeeded")
            if self._cancel_event.is_set() or "cancelled" in statuses:
                results["status"] = "cancelled"
                ok = False
                message = "Quality comparison cancelled"
            elif succeeded == len(statuses):
                results["status"] = "complete"
                ok = True
                message = "Quality comparison complete"
            elif succeeded:
                results["status"] = "partial"
                ok = True
                message = "Quality comparison completed with partial results"
            else:
                results["status"] = "failed"
                ok = False
                message = "No quality metrics could be computed"
            DIAGNOSTICS.update(
                diagnostic_id,
                comparison_status=results["status"],
                metric_statuses={
                    key: results["metrics"].get(key, {}).get("status")
                    for key in ("vmaf", "psnr", "ssim")
                },
            )
            self.finished_signal.emit(ok, message, results)
        except Exception as exc:
            results["status"] = "failed"
            DIAGNOSTICS.update(diagnostic_id, exception=type(exc).__name__)
            self.finished_signal.emit(False, str(exc), results)


# ---------------------------------------------------------------------------
# ThumbnailWorker
# ---------------------------------------------------------------------------


class ThumbnailWorker(QThread):
    """Extract a filmstrip in one cancellable FFmpeg decode pass."""
    thumbnails_ready = pyqtSignal(list)  # list of QPixmap

    def __init__(self, filepath, count=12, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.count = count
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        if not FFMPEG:
            return
        info = probe_video(self.filepath)
        if not info:
            return
        duration = info.get("duration", 0)
        if duration <= 0:
            return
        tmpdir = create_job_temp_dir("thumbs")
        try:
            output_pattern = str(Path(tmpdir) / "thumb_%03d.jpg")
            command = [
                FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                self.filepath,
                "-vf",
                f"fps={self.count / duration:.9f},scale=-2:44",
                "-frames:v",
                str(self.count),
                "-q:v",
                "4",
                "-y",
                output_pattern,
            ]
            outcome = run_managed_process(
                command,
                cancel_event=self._cancel_event,
                timeout=max(60, duration * 2),
            )
            if outcome.returncode != 0 or outcome.cancelled or outcome.timed_out:
                return
            thumbs = []
            for index in range(1, self.count + 1):
                path = Path(tmpdir) / f"thumb_{index:03d}.jpg"
                pixmap = QPixmap(str(path)) if path.exists() else QPixmap()
                thumbs.append(
                    pixmap.scaledToHeight(
                        44,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    if not pixmap.isNull()
                    else pixmap
                )
            self.thumbnails_ready.emit(thumbs)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            _unregister_temp_dir(tmpdir)


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
        diagnostic_id = _start_worker_diagnostics(
            self,
            "ai-upscale",
            [
                self.engine,
                self.input_path,
                self.output_path,
                self.model,
                str(self.scale),
            ],
            context={"output_validation": True},
        )
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
            upscaled_dir = os.path.join(tmpdir, "upscaled")
            os.makedirs(upscaled_dir)
            info = probe_video(self.input_path)
            if not info:
                self.finished_signal.emit(False, "Could not probe video")
                return
            fps = info.get("fps", 30)
            frame_cache = AIFrameCache()
            frames_dir = frame_cache.lookup(self.input_path)
            frame_cache_staging = None
            if frames_dir:
                self.log_output.emit(f"[1/3] Reusing {frames_dir.name} frame cache.\n")
            else:
                required = frame_cache.estimate_required_bytes(info)
                free = shutil.disk_usage(frame_cache.root).free
                if required > free * 0.9:
                    self.finished_signal.emit(
                        False,
                        f"Insufficient cache space: need about {required / 1024**3:.1f} GiB",
                    )
                    return
                frame_cache_staging = frame_cache.staging_dir(self.input_path)
                self.log_output.emit(
                    f"[1/3] Extracting reusable PNG frames "
                    f"(estimate {required / 1024**3:.1f} GiB)...\n"
                )
                extract_result = run_managed_process(
                    [
                        FFMPEG,
                        "-y",
                        "-i",
                        self.input_path,
                        os.path.join(frame_cache_staging, "frame_%06d.png"),
                    ],
                    cancel_event=self._cancel_event,
                    timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
                )
                if extract_result.cancelled:
                    self.finished_signal.emit(False, "Cancelled")
                    return
                if extract_result.returncode != 0:
                    self.finished_signal.emit(False, "Frame extraction failed")
                    return
                extracted = sorted(Path(frame_cache_staging).glob("*.png"))
                if not extracted:
                    self.finished_signal.emit(False, "No frames extracted")
                    return
                frames_dir = frame_cache.commit(
                    self.input_path,
                    frame_cache_staging,
                    len(extracted),
                )
                frame_cache_staging = None
            frames = sorted(Path(frames_dir).glob("*.png"))
            total = len(frames)
            if total == 0:
                self.finished_signal.emit(False, "No frames extracted")
                return
            self.log_output.emit(f"  Extracted {total} frames\n")
            self.progress.emit(10)

            self.log_output.emit(f"[2/3] Upscaling with {upscaler_name}...\n")
            cmd_up = [upscaler, "-i", str(frames_dir), "-o", upscaled_dir,
                      "-n", self.model, "-s", str(self.scale), "-f", "png"]
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
                cwd=str(Path(upscaler).parent),
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
                      "-i", os.path.join(upscaled_dir, "frame_%06d.png")]
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
            DIAGNOSTICS.update(
                diagnostic_id,
                output_valid=valid,
                output_validation_message=reason,
            )
            if not valid:
                self.finished_signal.emit(False, reason)
                return
            os.replace(staged_output, final_output)
            staged_output = None
            self.progress.emit(100)
            self.finished_signal.emit(True, "Upscale complete")
        except Exception as e:
            DIAGNOSTICS.update(diagnostic_id, exception=type(e).__name__)
            self.finished_signal.emit(False, str(e))
        finally:
            if "frame_cache_staging" in locals() and frame_cache_staging:
                shutil.rmtree(frame_cache_staging, ignore_errors=True)
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
        diagnostic_id = _start_worker_diagnostics(
            self,
            "frame-interpolation",
            [
                "rife",
                self.input_path,
                self.output_path,
                self.model,
                str(self.multiplier),
            ],
            context={"output_validation": True},
        )
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
            interp_dir = os.path.join(tmpdir, "interpolated")
            os.makedirs(interp_dir)
            info = probe_video(self.input_path)
            if not info:
                self.finished_signal.emit(False, "Could not probe video")
                return
            fps = info.get("fps", 30)
            new_fps = fps * self.multiplier
            frame_cache = AIFrameCache()
            frames_dir = frame_cache.lookup(self.input_path)
            frame_cache_staging = None
            if frames_dir:
                self.log_output.emit(f"[1/3] Reusing {frames_dir.name} frame cache.\n")
            else:
                required = frame_cache.estimate_required_bytes(info)
                free = shutil.disk_usage(frame_cache.root).free
                if required > free * 0.9:
                    self.finished_signal.emit(
                        False,
                        f"Insufficient cache space: need about {required / 1024**3:.1f} GiB",
                    )
                    return
                frame_cache_staging = frame_cache.staging_dir(self.input_path)
                self.log_output.emit(
                    f"[1/3] Extracting reusable PNG frames "
                    f"(estimate {required / 1024**3:.1f} GiB)...\n"
                )
                extract_result = run_managed_process(
                    [
                        FFMPEG,
                        "-y",
                        "-i",
                        self.input_path,
                        os.path.join(frame_cache_staging, "frame_%06d.png"),
                    ],
                    cancel_event=self._cancel_event,
                    timeout=max(3600, float(info.get("duration", 0) or 0) * 20),
                )
                if extract_result.cancelled:
                    self.finished_signal.emit(False, "Cancelled")
                    return
                if extract_result.returncode != 0:
                    self.finished_signal.emit(False, "Frame extraction failed")
                    return
                extracted = sorted(Path(frame_cache_staging).glob("*.png"))
                if not extracted:
                    self.finished_signal.emit(False, "No frames extracted")
                    return
                frames_dir = frame_cache.commit(
                    self.input_path,
                    frame_cache_staging,
                    len(extracted),
                )
                frame_cache_staging = None
            frames = sorted(Path(frames_dir).glob("*.png"))
            if len(frames) == 0:
                self.finished_signal.emit(False, "No frames extracted")
                return
            self.log_output.emit(f"  Extracted {len(frames)} frames\n")
            self.progress.emit(15)

            self.log_output.emit(f"[2/3] Interpolating {self.multiplier}x with RIFE...\n")
            cmd_rife = [rife, "-i", str(frames_dir), "-o", interp_dir,
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
                cwd=str(Path(rife).parent),
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
            DIAGNOSTICS.update(
                diagnostic_id,
                output_valid=valid,
                output_validation_message=reason,
            )
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
            DIAGNOSTICS.update(diagnostic_id, exception=type(e).__name__)
            self.finished_signal.emit(False, str(e))
        finally:
            if "frame_cache_staging" in locals() and frame_cache_staging:
                shutil.rmtree(frame_cache_staging, ignore_errors=True)
            if 'tmpdir' in locals():
                shutil.rmtree(tmpdir, ignore_errors=True)
                _unregister_temp_dir(tmpdir)
            if staged_output:
                try:
                    Path(staged_output).unlink(missing_ok=True)
                except OSError:
                    pass
