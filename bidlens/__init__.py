"""BidLens - AI-assisted supplier bid analysis with human-in-the-loop review."""

from pathlib import Path

__version__ = "0.1.0"

_PACKAGE_DIR = Path(__file__).resolve().parent


def source_signature() -> tuple:
    """Fingerprint of the package's Python files on disk (used by reload_guard.py)."""
    return tuple(sorted((str(p.relative_to(_PACKAGE_DIR)), p.stat().st_mtime_ns, p.stat().st_size)
                        for p in _PACKAGE_DIR.rglob("*.py")))


# Fingerprint at import time; if the files change later (e.g. a redeploy), it no longer matches.
_source_signature = source_signature()
