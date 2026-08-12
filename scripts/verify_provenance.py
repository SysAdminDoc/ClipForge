#!/usr/bin/env python3
"""Emit and validate the complete ClipForge runtime provenance manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clipforge.provenance import build_provenance, validate_provenance  # noqa: E402


def write_manifest(output_path, manifest):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staged = output_path.with_name(f".{output_path.name}.clipforge-{uuid.uuid4().hex}")
    try:
        staged.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(staged, output_path)
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-lock",
        help="Require every applicable package in this lock to be installed at its pinned version",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest = build_provenance(strict_lock=args.strict_lock)
    validate_provenance(manifest)
    if args.output:
        write_manifest(args.output, manifest)
    dependency_count = sum(
        len(group["dependencies"])
        for group in manifest["python"]["groups"]
    )
    print(
        "ClipForge provenance verified: "
        f"{dependency_count} locked Python entries, "
        f"{len(manifest['browser']['artifacts'])} browser artifacts, "
        f"{len(manifest['ai_tools']['tools'])} managed AI tool manifests"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

