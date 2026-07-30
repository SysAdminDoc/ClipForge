"""Security regression tests for external media runtimes."""

from clipforge import tools


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
