"""Read secrets outside Streamlit (scripts, evals): environment first, then .streamlit/secrets.toml."""

import os
import tomllib

from .config import ROOT

SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"


def get_secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if SECRETS_FILE.exists():
        with SECRETS_FILE.open("rb") as f:
            value = tomllib.load(f).get(name)
        return str(value) if value else None
    return None
