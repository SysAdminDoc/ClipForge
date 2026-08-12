import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
