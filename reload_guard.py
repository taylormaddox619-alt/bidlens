"""Make Streamlit pick up redeployed `bidlens` code without a manual reboot.

Streamlit re-executes page scripts on every run, but modules they import stay cached in
the server process. After a deploy, a fresh page could call a function that the cached,
older `bidlens` module doesn't have (AttributeError). Pages call `ensure_fresh()` before
importing `bidlens`: if the package's files changed since it was imported, the cached
modules are dropped so the imports below load the current code.

This module lives outside the package so importing it never imports `bidlens` itself.
"""

import sys


def ensure_fresh() -> bool:
    """Return True if stale bidlens modules were dropped."""
    package = sys.modules.get("bidlens")
    if package is None:
        return False
    signature = getattr(package, "source_signature", None)
    if callable(signature) and signature() == getattr(package, "_source_signature", None):
        return False
    # Package predates the fingerprint, or files changed on disk: drop every cached bidlens module.
    for name in [n for n in sys.modules if n == "bidlens" or n.startswith("bidlens.")]:
        del sys.modules[name]
    return True
