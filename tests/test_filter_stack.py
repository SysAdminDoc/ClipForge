import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from clipforge.filter_stack import (
    filter_graph,
    normalize_filter_order,
    reorder_filter_stack,
)
from clipforge.panels.filters import FiltersPanel
from clipforge.redaction import build_redaction_filter, normalize_redaction_state


_QT_APP = QApplication.instance() or QApplication([])


def test_filter_stack_order_is_complete_and_reorderable():
    order = normalize_filter_order(["subtitles", "subtitles", "unknown", "color"])
    assert order[0:2] == ["subtitles", "color"]
    moved = reorder_filter_stack(order, 0, 5)
    assert moved[-1] == "subtitles"
    assert "[video] →" in filter_graph(["eq=contrast=1.2", "yadif"], ["loudnorm=I=-14"])


def test_filters_panel_uses_stack_order_in_generated_video_graph():
    panel = FiltersPanel(None)
    panel.chk_deinterlace.setChecked(True)
    panel.chk_sharpen.setChecked(True)
    panel._filter_stack_order = ["sharpen", "deinterlace"]
    video, _audio = panel._build_filters(update_graph=False)
    assert video == ["unsharp=5:5:1.0", "yadif"]
    panel.close()


def test_silence_markers_are_editable_and_unchecked_ranges_are_not_removed():
    panel = FiltersPanel(None)
    panel._populate_silence_markers([(1.0, 2.0, True), (3.0, 4.0, True)])
    assert panel.tbl_silence_markers.rowCount() == 2
    panel.tbl_silence_markers.item(0, 1).setText("1.500")
    panel.tbl_silence_markers.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    panel._sync_silence_segments()
    assert panel._silence_segments == [(3.0, 4.0)]
    assert "1 of 2" in panel.lbl_silence_result.text()
    panel.close()


def test_redaction_keyframes_are_clamped_and_compile_to_motion_filter():
    state = normalize_redaction_state({
        "enabled": True,
        "start": 2,
        "end": 4,
        "blur_radius": 99,
        "keyframes": [
            {"time": 2, "x": 0.9, "y": -1, "width": 0.5, "height": 0.25},
            {"time": 4, "x": 0.2, "y": 0.3, "width": 0.25, "height": 0.4},
        ],
    })
    assert state["blur_radius"] == 8
    assert state["keyframes"][0]["x"] == 0.5
    graph = build_redaction_filter(state)
    assert "split=2[redact_base][redact_region]" in graph
    assert "boxblur=luma_radius=8" in graph
    assert "overlay=" in graph
    assert "if(lt(t,4.000000)" in graph


def test_filters_panel_persists_redaction_controls_and_graph():
    panel = FiltersPanel(None)
    panel.chk_redaction.setChecked(True)
    panel.spn_redaction_end.setValue(3.0)
    panel.spn_redaction_end_x.setValue(60.0)
    video, audio = panel._build_filters(update_graph=False)
    assert not audio
    assert len(video) == 1
    assert "redact_region" in video[0]
    state = panel.project_state()
    assert state["redaction"]["enabled"] is True
    assert state["redaction"]["end"] == 3.0
    panel._reset_to_defaults()
    assert panel.chk_redaction.isChecked() is False
    panel.restore_project_state(state)
    assert panel.chk_redaction.isChecked() is True
    assert panel.spn_redaction_end.value() == 3.0
    panel.close()
