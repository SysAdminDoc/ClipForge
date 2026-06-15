"""Entry point for `python -m clipforge`."""
import sys
import os
import subprocess

def _bootstrap():
    deps = {
        "PyQt6": "PyQt6",
        "PyQt6.QtMultimedia": "PyQt6-Multimedia",
        "PyQt6.QtMultimediaWidgets": "PyQt6-Multimedia",
    }
    missing = []
    for mod, pkg in deps.items():
        try:
            __import__(mod)
        except ImportError:
            if pkg not in missing:
                missing.append(pkg)
    if missing:
        print(f"[ClipForge] Installing: {', '.join(missing)}")
        pip_args = [sys.executable, "-m", "pip", "install", *missing]
        in_venv = (hasattr(sys, "real_prefix")
                   or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix))
        if not in_venv:
            pip_args.append("--user")
        subprocess.check_call(
            pip_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

_bootstrap()

from clipforge.app import main
main()
