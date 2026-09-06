#!/usr/bin/env python3
"""Synchronize and validate every ClipForge version surface."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "clipforge" / "version.py"
README_FILE = ROOT / "README.md"
WEB_FILE = ROOT / "index.html"
WINDOWS_VERSION_FILE = ROOT / "packaging" / "windows-version.txt"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_version() -> str:
    match = re.search(
        r'^APP_VERSION = "(\d+\.\d+\.\d+)"$',
        VERSION_FILE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"Could not read APP_VERSION from {VERSION_FILE}")
    return match.group(1)


def windows_version_text(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'SysAdminDoc'),
          StringStruct('FileDescription', 'ClipForge Video Editor'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'ClipForge'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', 'ClipForge.exe'),
          StringStruct('ProductName', 'ClipForge'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def expected_surfaces(version: str) -> dict[Path, str]:
    readme = README_FILE.read_text(encoding="utf-8")
    readme = re.sub(
        r"(?m)^# ClipForge v\d+\.\d+\.\d+$",
        f"# ClipForge v{version}",
        readme,
        count=1,
    )
    readme = re.sub(
        r"Version-\d+\.\d+\.\d+-orange",
        f"Version-{version}-orange",
        readme,
        count=1,
    )
    readme = re.sub(
        r"badge/version-\d+\.\d+\.\d+-6366f1",
        f"badge/version-{version}-6366f1",
        readme,
        count=1,
    )
    web = re.sub(
        r">v\d+\.\d+\.\d+</div>",
        f">v{version}</div>",
        WEB_FILE.read_text(encoding="utf-8"),
        count=1,
    )
    web = re.sub(
        r'editor\.js\?v=\d+\.\d+\.\d+',
        f"editor.js?v={version}",
        web,
        count=1,
    )
    return {
        README_FILE: readme,
        WEB_FILE: web,
        WINDOWS_VERSION_FILE: windows_version_text(version),
    }


def sync(version: str) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError("Version must use MAJOR.MINOR.PATCH")
    VERSION_FILE.write_text(
        '"""Single source of truth for ClipForge\'s application version."""\n\n'
        f'APP_VERSION = "{version}"\n',
        encoding="utf-8",
        newline="\n",
    )
    WINDOWS_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    for path, content in expected_surfaces(version).items():
        path.write_text(content, encoding="utf-8", newline="\n")


def check(version: str) -> list[str]:
    failures = []
    for path, expected in expected_surfaces(version).items():
        if not path.exists():
            failures.append(f"missing: {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != expected:
            failures.append(f"out of sync: {path.relative_to(ROOT)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", dest="new_version")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.new_version:
        sync(args.new_version)
    version = read_version()
    failures = check(version)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    if args.check:
        print(f"Version surfaces are synchronized at {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
