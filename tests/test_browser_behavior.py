import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from clipforge.tools import FFMPEG


ROOT = Path(__file__).resolve().parents[1]
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _IsolatedHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "application/javascript",
    }

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()

    def log_message(self, _format, *_args):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionError, OSError):
            pass


@pytest.fixture(scope="session")
def browser_app_url():
    handler = partial(_IsolatedHandler, directory=ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def chromium_browser():
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as error:
        playwright.stop()
        pytest.fail(
            "Chromium is required for browser behavior tests. "
            "Run `python -m playwright install chromium`. "
            f"Launch error: {error}"
        )
    try:
        yield browser
    finally:
        browser.close()
        playwright.stop()


@pytest.fixture
def browser_page(chromium_browser, browser_app_url):
    context = chromium_browser.new_context(viewport={"width": 900, "height": 700})
    page = context.new_page()
    page.goto(browser_app_url, wait_until="domcontentloaded")
    page.wait_for_function("() => window.clipforgeEditorReady === true")
    try:
        yield page
    finally:
        context.close()


def _project(name, *, media=None, clips=None, transitions=None):
    return {
        "schema": "clipforge.project",
        "version": 1,
        "name": name,
        "media": media or [],
        "clips": clips or [],
        "transitions": transitions or [],
        "timeline": {"pixelsPerSecond": 50, "trackStates": {}},
    }


def _upload_project(page, payload, name="project.clipforge"):
    page.locator("#projectFileInput").set_input_files(
        {
            "name": name,
            "mimeType": "application/json",
            "buffer": json.dumps(payload).encode(),
        }
    )


def _project_with_missing_clip(name="Timeline Project"):
    return _project(
        name,
        media=[
            {
                "id": "media-source",
                "name": "source.png",
                "type": "image",
                "duration": 5,
                "width": 1,
                "height": 1,
                "reference": {
                    "name": "source.png",
                    "size": len(PNG_BYTES),
                    "lastModified": 0,
                    "mime": "image/png",
                },
            }
        ],
        clips=[
            {
                "id": "clip-source",
                "mediaId": "media-source",
                "track": "video",
                "startTime": 0,
                "duration": 5,
                "inPoint": 0,
                "outPoint": 5,
                "name": "Source clip",
                "type": "image",
            }
        ],
    )


def test_malicious_project_import_is_normalized_and_rendered_as_text(browser_page):
    hostile = "<img src=x onerror=globalThis.projectImportExecuted=true>"
    payload = _project(
        hostile,
        media=[{"id": "x');alert(1)//", "name": "<svg onload=alert(1)>"}],
    )
    _upload_project(browser_page, payload)

    browser_page.wait_for_function(
        "(name) => document.querySelector('.project-name')?.textContent === name",
        arg=hostile,
    )
    assert browser_page.locator(".project-name").inner_text() == hostile
    assert browser_page.locator(".project-name img").count() == 0
    assert not browser_page.evaluate("Boolean(globalThis.projectImportExecuted)")


def test_legacy_project_migrates_and_relinks_matching_local_media(browser_page):
    legacy = {
        "mediaItems": [
            {
                "id": "legacy-media",
                "name": "source.png",
                "type": "image",
                "size": len(PNG_BYTES),
                "lastModified": 0,
                "duration": 5,
            }
        ],
        "clips": [
            {
                "id": "legacy-clip",
                "mediaId": "legacy-media",
                "track": "video",
                "startTime": 0,
                "duration": 5,
                "inPoint": 0,
                "outPoint": 5,
            }
        ],
    }
    _upload_project(browser_page, legacy, "legacy.clipforge")
    browser_page.locator(".media-item.missing").wait_for()
    assert browser_page.locator(".media-item.missing").count() == 1

    browser_page.locator("#relinkFileInput").set_input_files(
        {"name": "source.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    browser_page.locator(".media-item:not(.missing)").wait_for()
    assert browser_page.locator(".media-item.missing").count() == 0


def test_timeline_resolver_is_shared_by_preview_and_export_plan(browser_page):
    payload = _project(
        "Resolver Project",
        media=[
            {
                "id": "media-a",
                "name": "first.mp4",
                "type": "video",
                "duration": 12,
                "width": 320,
                "height": 180,
                "reference": {"name": "first.mp4", "mime": "video/mp4"},
            },
            {
                "id": "media-b",
                "name": "second.mp4",
                "type": "video",
                "duration": 12,
                "width": 320,
                "height": 180,
                "reference": {"name": "second.mp4", "mime": "video/mp4"},
            },
        ],
        clips=[
            {
                "id": "video-a",
                "mediaId": "media-a",
                "track": "video",
                "startTime": 0,
                "duration": 2,
                "inPoint": 3,
                "outPoint": 5,
                "name": "First video",
                "type": "video",
                "linkedTo": "audio-a",
            },
            {
                "id": "audio-a",
                "mediaId": "media-a",
                "track": "audio",
                "startTime": 0,
                "duration": 2,
                "inPoint": 3,
                "outPoint": 5,
                "name": "First audio",
                "type": "audio",
                "linkedTo": "video-a",
                "volume": 80,
            },
            {
                "id": "video-b",
                "mediaId": "media-b",
                "track": "video",
                "startTime": 2,
                "duration": 3,
                "inPoint": 1,
                "outPoint": 4,
                "name": "Second video",
                "type": "video",
                "linkedTo": "audio-b",
            },
            {
                "id": "audio-b",
                "mediaId": "media-b",
                "track": "audio",
                "startTime": 2,
                "duration": 3,
                "inPoint": 1,
                "outPoint": 4,
                "name": "Second audio",
                "type": "audio",
                "linkedTo": "video-b",
            },
        ],
    )
    _upload_project(browser_page, payload)
    browser_page.wait_for_function(
        "() => document.querySelector('.project-name')?.textContent === 'Resolver Project'"
    )

    resolved = browser_page.evaluate(
        """
        () => {
            const atStart = window.resolveTimelineAtTime(0.5);
            const atSecond = window.resolveTimelineAtTime(2.5);
            const plan = window.buildResolvedTimelinePlan();
            const preflight = window.buildExportPreflight();
            return {
                atStart: {
                    video: atStart.video && {
                        name: atStart.video.clip.name,
                        sourceTime: atStart.video.sourceTime,
                    },
                    audio: atStart.audio.map(entry => ({
                        name: entry.clip.name,
                        sourceTime: entry.sourceTime,
                        volume: entry.volume,
                    })),
                },
                atSecond: {
                    name: atSecond.video?.clip.name,
                    sourceTime: atSecond.video?.sourceTime,
                },
                plan: plan.videoSegments.map(segment => ({
                    name: segment.clip.name,
                    timelineStart: segment.timelineStart,
                    sourceStart: segment.sourceStart,
                    duration: segment.duration,
                })),
                exportPlan: preflight.timelinePlan.videoSegments.map(segment => ({
                    name: segment.clip.name,
                    timelineStart: segment.timelineStart,
                    sourceStart: segment.sourceStart,
                    duration: segment.duration,
                })),
            };
        }
        """
    )
    assert resolved["atStart"] == {
        "video": {"name": "First video", "sourceTime": 3.5},
        "audio": [{"name": "First audio", "sourceTime": 3.5, "volume": 80}],
    }
    assert resolved["atSecond"] == {"name": "Second video", "sourceTime": 1.5}
    assert resolved["plan"] == [
        {"name": "First video", "timelineStart": 0, "sourceStart": 3, "duration": 2},
        {"name": "Second video", "timelineStart": 2, "sourceStart": 1, "duration": 3},
    ]
    assert resolved["exportPlan"] == resolved["plan"]


def test_project_switch_clears_history_after_dirty_confirmation(browser_page):
    browser_page.locator("#fileInput").set_input_files(
        {"name": "first.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    browser_page.locator(".media-item").dblclick()
    browser_page.locator(".clip").wait_for()
    assert browser_page.locator("html").get_attribute("data-project-dirty") == "true"

    browser_page.once("dialog", lambda dialog: dialog.accept())
    _upload_project(browser_page, _project("Clean replacement"))
    browser_page.wait_for_function(
        "() => document.querySelector('.project-name')?.textContent === 'Clean replacement'"
    )
    assert browser_page.locator(".project-name").inner_text() == "Clean replacement"
    assert browser_page.locator(".clip").count() == 0
    browser_page.keyboard.press("Control+z")
    assert browser_page.locator(".clip").count() == 0
    assert browser_page.locator("html").get_attribute("data-project-dirty") == "false"


def test_invalid_media_import_fails_without_creating_project_media(browser_page):
    browser_page.locator("#fileInput").set_input_files(
        {"name": "broken.mp4", "mimeType": "video/mp4", "buffer": b"not video"}
    )
    browser_page.get_by_text(
        "Could not import broken.mp4: the browser could not decode its metadata"
    ).wait_for()
    assert browser_page.locator(".media-item").count() == 0


def test_media_metadata_import_timeout_is_actionable(
    chromium_browser,
    browser_app_url,
):
    context = chromium_browser.new_context(viewport={"width": 900, "height": 700})
    context.add_init_script(
        """
        globalThis.CLIPFORGE_METADATA_TIMEOUT_MS = 100;
        const createElement = Document.prototype.createElement;
        Document.prototype.createElement = function(name, options) {
            const element = createElement.call(this, name, options);
            if (name === 'video' || name === 'audio') {
                Object.defineProperty(element, 'src', {
                    configurable: true,
                    get() { return ''; },
                    set() {},
                });
            }
            return element;
        };
        """
    )
    page = context.new_page()
    page.goto(browser_app_url, wait_until="domcontentloaded")
    page.wait_for_function("() => window.clipforgeEditorReady === true")
    try:
        page.locator("#fileInput").set_input_files(
            {"name": "stalled.mp4", "mimeType": "video/mp4", "buffer": b"pending"}
        )
        page.get_by_text(
            "Could not import stalled.mp4: Metadata import for stalled.mp4 "
            "timed out after 0 seconds"
        ).wait_for(timeout=5000)
        assert page.locator(".media-item").count() == 0
    finally:
        context.close()


def test_export_preflight_and_modal_focus_contract(browser_page):
    _upload_project(browser_page, _project_with_missing_clip())
    browser_page.wait_for_function(
        "() => document.querySelector('.project-name')?.textContent === 'Timeline Project'"
    )
    export_button = browser_page.locator("#exportButton")
    export_button.click()
    modal = browser_page.locator("#exportModal")
    assert modal.get_attribute("aria-hidden") == "false"
    assert browser_page.evaluate("document.activeElement.id") == "exportFormat"
    assert "needs its source relinked" in browser_page.locator(
        "#exportPreflight"
    ).inner_text()
    assert not browser_page.locator("#confirmExportButton").is_enabled()

    browser_page.keyboard.press("Shift+Tab")
    assert browser_page.evaluate("document.activeElement.dataset.action") == "hide-export"
    browser_page.keyboard.press("Escape")
    assert modal.get_attribute("aria-hidden") == "true"
    assert browser_page.evaluate("document.activeElement.id") == "exportButton"


def test_quota_failure_surfaces_recovery_warning(chromium_browser, browser_app_url):
    context = chromium_browser.new_context(viewport={"width": 900, "height": 700})
    context.add_init_script(
        """
        Object.defineProperty(window, 'indexedDB', {
            configurable: true,
            value: {
                open() {
                    throw new DOMException('quota exhausted', 'QuotaExceededError');
                },
            },
        });
        """
    )
    page = context.new_page()
    page.goto(browser_app_url, wait_until="domcontentloaded")
    page.wait_for_function("() => window.clipforgeEditorReady === true")
    try:
        page.locator("#fileInput").set_input_files(
            {"name": "quota.png", "mimeType": "image/png", "buffer": PNG_BYTES}
        )
        page.get_by_text(
            "Browser storage could not save recovery data; export the project file now."
        ).wait_for(timeout=5000)
    finally:
        context.close()


def test_browser_proxy_cache_uses_sampled_identity_and_explicit_purge(browser_page):
    identity = browser_page.evaluate(
        """
        async () => {
            const first = new File(
                [new Uint8Array(192).fill(1)],
                'clip.mp4',
                {type: 'video/mp4', lastModified: 123},
            );
            const changed = new File(
                [new Uint8Array(192).fill(2)],
                'clip.mp4',
                {type: 'video/mp4', lastModified: 123},
            );
            return {
                first: await window.browserProxyKey(first),
                changed: await window.browserProxyKey(changed),
            };
        }
        """
    )
    assert identity["first"] != identity["changed"]

    browser_page.evaluate(
        """
        async () => {
            const db = await new Promise((resolve, reject) => {
                const request = indexedDB.open('clipforge-recovery', 2);
                request.onupgradeneeded = () => {
                    if (!request.result.objectStoreNames.contains('projects')) {
                        request.result.createObjectStore('projects');
                    }
                    if (!request.result.objectStoreNames.contains('proxies')) {
                        request.result.createObjectStore('proxies');
                    }
                };
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            await new Promise((resolve, reject) => {
                const request = db.transaction('proxies', 'readwrite')
                    .objectStore('proxies')
                    .put({
                        key: 'test-proxy',
                        profile: 2,
                        complete: true,
                        size: 4,
                        blob: new Blob([new Uint8Array([1, 2, 3, 4])]),
                        createdAt: 1,
                    }, 'test-proxy');
                request.onsuccess = resolve;
                request.onerror = () => reject(request.error);
            });
            db.close();
            await window.refreshBrowserProxyCacheStatus();
        }
        """
    )
    browser_page.wait_for_function(
        "() => document.querySelector('#browserProxyCacheText')?.textContent.includes('1 entry')"
    )
    browser_page.locator('[data-action="purge-browser-cache"]').click()
    browser_page.wait_for_function(
        "() => document.querySelector('#browserProxyCacheText')?.textContent.includes('0 entries')"
    )
    assert browser_page.evaluate("async () => (await window.browserProxyCacheStats()).bytes") == 0


def test_browser_diagnostics_are_bounded_and_redacted(browser_page):
    browser_page.evaluate(
        """
        () => window.dispatchEvent(new ErrorEvent('error', {
            message: 'Could not fetch https://user:password@example.com/media.mp4?token=browser-secret',
            error: new Error('Could not fetch https://user:password@example.com/media.mp4?token=browser-secret'),
        }))
        """
    )
    diagnostics = browser_page.evaluate(
        "async () => window.buildBrowserDiagnostics()"
    )
    serialized = json.dumps(diagnostics)
    assert "browser-secret" not in serialized
    assert "password" not in serialized
    assert diagnostics["schema"] == "clipforge.browser-diagnostics"
    assert diagnostics["privacy"] == {
        "redacted": True,
        "localPathsIncluded": False,
        "mediaContentsIncluded": False,
        "privateMediaMetadataIncluded": False,
        "urlCredentialsIncluded": False,
        "urlTokensIncluded": False,
    }
    assert "capabilities" in diagnostics
    assert "storage" in diagnostics
    assert len(diagnostics["errors"]) <= 50


def test_every_track_is_reachable_at_900_by_700(browser_page):
    layout = browser_page.evaluate(
        """
        () => {
            const music = document.querySelector('#musicTrack');
            const controls = document.querySelector('.timeline-toolbar');
            const trackControls = [...document.querySelectorAll('[data-track-control]')]
                .map(control => {
                    const bounds = control.getBoundingClientRect();
                    return {
                        label: control.getAttribute('aria-label'),
                        visible: Boolean(control.offsetWidth || control.offsetHeight),
                        left: bounds.left,
                        right: bounds.right,
                        top: bounds.top,
                        bottom: bounds.bottom,
                    };
                });
            return {
                viewport: [innerWidth, innerHeight],
                documentWidth: document.documentElement.scrollWidth,
                musicBottom: music.getBoundingClientRect().bottom,
                controlsBottom: controls.getBoundingClientRect().bottom,
                trackControls,
            };
        }
        """
    )
    assert layout["viewport"] == [900, 700]
    assert layout["documentWidth"] <= 900
    assert layout["musicBottom"] <= 700
    assert layout["controlsBottom"] <= 700
    assert len(layout["trackControls"]) == 6
    assert all(
        control["visible"]
        and control["label"]
        and 0 <= control["left"] < control["right"] <= 900
        and 0 <= control["top"] < control["bottom"] <= 700
        for control in layout["trackControls"]
    )


def test_proof_locale_translates_static_controls_without_layout_overflow(
    chromium_browser,
    browser_app_url,
):
    context = chromium_browser.new_context(viewport={"width": 1280, "height": 860})
    context.add_init_script("window.CLIPFORGE_LOCALE = 'en-XA';")
    page = context.new_page()
    page.goto(browser_app_url, wait_until="domcontentloaded")
    page.wait_for_function("() => window.clipforgeEditorReady === true")
    try:
        assert page.locator("html").get_attribute("data-locale") == "en-XA"
        assert page.locator('[data-i18n="openProject"]').inner_text().startswith("⟦")
        assert page.locator('[data-i18n="selectClip"]').inner_text().startswith("⟦")
        layout = page.evaluate(
            """
            () => ({
                documentWidth: document.documentElement.scrollWidth,
                viewportWidth: window.innerWidth,
                visibleRight: [...document.querySelectorAll(
                    '.menu-bar, .header, .media-panel, .center-area, .properties-panel'
                )].filter(element => element.offsetParent !== null)
                    .map(element => Math.round(element.getBoundingClientRect().right)),
            })
            """
        )
        assert layout["documentWidth"] <= layout["viewportWidth"]
        assert max(layout["visibleRight"]) <= layout["viewportWidth"]
    finally:
        context.close()


def test_media_clips_and_context_actions_are_keyboard_operable(browser_page):
    browser_page.locator("#fileInput").set_input_files(
        {"name": "keyboard.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    media = browser_page.locator(".media-item").first
    media.wait_for()
    assert media.get_attribute("role") == "button"
    assert media.get_attribute("aria-label").startswith(
        "Add keyboard.png (image,"
    )
    media.focus()
    media.press("Enter")

    clip = browser_page.locator(".clip").first
    clip.wait_for()
    assert clip.get_attribute("role") == "button"
    assert "video track" in clip.get_attribute("aria-label")
    clip.focus()
    clip.press(" ")
    assert browser_page.locator(".clip").first.get_attribute("aria-pressed") == "true"

    clip = browser_page.locator(".clip").first
    clip.focus()
    clip.press("Shift+F10")
    menu = browser_page.locator("#contextMenu")
    assert menu.get_attribute("aria-hidden") == "false"
    assert browser_page.evaluate(
        "document.activeElement?.dataset.action"
    ) == "cut-clip"

    browser_page.keyboard.press("ArrowDown")
    assert browser_page.evaluate(
        "document.activeElement?.dataset.action"
    ) == "copy-clip"
    browser_page.keyboard.press("End")
    assert browser_page.evaluate(
        "document.activeElement?.dataset.action"
    ) == "unlink-audio"
    browser_page.keyboard.press("Home")
    assert browser_page.evaluate(
        "document.activeElement?.dataset.action"
    ) == "cut-clip"
    browser_page.keyboard.press("Escape")
    assert menu.get_attribute("aria-hidden") == "true"
    assert browser_page.evaluate(
        "document.activeElement?.classList.contains('clip')"
    )

    browser_page.locator(".clip").first.press("Shift+F10")
    browser_page.keyboard.press("ArrowDown")
    browser_page.keyboard.press("Enter")
    assert menu.get_attribute("aria-hidden") == "true"
    browser_page.get_by_text("Copied to clipboard").wait_for()


def test_edit_menu_actions_and_timeline_tools_are_truthful(browser_page):
    browser_page.locator("#fileInput").set_input_files(
        {"name": "tools.png", "mimeType": "image/png", "buffer": PNG_BYTES}
    )
    browser_page.locator(".media-item").dblclick()
    clip = browser_page.locator(".clip").first
    clip.wait_for()
    clip.click()

    edit_button = browser_page.locator('[data-action="show-edit-menu"]')
    menu = browser_page.locator("#editMenu")
    edit_button.click()
    assert menu.get_attribute("aria-hidden") == "false"
    assert not browser_page.locator("#editUndo").is_disabled()
    assert not browser_page.locator("#editCopy").is_disabled()
    assert browser_page.evaluate("document.activeElement?.id") == "editUndo"
    browser_page.keyboard.press("ArrowDown")
    assert browser_page.evaluate("document.activeElement?.id") == "editCut"
    browser_page.keyboard.press("Escape")
    assert menu.get_attribute("aria-hidden") == "true"
    assert browser_page.evaluate("document.activeElement?.dataset.action") == "show-edit-menu"

    edit_button.click()
    browser_page.locator("#editCopy").click()
    browser_page.get_by_text("Copied to clipboard").wait_for()
    edit_button.click()
    assert not browser_page.locator("#editPaste").is_disabled()
    browser_page.locator("#editPaste").click()
    assert browser_page.locator(".clip").count() == 2

    browser_page.locator('[data-tool="slip"]').click()
    browser_page.locator(".clip").last.click()
    browser_page.evaluate("window.updateClipProperty('duration', 3)")
    before = browser_page.evaluate(
        "window.serializeProject().clips.filter(clip => clip.track === 'video').at(-1)"
    )
    slip_clip = browser_page.locator(".clip").last
    bounds = slip_clip.bounding_box()
    assert bounds
    browser_page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + 12)
    browser_page.mouse.down()
    browser_page.mouse.move(bounds["x"] + bounds["width"] / 2 + 50, bounds["y"] + 12)
    browser_page.mouse.up()
    after = browser_page.evaluate(
        "window.serializeProject().clips.filter(clip => clip.track === 'video').at(-1)"
    )
    assert after["startTime"] == before["startTime"]
    assert after["duration"] == before["duration"]
    assert after["inPoint"] > before["inPoint"]
    assert after["outPoint"] == after["inPoint"] + after["duration"]

    browser_page.locator('[data-tool="hand"]').click()
    browser_page.locator("#zoomSlider").evaluate(
        "el => { el.value = '200'; el.dispatchEvent(new Event('input', { bubbles: true })); }"
    )
    container = browser_page.locator("#tracksContainer")
    assert browser_page.evaluate(
        "() => { const el = document.querySelector('#tracksContainer'); return el.scrollWidth > el.clientWidth; }"
    )
    container_bounds = container.bounding_box()
    assert container_bounds
    start_x = container_bounds["x"] + container_bounds["width"] / 2
    start_y = container_bounds["y"] + 20
    browser_page.mouse.move(start_x, start_y)
    browser_page.mouse.down()
    assert browser_page.locator("#tracksContainer").evaluate(
        "el => el.classList.contains('panning')"
    )
    browser_page.mouse.move(start_x - 100, start_y)
    browser_page.mouse.up()
    assert browser_page.locator("#tracksContainer").evaluate("el => el.scrollLeft > 0")


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is required for browser job media")
def test_media_job_exclusion_and_cancel_recover_engine(
    browser_page,
    tmp_path,
):
    source = tmp_path / "proxy-source.mp4"
    result = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    browser_page.wait_for_function(
        "() => window.getBrowserFfmpegJobState?.().engineReady === true",
        timeout=90000,
    )
    browser_page.locator("#fileInput").set_input_files(str(source))
    proxy_button = browser_page.locator(".media-proxy-btn")
    proxy_button.wait_for(timeout=15000)
    proxy_button.click()
    browser_page.wait_for_function(
        "() => window.getBrowserFfmpegJobState().active?.type === 'proxy'",
        timeout=10000,
    )
    assert not browser_page.locator("#exportButton").is_enabled()
    browser_page.once("dialog", lambda dialog: dialog.accept())
    _upload_project(browser_page, _project("After cancellation"))
    browser_page.wait_for_function(
        """
        () => {
            const state = window.getBrowserFfmpegJobState();
            return state.active === null
                && state.last?.state === 'cancelled'
                && state.engineReady === true
                && document.querySelector('.project-name')?.textContent
                    === 'After cancellation';
        }
        """,
        timeout=120000,
    )
