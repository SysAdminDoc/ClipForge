from scripts.release_check import build_media_fixtures, exercise_media_operations


def test_generated_media_fixture_matrix(tmp_path):
    fixtures = build_media_fixtures(tmp_path)
    assert set(fixtures) == {
        "audio_video",
        "video_only",
        "subtitles",
        "chapters",
        "rotation",
        "vfr",
    }
    exercise_media_operations(tmp_path, fixtures)
