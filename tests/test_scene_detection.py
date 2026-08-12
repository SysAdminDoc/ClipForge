from clipforge.scene_detection import normalize_scene_markers, parse_scene_markers


def test_scene_log_parser_deduplicates_close_showinfo_frames():
    log = "showinfo n:0 pts_time:0.50\nshowinfo pts_time:0.60\nshowinfo pts_time:2.25\n"
    markers = parse_scene_markers(log, duration=3.0, minimum_gap=0.25)
    assert [marker.time for marker in markers] == [0.5, 2.25]
    assert all(marker.keep for marker in markers)


def test_scene_marker_normalization_clamps_and_preserves_review_choice():
    markers = normalize_scene_markers(
        [{"time": 99, "keep": False}, {"time": 1.0, "keep": True}],
        duration=2.0,
    )
    assert [(marker.time, marker.keep) for marker in markers] == [
        (1.0, True),
        (2.0, False),
    ]
