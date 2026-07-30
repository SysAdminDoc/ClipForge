import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QProgressBar,
    QSlider,
    QTextEdit,
    QWidget,
)

from clipforge import APP_VERSION
from clipforge.app import MainWindow, apply_application_theme
from clipforge.constants import C
from clipforge.widgets import RangeSlider, Toast


_QT_APP = QApplication.instance() or QApplication([])


def _relative_luminance(hex_color):
    channels = [
        int(hex_color[index : index + 2], 16) / 255
        for index in (0, 2, 4)
    ]
    channels = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return (
        0.2126 * channels[0]
        + 0.7152 * channels[1]
        + 0.0722 * channels[2]
    )


def _contrast_ratio(first, second):
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


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


def test_long_toasts_preserve_the_actionable_beginning():
    parent = QWidget()
    parent.resize(1280, 860)
    toast = Toast(parent)
    message = "Reset preferences after quarantining malformed data as state.corrupt.json"
    toast.show_message(message)

    assert toast.text().startswith("Reset preferences")
    assert toast.text().endswith("...")
    assert toast.toolTip() == message
    assert toast.accessibleName() == message
    parent.close()


def test_desktop_panels_fit_1280_by_860_in_both_themes(monkeypatch):
    monkeypatch.setattr("clipforge.app.load_settings", lambda: {})
    monkeypatch.setattr(MainWindow, "_check_deps", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_recent", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_persistence_notices", lambda self: None)
    window = MainWindow()
    window.resize(1280, 860)
    window.show()
    _QT_APP.processEvents()

    try:
        for high_contrast in (False, True):
            apply_application_theme(_QT_APP, high_contrast)
            _QT_APP.processEvents()
            assert min(window.top_splitter.sizes()) >= 440

            for index, panel in enumerate(window._panels):
                window._switch_panel(index)
                _QT_APP.processEvents()
                scroll = window.stack.currentWidget()
                viewport = scroll.viewport()

                assert scroll.horizontalScrollBar().maximum() == 0
                assert panel.width() <= viewport.width()
                for control in panel.findChildren(
                    (
                        QAbstractButton,
                        QAbstractSpinBox,
                        QComboBox,
                        QLineEdit,
                        QSlider,
                        QTextEdit,
                    )
                ):
                    if not control.isVisibleTo(panel):
                        continue
                    top_left = control.mapTo(viewport, control.rect().topLeft())
                    bottom_right = control.mapTo(
                        viewport,
                        control.rect().bottomRight(),
                    )
                    assert 0 <= top_left.x()
                    assert bottom_right.x() < viewport.width()

                focusable = [
                    control
                    for control in panel.findChildren(QWidget)
                    if control.focusPolicy() != Qt.FocusPolicy.NoFocus
                    and control.isVisibleTo(panel)
                    and not control.objectName().startswith("qt_")
                ]
                visual_order = sorted(
                    focusable,
                    key=lambda control: (
                        control.mapTo(
                            panel,
                            control.rect().topLeft(),
                        ).y(),
                        control.mapTo(
                            panel,
                            control.rect().topLeft(),
                        ).x(),
                    ),
                )
                focus_order = []
                current = visual_order[0]
                seen = set()
                while current not in seen:
                    if current in focusable:
                        focus_order.append(current)
                        seen.add(current)
                    current = current.nextInFocusChain()
                assert focus_order == visual_order

            for control in window.player.findChildren(
                (QAbstractButton, QComboBox, QProgressBar, QSlider)
            ):
                if not control.isVisibleTo(window.player):
                    continue
                top_left = control.mapTo(
                    window.player,
                    control.rect().topLeft(),
                )
                bottom_right = control.mapTo(
                    window.player,
                    control.rect().bottomRight(),
                )
                assert 0 <= top_left.x()
                assert bottom_right.x() < window.player.width()
    finally:
        window.hide()
        window.deleteLater()
        _QT_APP.processEvents()


def test_desktop_named_controls_expose_accessible_values(monkeypatch):
    monkeypatch.setattr("clipforge.app.load_settings", lambda: {})
    monkeypatch.setattr(MainWindow, "_check_deps", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_recent", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_persistence_notices", lambda self: None)
    window = MainWindow()
    try:
        owners = [window, window.player, *window._panels]
        control_types = (
            QAbstractSpinBox,
            QComboBox,
            QLineEdit,
            QProgressBar,
            QSlider,
            QTextEdit,
        )
        for owner in owners:
            for attribute, control in vars(owner).items():
                if isinstance(control, control_types):
                    assert control.accessibleName().strip(), (
                        type(owner).__name__,
                        attribute,
                    )
                    if hasattr(control, "value"):
                        assert control.value() is not None
    finally:
        window.deleteLater()
        _QT_APP.processEvents()


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
    assert "Local FFmpeg module load" in script
    assert "import('./vendor/ffmpeg/ffmpeg/index.js')" in script
    assert "setAttribute('aria-valuenow'" in script


