"""reload_guard: pages must pick up redeployed bidlens code (the live AttributeError after a deploy)."""

import importlib
import sys

import pytest

import reload_guard


@pytest.fixture
def restore_bidlens_modules():
    saved = {n: m for n, m in sys.modules.items() if n == "bidlens" or n.startswith("bidlens.")}
    yield
    for name in [n for n in sys.modules if n == "bidlens" or n.startswith("bidlens.")]:
        del sys.modules[name]
    sys.modules.update(saved)


def test_unchanged_code_is_not_reloaded(restore_bidlens_modules):
    import bidlens.db
    assert reload_guard.ensure_fresh() is False
    assert sys.modules["bidlens.db"] is bidlens.db


def test_redeployed_code_replaces_stale_module(restore_bidlens_modules, tmp_path, monkeypatch):
    import bidlens
    import bidlens.db
    stale_db = bidlens.db
    # Simulate the live failure: the cached module predates a function the new page calls...
    monkeypatch.delattr(stale_db, "public_generations_today")
    # ...and the files on disk have changed since the package was imported.
    monkeypatch.setattr(bidlens, "_source_signature", ("files changed on disk",))

    assert reload_guard.ensure_fresh() is True
    fresh_db = importlib.import_module("bidlens.db")
    assert fresh_db is not stale_db
    assert callable(fresh_db.public_generations_today)


def test_package_without_fingerprint_is_treated_as_stale(restore_bidlens_modules, monkeypatch):
    import bidlens
    monkeypatch.delattr(bidlens, "source_signature")
    assert reload_guard.ensure_fresh() is True
    assert "bidlens" not in sys.modules


def test_reloaded_db_module_still_reads_existing_database(restore_bidlens_modules, tmp_path, monkeypatch):
    monkeypatch.setenv("BIDLENS_DB", str(tmp_path / "shared.duckdb"))
    for name in [n for n in sys.modules if n == "bidlens" or n.startswith("bidlens.")]:
        del sys.modules[name]
    old_db = importlib.import_module("bidlens.db")
    event_id = old_db.create_event({"event_name": "before redeploy", "item": "x", "quantity": 1, "currency": "USD",
                                    "required_lead_time_weeks": 1.0, "standard_payment_days": 60,
                                    "destination": "y", "evaluation_date": "2026-09-15"}, "tester")
    importlib.import_module("bidlens")._source_signature = ("changed",)
    assert reload_guard.ensure_fresh() is True
    new_db = importlib.import_module("bidlens.db")
    assert new_db is not old_db
    assert new_db.get_event(event_id)["name"] == "before redeploy"  # same process opens the same file again
    assert new_db.public_generations_today() == 0                      # new tables created on reconnect
