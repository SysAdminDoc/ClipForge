"""Brand asset paths shared by source and packaged ClipForge builds."""

from __future__ import annotations

import sys
from pathlib import Path

ICON_FILENAME = "clipforge-mark-256.png"


def application_icon_path() -> Path:
    """Return the bundled application icon path."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "clipforge" / "assets" / ICON_FILENAME
    return Path(__file__).resolve().parent / "assets" / ICON_FILENAME
