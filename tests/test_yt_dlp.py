import pytest

from clipforge.yt_dlp import (
    build_yt_dlp_command,
    validate_download_path,
    validate_source_url,
)


def test_yt_dlp_command_is_single_video_and_folder_scoped(tmp_path):
    command = build_yt_dlp_command(
        "yt-dlp.exe",
        "https://example.com/watch?v=abc",
        tmp_path,
    )
    assert "--no-playlist" in command
    assert "--restrict-filenames" in command
    assert "%(title).150B.%(ext)s" in command
    assert command[-1] == "https://example.com/watch?v=abc"


def test_yt_dlp_url_policy_rejects_local_and_credential_urls():
    with pytest.raises(ValueError, match="http"):
        validate_source_url("file:///C:/secret.mp4")
    with pytest.raises(ValueError, match="credentials"):
        validate_source_url("https://user:password@example.com/video")


def test_download_output_must_be_a_supported_file_inside_destination(tmp_path):
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"video")
    assert validate_download_path(output, tmp_path) == output.resolve()
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"video")
    assert validate_download_path(outside, tmp_path) is None
