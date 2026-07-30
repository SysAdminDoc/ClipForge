#!/usr/bin/env python3
"""Generate universal, hash-locked ClipForge dependency environments."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    ("requirements.in", "requirements.lock"),
    ("requirements-dev.in", "requirements-dev.lock"),
    ("requirements-mpv.in", "requirements-mpv.lock"),
)


def main() -> int:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            "uv is required to regenerate locks; install it from https://docs.astral.sh/uv/"
        )
    for source, destination in LOCKS:
        subprocess.run(
            [
                uv,
                "pip",
                "compile",
                "--universal",
                "--generate-hashes",
                "--upgrade",
                "--python-version",
                "3.11",
                "--output-file",
                destination,
                source,
            ],
            cwd=ROOT,
            check=True,
        )
    print("Runtime, development, and optional mpv locks regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
