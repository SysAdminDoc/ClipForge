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
