from clipforge.tools import probe_media, stream_copy_issues
from scripts.release_check import build_media_fixtures


def test_probe_exposes_rotation_chapters_disposition_and_time_base(tmp_path):
    fixtures = build_media_fixtures(tmp_path)

    rotated = probe_media(str(fixtures["rotation"]))
    chaptered = probe_media(str(fixtures["chapters"]))

    assert rotated.error is None
    assert rotated.info["rotation"] == 90.0
    assert rotated.info["streams"][0]["time_base"]
    assert rotated.info["streams"][0]["disposition"]["default"] == 1
    assert chaptered.error is None
    assert len(chaptered.info["chapters"]) == 2
    assert chaptered.info["chapters"][0]["tags"]["title"] == "Intro"
    audio = next(
        stream
        for stream in chaptered.info["streams"]
        if stream["codec_type"] == "audio"
    )
    assert audio["channel_layout"] == "mono"
    assert audio["sample_rate"]


def test_probe_returns_actionable_error_for_invalid_media(tmp_path):
    invalid = tmp_path / "not-media.mp4"
    invalid.write_text("not a media file", encoding="utf-8")

    result = probe_media(str(invalid))

    assert result.info is None
    assert result.error.code == "probe_failed"
    assert "could not read" in result.error.message.lower()
    assert result.error.details


def test_stream_copy_preflight_is_container_specific():
    streams = [
        {"index": 2, "codec_type": "video", "codec_name": "h264"},
        {"index": 5, "codec_type": "audio", "codec_name": "aac"},
        {"index": 8, "codec_type": "subtitle", "codec_name": "subrip"},
    ]

    assert stream_copy_issues("MKV", streams) == []
    mp4_issues = stream_copy_issues("MP4", streams)
    assert len(mp4_issues) == 1
    assert "Stream #8" in mp4_issues[0]
    assert stream_copy_issues("WebM", streams[:2])
    assert stream_copy_issues("MP4", []) == ["Select at least one stream."]
