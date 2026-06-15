"""Tests for utility functions — imports from clipforge_utils.py (no PyQt6 dependency)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipforge_utils import (
    _parse_fps, format_duration, format_duration_short,
    format_size, format_bitrate, estimate_output_size,
    _sanitize_preset_name, validate_media_path,
)


def _build_atempo(speed):
    """Replicate atempo logic from clipforge.py for testing."""
    atempo_parts = []
    atempo_val = speed
    if atempo_val < 0.5:
        while atempo_val < 0.5:
            atempo_parts.append("atempo=0.5")
            atempo_val /= 0.5
        atempo_parts.append(f"atempo={atempo_val:.4f}")
    elif atempo_val > 2.0:
        while atempo_val > 2.0:
            atempo_parts.append("atempo=2.0")
            atempo_val /= 2.0
        atempo_parts.append(f"atempo={atempo_val:.4f}")
    else:
        atempo_parts.append(f"atempo={atempo_val:.4f}")
    return ",".join(atempo_parts)


def _build_eq_filter(brightness=0, contrast=0, saturation=100, gamma=100):
    """Replicate eq filter logic from clipforge.py for testing."""
    eq_parts = []
    if brightness != 0:
        eq_parts.append(f"brightness={brightness/100:.2f}")
    if contrast != 0:
        eq_parts.append(f"contrast={1 + contrast/100:.2f}")
    if saturation != 100:
        eq_parts.append(f"saturation={saturation/100:.2f}")
    if gamma != 100:
        eq_parts.append(f"gamma={gamma/100:.2f}")
    if eq_parts:
        return f"eq={':'.join(eq_parts)}"
    return None


# === Tests ===

class TestParseFps:
    def test_fraction(self):
        assert _parse_fps("30000/1001") == 29.97

    def test_integer_fraction(self):
        assert _parse_fps("24/1") == 24.0

    def test_float_string(self):
        assert _parse_fps("29.97") == 29.97

    def test_zero_denominator(self):
        assert _parse_fps("0/0") == 0.0

    def test_empty(self):
        assert _parse_fps("") == 0.0

    def test_garbage(self):
        assert _parse_fps("abc") == 0.0


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "00:00:00.000"

    def test_negative(self):
        assert format_duration(-5) == "00:00:00.000"

    def test_seconds(self):
        assert format_duration(65.5) == "00:01:05.500"

    def test_hours(self):
        assert format_duration(3723.25) == "01:02:03.250"


class TestFormatDurationShort:
    def test_zero(self):
        assert format_duration_short(0) == "0:00"

    def test_minutes(self):
        assert format_duration_short(125) == "2:05"

    def test_hours(self):
        assert format_duration_short(3661) == "1:01:01"


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500.0 B"

    def test_megabytes(self):
        result = format_size(1024 * 1024 * 5)
        assert "5.0 MB" == result

    def test_gigabytes(self):
        result = format_size(1024 * 1024 * 1024 * 2)
        assert "2.0 GB" == result


class TestFormatBitrate:
    def test_zero(self):
        assert format_bitrate(0) == "N/A"

    def test_kbps(self):
        assert format_bitrate(192000) == "192 kbps"

    def test_mbps(self):
        assert format_bitrate(5000000) == "5.0 Mbps"


class TestEstimateOutputSize:
    def test_positive(self):
        result = estimate_output_size(60, 18, 1920, 1080)
        assert result > 0

    def test_higher_crf_smaller(self):
        high = estimate_output_size(60, 18, 1920, 1080)
        low = estimate_output_size(60, 28, 1920, 1080)
        assert high > low

    def test_longer_larger(self):
        short = estimate_output_size(30, 18, 1920, 1080)
        long = estimate_output_size(120, 18, 1920, 1080)
        assert long > short


class TestSanitizePresetName:
    def test_clean(self):
        assert _sanitize_preset_name("My Preset") == "My Preset"

    def test_slashes(self):
        assert "/" not in _sanitize_preset_name("my/preset")
        assert "\\" not in _sanitize_preset_name("my\\preset")

    def test_null_bytes(self):
        result = _sanitize_preset_name("test\x00bad")
        assert "\x00" not in result

    def test_empty(self):
        assert _sanitize_preset_name("") == "preset"

    def test_dots_only(self):
        assert _sanitize_preset_name("...") == "preset"

    def test_long_name(self):
        assert len(_sanitize_preset_name("x" * 200)) <= 100


class TestValidateMediaPath:
    def test_none(self):
        assert validate_media_path(None) is False

    def test_empty(self):
        assert validate_media_path("") is False

    def test_null_byte(self):
        assert validate_media_path("/tmp/test\x00.mp4") is False

    def test_nonexistent(self):
        assert validate_media_path("/nonexistent/path/video.mp4") is False

    def test_existing_file(self):
        assert validate_media_path(__file__) is True


class TestAtempo:
    def test_normal_speed(self):
        assert _build_atempo(1.5) == "atempo=1.5000"

    def test_slow_speed(self):
        result = _build_atempo(0.25)
        assert "atempo=0.5" in result
        parts = result.split(",")
        assert len(parts) >= 2

    def test_fast_speed(self):
        result = _build_atempo(4.0)
        assert "atempo=2.0" in result
        parts = result.split(",")
        assert len(parts) == 2

    def test_very_fast(self):
        result = _build_atempo(8.0)
        parts = result.split(",")
        assert len(parts) == 3

    def test_boundary_2x(self):
        result = _build_atempo(2.0)
        assert result == "atempo=2.0000"


class TestEqFilter:
    def test_no_changes(self):
        assert _build_eq_filter() is None

    def test_brightness_only(self):
        result = _build_eq_filter(brightness=50)
        assert result == "eq=brightness=0.50"

    def test_combined(self):
        result = _build_eq_filter(brightness=50, contrast=20, saturation=150, gamma=80)
        assert result is not None
        assert "brightness=0.50" in result
        assert "contrast=1.20" in result
        assert "saturation=1.50" in result
        assert "gamma=0.80" in result
        assert result.count("eq=") == 1

    def test_saturation_default(self):
        result = _build_eq_filter(saturation=100)
        assert result is None
