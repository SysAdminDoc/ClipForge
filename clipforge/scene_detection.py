"""Pure parsing and normalization for FFmpeg scene-change review markers."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PTS_TIME = re.compile(r"\bpts_time:(?P<time>\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class SceneMarker:
    time: float
    keep: bool = True


def parse_scene_markers(
    log_text: str,
    *,
    duration: float,
    minimum_gap: float = 0.25,
    max_markers: int = 2000,
) -> list[SceneMarker]:
    """Extract unique, bounded scene times from FFmpeg showinfo output."""

    duration = max(0.0, float(duration or 0))
    gap = max(0.01, float(minimum_gap))
    times = []
    for match in _PTS_TIME.finditer(str(log_text or "")):
        time_sec = max(0.0, min(duration, float(match.group("time"))))
        if times and time_sec - times[-1] < gap:
            continue
        times.append(time_sec)
        if len(times) >= max_markers:
            break
    return [SceneMarker(time) for time in times]


def normalize_scene_markers(markers, *, duration: float, max_markers: int = 2000):
    """Canonicalize editable marker dictionaries or numeric times."""

    duration = max(0.0, float(duration or 0))
    normalized = []
    for value in list(markers or [])[:max_markers]:
        if isinstance(value, dict):
            time_sec = value.get("time", 0)
            keep = bool(value.get("keep", True))
        elif isinstance(value, SceneMarker):
            time_sec = value.time
            keep = value.keep
        else:
            time_sec = value
            keep = True
        try:
            time_sec = max(0.0, min(duration, float(time_sec)))
        except (TypeError, ValueError):
            continue
        normalized.append(SceneMarker(time_sec, keep))
    normalized.sort(key=lambda marker: marker.time)
    deduplicated = []
    for marker in normalized:
        if deduplicated and abs(marker.time - deduplicated[-1].time) < 0.01:
            deduplicated[-1] = marker
        else:
            deduplicated.append(marker)
    return deduplicated[:max_markers]


__all__ = [
    "SceneMarker",
    "normalize_scene_markers",
    "parse_scene_markers",
]
