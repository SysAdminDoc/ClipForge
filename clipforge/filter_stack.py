"""Pure filter-stack ordering and graph helpers."""

from __future__ import annotations

from collections.abc import Iterable

FILTER_STACK_DEFAULT = (
    "color",
    "deinterlace",
    "denoise",
    "sharpen",
    "lut",
    "subtitles",
)
FILTER_STACK_LABELS = {
    "color": "Color correction",
    "deinterlace": "Deinterlace",
    "denoise": "Noise reduction",
    "sharpen": "Sharpen",
    "lut": "LUT grading",
    "subtitles": "Subtitle burn-in",
}


def normalize_filter_order(order: Iterable[str] | None) -> list[str]:
    """Return a complete, duplicate-free order with unknown ids removed."""

    seen = set()
    normalized = []
    for item in order or ():
        item = str(item)
        if item in FILTER_STACK_LABELS and item not in seen:
            seen.add(item)
            normalized.append(item)
    normalized.extend(item for item in FILTER_STACK_DEFAULT if item not in seen)
    return normalized


def reorder_filter_stack(order: Iterable[str] | None, source: int, target: int) -> list[str]:
    values = normalize_filter_order(order)
    if not 0 <= source < len(values):
        return values
    target = max(0, min(int(target), len(values) - 1))
    item = values.pop(source)
    values.insert(target, item)
    return values


def filter_graph(filters: Iterable[str], audio_filters: Iterable[str] = ()) -> str:
    video = [str(item) for item in filters if str(item)]
    audio = [str(item) for item in audio_filters if str(item)]
    video_text = " → ".join(video) if video else "passthrough"
    if audio:
        return f"[video] → {video_text} → [output]  |  [audio] → {' → '.join(audio)} → [output]"
    return f"[video] → {video_text} → [output]"


__all__ = [
    "FILTER_STACK_DEFAULT",
    "FILTER_STACK_LABELS",
    "filter_graph",
    "normalize_filter_order",
    "reorder_filter_stack",
]
