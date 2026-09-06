#!/usr/bin/env python3
"""Capture repeatable browser and desktop product screenshots offscreen."""

from __future__ import annotations

import argparse
import ctypes
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets" / "screenshots"
DEMO_STILL = ROOT / "assets" / "demo" / "coastal-drive.jpg"


def run(command: list[str | Path], *, timeout: int = 120) -> None:
    result = subprocess.run(
        [str(part) for part in command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(details or f"Command failed: {' '.join(map(str, command))}")


def build_demo_video(output: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to build the screenshot fixture")
    run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            DEMO_STILL,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000",
            "-vf",
            (
                "scale=1280:720,"
                "zoompan=z='min(zoom+0.00035,1.06)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                "d=192:s=1280x720:fps=24,format=yuv420p"
            ),
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            output,
        ],
        timeout=180,
    )
    return output


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


def capture_browser(media: Path, output: Path) -> None:
    from playwright.sync_api import sync_playwright

    handler = functools.partial(QuietHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            page.goto(
                f"http://127.0.0.1:{server.server_port}/?capture=browser",
                wait_until="domcontentloaded",
            )
            page.locator("#statusText").get_by_text("Ready", exact=True).wait_for(
                timeout=45_000
            )
            page.locator("#fileInput").set_input_files(str(media))
            item = page.locator(".media-item").first
            item.wait_for(state="visible", timeout=20_000)
            item.dblclick()
            clip = page.locator(".clip:not(.audio-clip)").first
            clip.wait_for(state="visible", timeout=20_000)
            clip.click()
            page.locator(".project-name").evaluate(
                "(element) => { element.textContent = 'Coastal Drive'; }"
            )
            page.wait_for_timeout(5_000)
            page.screenshot(path=str(output))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def capture_desktop(media: Path, output: Path) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.SetProcessDPIAware()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    with tempfile.TemporaryDirectory(prefix="clipforge-marketing-config-") as config:
        os.environ["CLIPFORGE_CONFIG_DIR"] = config

        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QFontDatabase, QPixmap
        from PyQt6.QtWidgets import QApplication, QLabel

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from clipforge.app import MainWindow, apply_application_theme
        from clipforge.settings import load_settings

        app = QApplication([])
        if sys.platform == "win32":
            fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
                QFontDatabase.addApplicationFont(str(fonts / name))
        apply_application_theme(app, load_settings().get("high_contrast", False))

        window = MainWindow()
        window._update_check_timer.stop()
        window.resize(1440, 900)
        window.show()
        state = {"saved": False}

        def save_capture() -> None:
            if state["saved"]:
                return
            state["saved"] = True
            window._switch_panel(3)
            window.player.player.setPosition(2400)
            window.console.clear()
            window._console_lines.clear()
            preview = QLabel(window.player.video_widget)
            preview.setGeometry(window.player.video_widget.rect())
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setStyleSheet("background: #09090b; border-radius: 8px;")
            preview.setPixmap(
                QPixmap(str(DEMO_STILL)).scaled(
                    preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            preview.show()
            preview.raise_()
            app.processEvents()
            if not window.grab().save(str(output), "PNG"):
                app.exit(1)
                return
            window.close()
            app.quit()

        def timed_out() -> None:
            if not state["saved"]:
                state["saved"] = True
                window.close()
                app.exit(1)

        window.file_bar.fileLoaded.connect(
            lambda *_args: QTimer.singleShot(3_500, save_capture)
        )
        window.file_bar.load_file(str(media))
        QTimer.singleShot(20_000, timed_out)
        if app.exec() != 0 or not output.is_file():
            raise RuntimeError("Desktop screenshot capture did not complete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--desktop-only", action="store_true")
    args = parser.parse_args()
    if args.browser_only and args.desktop_only:
        parser.error("Choose at most one capture filter")
    if not DEMO_STILL.is_file():
        raise RuntimeError(f"Demo still is missing: {DEMO_STILL}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clipforge-marketing-media-") as temp:
        media = build_demo_video(Path(temp) / "Coastal Drive Master.mp4")
        if not args.desktop_only:
            capture_browser(media, output_dir / "browser-editor.png")
        if not args.browser_only:
            capture_desktop(media, output_dir / "desktop-editor.png")
    print(f"Captured ClipForge marketing screenshots in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
