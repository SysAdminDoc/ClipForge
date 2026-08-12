"""Pure helpers for bounded, keyframe-tracked video redaction."""

from __future__ import annotations

import math
from collections.abc import Mapping

MAX_REDACTION_KEYFRAMES = 20
DEFAULT_REDACTION = {
    "enabled": False,
    "start": 0.0,
    "end": 1.0,
    "blur_radius": 6,
    "keyframes": [
        {"time": 0.0, "x": 0.35, "y": 0.25, "width": 0.3, "height": 0.3},
        {"time": 1.0, "x": 0.35, "y": 0.25, "width": 0.3, "height": 0.3},
    ],
}


def _finite(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value, low, high):
    return max(low, min(high, value))


def _normalize_keyframe(value, fallback):
    value = value if isinstance(value, Mapping) else {}
    width = _clamp(_finite(value.get("width"), fallback["width"]), 0.01, 1.0)
    height = _clamp(_finite(value.get("height"), fallback["height"]), 0.01, 1.0)
    return {
        "time": max(0.0, _finite(value.get("time"), fallback["time"])),
        "x": _clamp(_finite(value.get("x"), fallback["x"]), 0.0, 1.0 - width),
        "y": _clamp(_finite(value.get("y"), fallback["y"]), 0.0, 1.0 - height),
        "width": width,
        "height": height,
    }


def normalize_redaction_state(state=None):
    """Return safe, normalized redaction controls and at most 20 keyframes."""

    state = state if isinstance(state, Mapping) else {}
    start = max(0.0, _finite(state.get("start"), DEFAULT_REDACTION["start"]))
    end = max(start + 0.01, _finite(state.get("end"), DEFAULT_REDACTION["end"]))
    # FFmpeg's boxblur radius is bounded by the smallest cropped plane. Keep
    # the control conservative so small redaction boxes remain portable across
    # pixel formats and do not fail at render time.
    radius = int(_clamp(_finite(state.get("blur_radius"), 6), 1, 8))
    defaults = DEFAULT_REDACTION["keyframes"]
    raw_keyframes = state.get("keyframes")
    if not isinstance(raw_keyframes, list) or not raw_keyframes:
        raw_keyframes = defaults
    keyframes = []
    fallback = defaults[0]
    for value in raw_keyframes[:MAX_REDACTION_KEYFRAMES]:
        keyframe = _normalize_keyframe(value, fallback)
        keyframe["time"] = _clamp(keyframe["time"], start, end)
        keyframes.append(keyframe)
        fallback = keyframe
    keyframes.sort(key=lambda item: item["time"])
    deduplicated = []
    for keyframe in keyframes:
        if deduplicated and keyframe["time"] == deduplicated[-1]["time"]:
            deduplicated[-1] = keyframe
        else:
            deduplicated.append(keyframe)
    keyframes = deduplicated or [_normalize_keyframe(defaults[0], defaults[0])]
    if keyframes[0]["time"] > start:
        first = dict(keyframes[0])
        first["time"] = start
        keyframes.insert(0, first)
    if keyframes[-1]["time"] < end:
        last = dict(keyframes[-1])
        last["time"] = end
        keyframes.append(last)
    return {
        "enabled": bool(state.get("enabled", False)),
        "start": start,
        "end": end,
        "blur_radius": radius,
        "keyframes": keyframes[:MAX_REDACTION_KEYFRAMES],
    }


def _piecewise_expression(keyframes, field):
    """Build a linear interpolation expression using FFmpeg's frame time `t`."""

    if len(keyframes) == 1:
        return f"{keyframes[0][field]:.6f}"
    expression = f"{keyframes[-1][field]:.6f}"
    for left, right in reversed(list(zip(keyframes, keyframes[1:]))):
        left_time = left["time"]
        right_time = right["time"]
        if right_time <= left_time:
            segment = f"{right[field]:.6f}"
        else:
            delta = right[field] - left[field]
            segment = (
                f"({left[field]:.6f}+({delta:.6f})*"
                f"(t-{left_time:.6f})/{right_time - left_time:.6f})"
            )
        expression = f"if(lt(t,{right_time:.6f}),{segment},{expression})"
    return f"if(lt(t,{keyframes[0]['time']:.6f}),{keyframes[0][field]:.6f},{expression})"


def build_redaction_filter(state=None):
    """Build a single FFmpeg `-vf` graph for an interpolated blurred region."""

    normalized = normalize_redaction_state(state)
    if not normalized["enabled"]:
        return None
    keyframes = normalized["keyframes"]
    x = _piecewise_expression(keyframes, "x")
    y = _piecewise_expression(keyframes, "y")
    width = _piecewise_expression(keyframes, "width")
    height = _piecewise_expression(keyframes, "height")
    start = normalized["start"]
    end = normalized["end"]
    radius = normalized["blur_radius"]
    crop_width = f"min(iw,max(2,iw*{width}))"
    crop_height = f"min(ih,max(2,ih*{height}))"
    crop_x = f"iw*{x}"
    crop_y = f"ih*{y}"
    overlay_x = f"main_w*{x}"
    overlay_y = f"main_h*{y}"
    enabled = f"between(t,{start:.6f},{end:.6f})"
    return (
        "split=2[redact_base][redact_region];"
        f"[redact_region]crop=w='{crop_width}':h='{crop_height}':"
        f"x='{crop_x}':y='{crop_y}',"
        f"boxblur=luma_radius={radius}:luma_power=2:chroma_radius=0:alpha_radius=0"
        "[redact_blurred];"
        f"[redact_base][redact_blurred]overlay=x='{overlay_x}':y='{overlay_y}':"
        f"enable='{enabled}'"
    )


__all__ = [
    "DEFAULT_REDACTION",
    "MAX_REDACTION_KEYFRAMES",
    "build_redaction_filter",
    "normalize_redaction_state",
]