def test_browser_normal_text_tokens_meet_wcag_aa_contrast():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    tokens = dict(re.findall(r"--([\w-]+):\s*#([0-9a-fA-F]{6})", html))
    backgrounds = [tokens[f"bg-{index}"] for index in range(6)]

    for text_token in ("text-0", "text-1", "text-2", "text-3"):
        assert min(
            _contrast_ratio(tokens[text_token], background)
            for background in backgrounds
        ) >= 4.5

    for solid_token in ("accent-solid", "accent-solid-hover"):
        assert _contrast_ratio("ffffff", tokens[solid_token]) >= 4.5


def test_browser_project_and_export_contract_is_explicit():
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

    assert by_id["projectFileInput"][1]["accept"].startswith(".clipforge")
    assert by_id["relinkFileInput"][1]["multiple"] is None
    assert by_id["exportPreflight"][1]["aria-live"] == "polite"
    assert by_id["cancelExportButton"][0] == "button"
    assert "const PROJECT_SCHEMA_VERSION = 1" in script
    assert "Project schema v${source.version} is newer" in script
    assert "indexedDB.open(PROJECT_DB_NAME, 2)" in script
    assert "browserProxyKey" in script
    assert "Proxy cached and selected for preview" in script
    assert "Transitions are visible in the editor but are not yet rendered" in script
    assert "Unlinked audio and music tracks are not yet mixed" in script
    assert "sanitizeDownloadName" in script
    assert "job.engine?.terminate()" in script


def test_browser_csp_has_no_inline_handlers_or_remote_runtime():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "editor.js").read_text(encoding="utf-8")
    parser = _ElementParser()
    parser.feed(html)

    assert not [
        name
        for _tag, attrs in parser.elements
        for name in attrs
        if name.lower().startswith("on")
    ]
    csp = next(
        attrs["content"]
        for tag, attrs in parser.elements
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "https://" not in script
    assert "http://" not in script


def test_browser_project_import_remaps_untrusted_identifiers():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser project security test")
    root = Path(__file__).resolve().parents[1]
    runner = r"""
const fs = require('fs');
const vm = require('vm');
const context = {
    window: { addEventListener() {} },
    document: { addEventListener() {} },
    console: { log() {}, error() {}, warn() {} },
    setTimeout,
    clearTimeout,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('editor.js', 'utf8'), context);
const hostile = "x');globalThis.projectImportExecuted=true;//";
const project = context.normalizeProject({
    schema: 'clipforge.project',
    version: 1,
    name: '<img src=x onerror=globalThis.projectImportExecuted=true>',
    media: [{ id: hostile, name: '<svg onload=alert(1)>', type: 'video' }],
    clips: [
        { id: 'bad-one', mediaId: hostile, linkedTo: 'bad-two', name: '<b>first</b>' },
        { id: 'bad-two', mediaId: hostile, linkedTo: 'bad-one', name: '<b>second</b>' },
    ],
    transitions: [{ id: '\" onmouseover=\"alert(1)', type: 'dissolve' }],
});
process.stdout.write(JSON.stringify({
    mediaIds: project.media.map(item => item.id),
    clipIds: project.clips.map(item => item.id),
    mediaLinks: project.clips.map(item => item.mediaId),
    clipLinks: project.clips.map(item => item.linkedTo),
    transitionIds: project.transitions.map(item => item.id),
    executed: Boolean(context.projectImportExecuted),
}));
"""
    result = subprocess.run(
        [node, "-e", runner],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    normalized = json.loads(result.stdout)
    assert normalized == {
        "mediaIds": ["media-1"],
        "clipIds": ["clip-1", "clip-2"],
        "mediaLinks": ["media-1", "media-1"],
        "clipLinks": ["clip-2", "clip-1"],
        "transitionIds": ["transition-1"],
        "executed": False,
    }


def test_browser_project_values_are_rendered_as_data_not_markup():
    root = Path(__file__).resolve().parents[1]
    script = (root / "editor.js").read_text(encoding="utf-8")
    media_renderer = script.split("function renderMediaList()", 1)[1].split(
        "// ==================== TIMELINE CLIPS", 1
    )[0]
    timeline_renderer = script.split("function renderTimeline()", 1)[1].split(
        "function drawWaveform", 1
    )[0]

    assert "innerHTML" not in media_renderer
    assert "ondblclick" not in media_renderer
    assert "onclick" not in media_renderer
    assert "name.textContent = media.name" in media_renderer
    assert "proxyButton.addEventListener('click'" in media_renderer
    assert "clipEl.innerHTML" not in timeline_renderer
    assert "header.textContent = clip.name" in timeline_renderer
    assert "const canonicalIdMap" in script
