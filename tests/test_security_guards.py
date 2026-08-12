"""Security regression tests for external media runtimes."""

from clipforge import tools
from clipforge.runtime_policy import (
    evaluate_ffmpeg_runtime,
    evaluate_nvdec,
    evaluate_qt_runtime,
    policy_manifest,
)


class _ProcessResult:
    def __init__(self, *, returncode=0, stdout="", stderr="", cancelled=False, timed_out=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cancelled = cancelled
        self.timed_out = timed_out


def test_ffmpeg_version_parser_handles_release_and_git_banners():
    assert tools.parse_ffmpeg_version("ffmpeg version 8.1.2-full_build") == (8, 1, 2)
    assert tools.parse_ffmpeg_version("ffmpeg version n9.0 Copyright") == (9, 0, 0)
    assert tools.parse_ffmpeg_version("ffmpeg version N-119999-gabcdef") is None


def test_nvdec_guard_fails_closed_through_affected_release():
    assert not tools.nvdec_decode_is_safe("")
    assert not tools.nvdec_decode_is_safe("ffmpeg version N-119999-gabcdef")
    assert not tools.nvdec_decode_is_safe("ffmpeg version 4.4")
    assert not tools.nvdec_decode_is_safe("ffmpeg version 8.1.2-full_build")
    assert tools.nvdec_decode_is_safe("ffmpeg version 8.1.3")
    assert tools.nvdec_decode_is_safe("ffmpeg version 9.0")


def test_nvdec_guard_accepts_build_banner_with_upstream_fix():
    banner = f"ffmpeg version N-120000-g{tools.NVDEC_FIX_COMMIT}"
    assert tools.nvdec_decode_is_safe(banner)


def test_nvenc_keeps_encoding_but_uses_safe_decode_fallback(monkeypatch):
    monkeypatch.setattr(tools, "CUDA_NVDEC_SAFE", False)
    assert tools.hardware_decode_args("h264_nvenc") == []

    monkeypatch.setattr(tools, "CUDA_NVDEC_SAFE", True)
    assert tools.hardware_decode_args("h264_nvenc") == [
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
    ]


def test_hardware_probe_runs_a_real_encoder_check_and_caches_by_binary(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _ProcessResult()

    monkeypatch.setattr("clipforge.processes.run_managed_process", fake_run)
    tools.clear_hw_capability_cache()

    first = tools.probe_hw_encoder(
        "h264_nvenc",
        "ffmpeg-test",
        version="ffmpeg version 9.0",
    )
    second = tools.probe_hw_encoder(
        "h264_nvenc",
        "ffmpeg-test",
        version="ffmpeg version 9.0",
    )

    assert first["status"] == "usable"
    assert second["cached"] is True
    assert len(calls) == 1
    assert calls[0][-5:] == ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-f", "null", "-"][-5:]


def test_hardware_probe_reports_driver_failure_reason(monkeypatch):
    monkeypatch.setattr(
        "clipforge.processes.run_managed_process",
        lambda *_args, **_kwargs: _ProcessResult(
            returncode=1,
            stderr="Cannot load NVENC driver",
        ),
    )
    tools.clear_hw_capability_cache()
    result = tools.probe_hw_encoder("h264_nvenc", "ffmpeg-test", version="ffmpeg version 9.0")
    assert result["status"] == "unavailable"
    assert "NVENC driver" in result["reason"]


def test_ffmpeg_security_policy_uses_patched_branch_floors():
    assert evaluate_ffmpeg_runtime("ffmpeg version 8.0.2").status == "blocked"
    assert evaluate_ffmpeg_runtime("ffmpeg version 8.0.3").status == "supported"
    assert evaluate_ffmpeg_runtime("ffmpeg version 5.1.10").status == "blocked"
    assert evaluate_ffmpeg_runtime("ffmpeg version N-123-gabcdef").status == "unknown"
    assert evaluate_ffmpeg_runtime("ffmpeg version 9.0").status == "supported"


def test_nvdec_policy_explains_conservative_boundary():
    assert evaluate_nvdec("ffmpeg version 8.1.2").status == "blocked"
    assert evaluate_nvdec("ffmpeg version 8.1.3").status == "supported"
    assert evaluate_nvdec("ffmpeg version N-123-gabcdef").status == "unknown"


def test_qt_policy_requires_the_locked_security_runtime():
    assert evaluate_qt_runtime("6.11.1").status == "supported"
    assert evaluate_qt_runtime("6.11.0").status == "blocked"
    assert evaluate_qt_runtime("").status == "unknown"


def test_runtime_policy_manifest_is_versioned_and_serializable():
    manifest = policy_manifest()
    assert manifest["schema"] == "clipforge.runtime-policy"
    assert manifest["version"] == 1
    assert manifest["reviewed_at"] == "2026-08-11"
    assert manifest["qt"]["security_minimum"] == "6.11.1"
