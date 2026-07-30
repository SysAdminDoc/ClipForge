import os
import subprocess
from pathlib import Path

import pytest

from clipforge.panels.batch import build_batch_output_path
from clipforge.panels.filters import FiltersPanel
from clipforge.panels.streams import StreamsPanel
from clipforge.tools import (
    FFMPEG,
    escape_ffmetadata_value,
    escape_ffmpeg_filter_value,
)


class _Control:
    def __init__(self):
        self.values = []

    def setRange(self, *value):
        self.values.append(value)

    def setEnabled(self, value):
        self.values.append(value)

    def setValue(self, value):
        self.values.append(value)

    def setText(self, value):
        self.values.append(value)


class _Signal:
    def __init__(self):
        self.messages = []

    def emit(self, *message):
        self.messages.append(message)


class _CaptionPanel:
    def __init__(self, tmpdir):
        self.progress = _Control()
        self.btn_gen_srt = _Control()
        self.requestToast = _Signal()
        self.lbl_sub_file = _Control()
        self._caption_tmpdir = tmpdir
        self._sub_path = None


def test_caption_commit_is_atomic_and_failure_preserves_existing_target(tmp_path):
    target = tmp_path / "captions.srt"
    target.write_bytes(b"existing")

    success_dir = tmp_path / ".caption-success"
    success_dir.mkdir()
    generated = success_dir / "source.srt"
    generated.write_bytes(b"new subtitles")
    panel = _CaptionPanel(success_dir)
    FiltersPanel._on_caption_done(
        panel, True, "", target, generated, success_dir
    )
    assert target.read_bytes() == b"new subtitles"
    assert not success_dir.exists()
    assert panel._sub_path == str(target)

    failed_dir = tmp_path / ".caption-failed"
    failed_dir.mkdir()
    partial = failed_dir / "source.srt"
    partial.write_bytes(b"partial")
    panel = _CaptionPanel(failed_dir)
    FiltersPanel._on_caption_done(
        panel, False, "cancelled", target, partial, failed_dir
    )
    assert target.read_bytes() == b"new subtitles"
    assert not failed_dir.exists()
    assert panel._sub_path is None


@pytest.mark.parametrize(
    "template",
    [
        "../escape{ext}",
        r"..\escape{ext}",
        "{unknown}{ext}",
        "{name.__class__}{ext}",
        "{name!r}{ext}",
        "{name:>20}{ext}",
    ],
)
def test_batch_output_rejects_paths_and_unsafe_template_fields(tmp_path, template):
    source = tmp_path / "source.mov"
    source.touch()
    with pytest.raises(ValueError):
        build_batch_output_path(
            source,
            "Convert to MP4 (H.264)",
            tmp_path / "outputs",
            template,
        )


def test_batch_output_is_a_direct_child_of_the_selected_directory(tmp_path):
    source = tmp_path / "source.mov"
    source.touch()
    output_dir = tmp_path / "outputs"
    output = Path(build_batch_output_path(
        source,
        "Convert to MP4 (H.264)",
        output_dir,
        "{index}-{name}{suffix}{ext}",
        index=6,
    ))
    assert output == output_dir.resolve() / "007-source_h264.mp4"


def test_chapter_titles_escape_ffmetadata_metacharacters(tmp_path):
    chapters = tmp_path / "chapters.txt"
    chapters.write_text(
        "00:00 Intro #1; key=value\\path\n00:01 Next\n",
        encoding="utf-8",
    )
    panel = type("_ChapterPanel", (), {"_info": {"duration": 2}})()
    metadata = StreamsPanel._parse_chapters(panel, chapters)
    assert r"title=Intro \#1\; key\=value\\path" in metadata
    assert "START=0" in metadata
    assert "END=1000" in metadata


def test_ffmetadata_values_preserve_unicode_and_escape_newlines():
    escaped = escape_ffmetadata_value("Résumé\\part\nnext=#tag;")
    assert escaped == "Résumé\\\\part\\\nnext\\=\\#tag\\;"


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required")
def test_subtitle_filter_accepts_hostile_but_valid_path_characters(tmp_path):
    hostile_dir = tmp_path / "odd Résumé ' [x],;=# dir"
    hostile_dir.mkdir()
    subtitle = hostile_dir / "caption's [1],;=#.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,500\nSafe caption\n",
        encoding="utf-8",
    )
    filter_value = escape_ffmpeg_filter_value(subtitle)
    result = subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:duration=1",
            "-vf",
            f"subtitles=filename={filter_value}",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=(
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ),
    )
    assert result.returncode == 0, result.stderr
