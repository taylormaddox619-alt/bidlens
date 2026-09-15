"""Read secrets outside Streamlit (scripts, evals): environment first, then .streamlit/secrets.toml."""

import os

import toml

from .config import ROOT

SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"


def get_secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if SECRETS_FILE.exists():
        value = toml.load(SECRETS_FILE).get(name)
        return str(value) if value else None
    return None
