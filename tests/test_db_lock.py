"""The app must start even when another process still holds the demo database's file lock.

Seen on Streamlit Cloud: a deploy restarted the server, the previous server process had not exited and still
held DuckDB's lock, and every page failed with "Could not set lock on file ... Conflicting lock is held".
"""

import subprocess
import sys

import duckdb
import pytest

from bidlens import db

HOLD_LOCK = "import duckdb, sys; c = duckdb.connect(sys.argv[1]); print('locked', flush=True); sys.stdin.read()"


@pytest.fixture
def locked_db(tmp_path, monkeypatch):
    """A database file that another live process has open, as a lingering server process would."""
    path = tmp_path / "bidlens.duckdb"
    holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "locked"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    yield path
    if db._conn is not None:
        db._conn.close()
    monkeypatch.setattr(db, "_conn", None)
    holder.stdin.close()
    holder.wait(timeout=30)


def test_default_demo_database_falls_back_to_its_own_file_when_locked(locked_db, monkeypatch, rfq):
    monkeypatch.delenv("BIDLENS_DB", raising=False)
    assert db.list_events() == []  # raised IOException before the fallback existed
    event_id = db.create_event(rfq.model_dump(), "tester")
    assert [e["id"] for e in db.list_events()] == [event_id]
    fallbacks = [p for p in locked_db.parent.glob("bidlens-*.duckdb")]
    assert len(fallbacks) == 1 and fallbacks[0] != locked_db


def test_an_explicitly_configured_database_never_falls_back(locked_db, monkeypatch):
    """BIDLENS_DB names a database somebody chose. Opening a different, empty one instead would look like data
    loss, so the lock error must surface."""
    monkeypatch.setenv("BIDLENS_DB", str(locked_db))
    with pytest.raises(duckdb.IOException):
        db.list_events()
    assert not list(locked_db.parent.glob("bidlens-*.duckdb"))
