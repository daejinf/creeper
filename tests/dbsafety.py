"""Guard for destructive test operations (DROP SCHEMA public CASCADE).

The reset runs only when ALL of these hold, checked before any connection is opened:
  1. the host is this machine (127.0.0.1, localhost or ::1)
  2. the port is the approved test port 54329
  3. the database name is exactly `moim_test`
  4. MOIM_ENV is `test`
If anything differs, UnsafeTestTarget is raised and nothing is dropped.
"""
from __future__ import annotations

import os

import psycopg
from sqlalchemy.engine import make_url

APPROVED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
APPROVED_PORT = 54329
EXPECTED_DATABASE = "moim_test"


class UnsafeTestTarget(AssertionError):
    """A destructive test operation was aimed at something that is not the disposable test database."""


def assert_safe_to_destroy(url: str, environ=None) -> None:
    environ = os.environ if environ is None else environ
    parsed = make_url(url)
    if (parsed.host or "") not in APPROVED_HOSTS:
        raise UnsafeTestTarget("destructive test refused: the host is not this machine")
    if parsed.port != APPROVED_PORT:
        raise UnsafeTestTarget(f"destructive test refused: the port is not {APPROVED_PORT}")
    if parsed.database != EXPECTED_DATABASE:
        raise UnsafeTestTarget(f"destructive test refused: the database is not exactly {EXPECTED_DATABASE}")
    if (environ.get("MOIM_ENV") or "") != "test":
        raise UnsafeTestTarget("destructive test refused: MOIM_ENV is not test")


def reset_test_schema(url: str, environ=None) -> None:
    """DROP SCHEMA public CASCADE and recreate it, only after every condition above passed."""
    assert_safe_to_destroy(url, environ)
    parsed = make_url(url)
    with psycopg.connect(host=parsed.host, port=parsed.port, user=parsed.username, password=parsed.password,
                         dbname=parsed.database, autocommit=True, connect_timeout=5) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
