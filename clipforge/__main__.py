"""Entry point for `python -m clipforge`."""

from multiprocessing import freeze_support


def main():
    """Launch ClipForge after validating its installed GUI dependency."""
    freeze_support()
    try:
        from clipforge.app import main as run_app
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PyQt6"):
            raise SystemExit(
                "ClipForge requires PyQt6. Install dependencies with "
                "'python -m pip install -r requirements.lock'."
            ) from exc
        raise
    run_app()


if __name__ == "__main__":
    main()
