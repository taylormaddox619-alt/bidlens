"""Secrets for scripts and evals: environment first, then .streamlit/secrets.toml (standard-library tomllib)."""

import pytest

from bidlens import credentials


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    path = tmp_path / "secrets.toml"
    monkeypatch.setattr(credentials, "SECRETS_FILE", path)
    monkeypatch.delenv("BIDLENS_TEST_SECRET", raising=False)
    return path


def test_environment_wins_over_the_file(secrets_file, monkeypatch):
    secrets_file.write_text('BIDLENS_TEST_SECRET = "from-file"\n', encoding="utf-8")
    monkeypatch.setenv("BIDLENS_TEST_SECRET", "from-env")
    assert credentials.get_secret("BIDLENS_TEST_SECRET") == "from-env"


def test_file_is_read_when_the_variable_is_unset(secrets_file):
    secrets_file.write_text('# comment\nBIDLENS_TEST_SECRET = "from-file"\nOTHER = 5\n', encoding="utf-8")
    assert credentials.get_secret("BIDLENS_TEST_SECRET") == "from-file"
    assert credentials.get_secret("OTHER") == "5"
    assert credentials.get_secret("ABSENT") is None


def test_missing_file_returns_none(secrets_file):
    assert not secrets_file.exists()
    assert credentials.get_secret("BIDLENS_TEST_SECRET") is None


def test_no_undeclared_toml_dependency():
    import sys
    assert "tomllib" in sys.modules and not hasattr(credentials, "toml")
