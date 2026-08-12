"""Run the evaluation-only Mediabunny/WebCodecs browser benchmark."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge.tools import FFMPEG  # noqa: E402


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


def _make_fixture(path: Path):
    if not FFMPEG:
        raise RuntimeError("FFmpeg is required to generate the benchmark fixture")
    result = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr)


def run(source: Path, start: float, end: float | None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required; install requirements-dev.lock") from error

    handler = partial(_IsolatedHandler, directory=ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/mediabunny-spike.html",
                    wait_until="domcontentloaded",
                )
                page.wait_for_function(
                    "() => window.clipforgeMediabunnySpikeReady === true"
                )
                page.locator("#sourceFile").set_input_files(str(source))
                page.locator("#trimStart").fill(str(start))
                if end is not None:
                    page.locator("#trimEnd").fill(str(end))
                page.locator("#runBenchmark").click()
                page.wait_for_function(
                    "() => !document.querySelector('#results').textContent.includes('No benchmark')",
                    timeout=240000,
                )
                return json.loads(page.locator("#results").inner_text())
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Short local MP4; otherwise generate one")
    parser.add_argument("--start", type=float, default=0.5)
    parser.add_argument("--end", type=float, default=2.5)
    args = parser.parse_args()
    if args.source:
        source = args.source.resolve()
        if not source.is_file():
            parser.error(f"Source does not exist: {source}")
        print(json.dumps(run(source, args.start, args.end), indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="clipforge-mediabunny-") as directory:
        source = Path(directory) / "fixture.mp4"
        _make_fixture(source)
        print(json.dumps(run(source, args.start, args.end), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
