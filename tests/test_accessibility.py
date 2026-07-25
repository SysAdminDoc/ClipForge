import os
from html.parser import HTMLParser
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from clipforge import APP_VERSION
from clipforge.app import apply_application_theme
from clipforge.constants import C
from clipforge.widgets import RangeSlider


_QT_APP = QApplication.instance() or QApplication([])


class _ElementParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_range_slider_has_focusable_keyboard_handles():
    slider = RangeSlider()
    slider.set_limits(0, 10)
    slider.set_range(1, 9)
    slider.show()
    slider.setFocus()

    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert slider.low() > 1
    QTest.keyClick(slider, Qt.Key.Key_Up)
    previous_end = slider.high()
    QTest.keyClick(slider, Qt.Key.Key_Left)

    assert slider.high() < previous_end
    assert slider.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert "Start" in slider.accessibleDescription()
    slider.close()


def test_application_theme_can_switch_live():
    apply_application_theme(_QT_APP, True)
    assert C["base"] == "#1a1a1a"
    assert "#ffffff" in _QT_APP.styleSheet()

    apply_application_theme(_QT_APP, False)
    assert C["base"] == "#1e1e2e"
    assert "#cdd6f4" in _QT_APP.styleSheet()


def test_browser_tabs_labels_live_regions_and_900px_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "editor.js").read_text(encoding="utf-8")
    parser = _ElementParser()
    parser.feed(html)
    by_id = {
        attrs["id"]: (tag, attrs)
        for tag, attrs in parser.elements
        if attrs.get("id")
    }

    tabs = [
        attrs
        for tag, attrs in parser.elements
        if tag == "button" and attrs.get("role") == "tab"
    ]
    assert len(tabs) == 5
    for tab in tabs:
        assert tab["aria-controls"] in by_id
        assert tab["aria-selected"] in {"true", "false"}

    icon_buttons = [
        attrs
        for tag, attrs in parser.elements
        if tag == "button"
        and (
            "transport-btn" in attrs.get("class", "")
            or "tool-btn" in attrs.get("class", "")
            or "track-btn" in attrs.get("class", "")
        )
    ]
    assert icon_buttons
    assert all(button.get("aria-label") for button in icon_buttons)
    assert by_id["loadingOverlay"][1]["aria-live"] == "polite"
    assert by_id["loadingProgressTrack"][1]["role"] == "progressbar"
    assert by_id["toastContainer"][1]["aria-live"] == "polite"
    assert "@media (max-width: 900px)" in html
    assert f'src="editor.js?v={APP_VERSION}" defer' in html
    assert "activatePanelTab" in script
    assert "withTimeout(" in script
    assert "FFmpeg module download" in script
    assert "setAttribute('aria-valuenow'" in script
