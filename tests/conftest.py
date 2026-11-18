"""Test isolation: DB tests only ever touch MOIM_TEST_DATABASE_URL (database name must end with _test)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))

import dbsafety  # noqa: E402


def _test_url() -> str | None:
    url = os.environ.get("MOIM_TEST_DATABASE_URL")
    if url:
        return url
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("MOIM_TEST_DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return None


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = _test_url()
    if not url:
        pytest.fail("MOIM_TEST_DATABASE_URL is required for database tests (see .env.example)")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("MOIM_TEST_DATABASE_URL must point to a database whose name ends with _test")
    return url


@pytest.fixture()
def test_environ(test_database_url, monkeypatch):
    """Process env for the test database; the dev URL is removed from the environment."""
    monkeypatch.setenv("MOIM_ENV", "test")
    monkeypatch.setenv("MOIM_DATABASE_URL", test_database_url)
    return test_database_url


@pytest.fixture()
def reset_schema(test_environ):
    """Empty the test database. Guarded: localhost, port 54329, exactly moim_test and MOIM_ENV=test, or nothing is dropped."""
    return lambda: dbsafety.reset_test_schema(test_environ)
