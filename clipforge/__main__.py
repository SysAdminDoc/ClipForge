"""Entry point for `python -m clipforge`."""

from multiprocessing import freeze_support
import sys


def main():
    """Launch ClipForge after validating its installed GUI dependency."""
    freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--release-smoke":
        from .release_smoke import main as run_release_smoke

        raise SystemExit(run_release_smoke(sys.argv[2:]))
    try:
        from clipforge.app import main as run_app
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PyQt6"):
            raise SystemExit(
                "ClipForge requires PyQt6. Install dependencies with "
                "'python -m pip install --require-hashes -r requirements.lock'."
            ) from exc
        raise
    run_app()


if __name__ == "__main__":
    main()
