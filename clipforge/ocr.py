"""Pure OCR-to-SubRip helpers used by the optional local Tesseract worker."""

from __future__ import annotations

import re
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class OCRSegment:
    start: float
    end: float
    text: str


def parse_tesseract_tsv(tsv_text: str, *, min_confidence: float = 35.0) -> str:
    """Extract readable lines from Tesseract TSV output, grouped by line."""

    lines = {}
    for raw_line in str(tsv_text or "").splitlines():
        if not raw_line or raw_line.startswith("level\t"):
            continue
        fields = raw_line.split("\t")
        if len(fields) < 12:
            continue
        try:
            confidence = float(fields[10])
        except (TypeError, ValueError):
            continue
        text = _WHITESPACE.sub(" ", fields[11]).strip()
        if not text or confidence < min_confidence:
            continue
        key = tuple(fields[index] for index in (1, 2, 3, 4))
        lines.setdefault(key, []).append((int(fields[5] or 0), text))
    ordered = []
    for words in lines.values():
        words.sort(key=lambda item: item[0])
        line = _WHITESPACE.sub(" ", " ".join(word for _order, word in words)).strip()
        if line:
            ordered.append(line)
    return "\n".join(ordered)


def merge_ocr_observations(
    observations,
    *,
    duration: float,
    sample_interval: float,
    max_segments: int = 2000,
) -> list[OCRSegment]:
    """Turn sampled OCR text into stable, non-overlapping subtitle segments."""

    duration = max(0.0, float(duration or 0))
    interval = max(0.01, float(sample_interval or 0))
    normalized = []
    for time_sec, text in observations:
        text = _WHITESPACE.sub(" ", str(text or "").replace("\n", " ")).strip()
        if not text:
            continue
        time_sec = max(0.0, min(duration, float(time_sec)))
        if normalized and normalized[-1][0] == time_sec:
            normalized[-1] = (time_sec, text)
        else:
            normalized.append((time_sec, text))
    segments = []
    current_text = None
    current_start = 0.0
    last_seen = 0.0
    for time_sec, text in normalized:
        if current_text is None:
            current_text = text
            current_start = time_sec
        elif text != current_text:
            end = min(duration, max(current_start + 0.01, time_sec))
            segments.append(OCRSegment(current_start, end, current_text))
            current_text = text
            current_start = time_sec
        last_seen = time_sec
    if current_text is not None:
        end = min(duration, max(current_start + 0.01, last_seen + interval))
        segments.append(OCRSegment(current_start, end, current_text))
    return segments[:max_segments]


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds or 0) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def format_srt(segments: list[OCRSegment]) -> str:
    """Format OCR segments as bounded UTF-8 SubRip text."""

    blocks = []
    previous_end = 0.0
    for index, segment in enumerate(segments, start=1):
        text = _WHITESPACE.sub(" ", str(segment.text or "")).strip()
        if not text:
            continue
        start = max(previous_end, float(segment.start))
        end = max(start + 0.01, float(segment.end))
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )
        previous_end = end
    return "\n".join(blocks)


def output_srt_path(path: str | Path) -> Path:
    """Return a normalized `.srt` destination without changing its directory."""

    target = Path(path).expanduser()
    return target if target.suffix.lower() == ".srt" else target.with_suffix(".srt")


def find_tesseract() -> str | None:
    """Locate an optional local Tesseract executable without downloading it."""

    candidates = []
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append(on_path)
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(str(Path(root) / "Tesseract-OCR" / "tesseract.exe"))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


__all__ = [
    "OCRSegment",
    "format_srt",
    "find_tesseract",
    "merge_ocr_observations",
    "output_srt_path",
    "parse_tesseract_tsv",
]
