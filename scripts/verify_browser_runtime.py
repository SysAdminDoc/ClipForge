"""Verify ClipForge's pinned, same-origin browser runtime inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge.runtime_policy import policy_manifest  # noqa: E402

VENDOR_ROOT = ROOT / "vendor" / "ffmpeg"
SBOM_PATH = VENDOR_ROOT / "sbom.json"
INVENTORY_DATE = "2026-07-29"
FFMPEG_WASM_COMMIT = "f876f907c7e9b9bf51d4ed0b913a855a63ae63fc"
FFMPEG_COMMIT = "19feb712f5c1821d8a3fa1ad63c5bd2e3b9672eb"

COMPONENTS = [
    {
        "name": "@ffmpeg/ffmpeg",
        "version": "0.12.15",
        "license": "MIT",
        "source": "https://github.com/ffmpegwasm/ffmpeg.wasm",
        "commit": FFMPEG_WASM_COMMIT,
        "modified": "Default core URL points to ClipForge's local patched core.",
    },
    {
        "name": "@ffmpeg/util",
        "version": "0.12.2",
        "license": "MIT",
        "source": "https://github.com/ffmpegwasm/ffmpeg.wasm",
        "commit": FFMPEG_WASM_COMMIT,
    },
    {
        "name": "FFmpeg",
        "version": "n5.1.10",
        "license": "GPL-2.0-or-later",
        "source": "https://github.com/FFmpeg/FFmpeg",
        "commit": FFMPEG_COMMIT,
        "build": {
            "ffmpeg_wasm_commit": FFMPEG_WASM_COMMIT,
            "emscripten": "3.1.40",
            "target": "single-thread",
            "flags": "-O3 -msimd128",
        },
    },
    {
        "name": "x264",
        "version": "4-cores@33cac6b77d5b9259c552156013a817ab23119612",
        "license": "GPL-2.0-or-later",
        "source": "https://github.com/ffmpegwasm/x264",
    },
    {
        "name": "x265",
        "version": "3.4@2bb5520e9596f361bf0ed81b3b8da0d7fd999069",
        "license": "GPL-2.0-or-later",
        "source": "https://github.com/ffmpegwasm/x265",
    },
    {
        "name": "libvpx",
        "version": "1.13.1",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/libvpx",
    },
    {
        "name": "LAME",
        "version": "master@2badea1974ae36cb8312afe99cff1e6b3b5decee",
        "license": "LGPL-2.0-or-later",
        "source": "https://github.com/ffmpegwasm/lame",
    },
    {
        "name": "libogg",
        "version": "1.3.4",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/Ogg",
    },
    {
        "name": "libtheora",
        "version": "1.1.1",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/theora",
    },
    {
        "name": "libvorbis",
        "version": "1.3.3",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/vorbis",
    },
    {
        "name": "Opus",
        "version": "1.3.1",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/opus",
    },
    {
        "name": "zlib",
        "version": "1.2.11",
        "license": "Zlib",
        "source": "https://github.com/ffmpegwasm/zlib",
    },
    {
        "name": "libwebp",
        "version": "1.3.2",
        "license": "BSD-3-Clause",
        "source": "https://github.com/ffmpegwasm/libwebp",
    },
    {
        "name": "FreeType",
        "version": "2.10.4",
        "license": "FTL OR GPL-2.0-only",
        "source": "https://github.com/ffmpegwasm/freetype2",
    },
    {
        "name": "FriBidi",
        "version": "1.0.9",
        "license": "LGPL-2.1-or-later",
        "source": "https://github.com/fribidi/fribidi",
    },
    {
        "name": "HarfBuzz",
        "version": "5.2.0",
        "license": "MIT",
        "source": "https://github.com/harfbuzz/harfbuzz",
    },
    {
        "name": "libass",
        "version": "0.15.0",
        "license": "ISC",
        "source": "https://github.com/libass/libass",
    },
    {
        "name": "zimg",
        "version": "3.0.5",
        "license": "WTFPL",
        "source": "https://github.com/sekrit-twc/zimg",
    },
]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_files():
    return sorted(
        path
        for path in VENDOR_ROOT.rglob("*")
        if path.is_file() and path != SBOM_PATH
    )


def build_inventory():
    return {
        "schema": "clipforge.browser-runtime.sbom",
        "schema_version": 1,
        "generated_at": INVENTORY_DATE,
        "runtime_policy": policy_manifest(),
        "components": COMPONENTS,
        "artifacts": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in _runtime_files()
        ],
    }


class _InlineHandlerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.handlers = []

    def handle_starttag(self, tag, attrs):
        for name, _value in attrs:
            if name.lower().startswith("on"):
                self.handlers.append((tag, name))


def verify_inventory():
    expected = json.loads(SBOM_PATH.read_text(encoding="utf-8"))
    if expected.get("runtime_policy") != policy_manifest():
        raise RuntimeError(
            "Browser runtime security policy is stale; run "
            "`python scripts/verify_browser_runtime.py --write`"
        )
    actual = build_inventory()
    if expected != actual:
        raise RuntimeError(
            "Browser runtime inventory is stale; run "
            "`python scripts/verify_browser_runtime.py --write`"
        )

    html = (ROOT / "index.html").read_text(encoding="utf-8")
    editor = (ROOT / "editor.js").read_text(encoding="utf-8")
    bootstrap = (ROOT / "bootstrap.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "coi-serviceworker.js").read_text(encoding="utf-8")
    parser = _InlineHandlerParser()
    parser.feed(html)
    if parser.handlers:
        raise RuntimeError(f"Inline event handlers violate CSP: {parser.handlers}")
    csp_match = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"',
        html,
    )
    if not csp_match:
        raise RuntimeError("Content Security Policy meta tag is missing")
    csp = csp_match.group(1)
    if "script-src 'self' 'wasm-unsafe-eval'" not in csp:
        raise RuntimeError("CSP does not constrain scripts to the local WASM runtime")
    if "'unsafe-inline'" in csp.split("script-src", 1)[1].split(";", 1)[0]:
        raise RuntimeError("CSP permits inline scripts")
    for name, content in {
        "index.html": html,
        "editor.js": editor,
        "bootstrap.js": bootstrap,
        "coi-serviceworker.js": service_worker,
    }.items():
        if re.search(r"https?://", content):
            raise RuntimeError(f"{name} contains a production network dependency")
    if "require-corp" not in service_worker or "CACHE_NAME" not in service_worker:
        raise RuntimeError("Service worker lacks isolation headers or offline caching")
    static_block = re.search(
        r"const STATIC_ASSETS = \[(.*?)\];",
        service_worker,
        flags=re.DOTALL,
    )
    if not static_block:
        raise RuntimeError("Service worker static asset manifest is missing")
    for asset in re.findall(r"'(\./[^']*)'", static_block.group(1)):
        if asset == "./":
            continue
        asset_path = ROOT / asset.removeprefix("./")
        if not asset_path.is_file():
            raise RuntimeError(f"Service worker asset does not exist: {asset}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        SBOM_PATH.write_text(
            json.dumps(build_inventory(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    verify_inventory()
    print("Browser runtime inventory verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
