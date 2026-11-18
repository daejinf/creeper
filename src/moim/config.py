"""Environment-based settings. Fails fast on missing/invalid values; never logs secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ENVIRONMENTS = ("development", "test", "production")


class ConfigError(RuntimeError):
    """Configuration problem. Messages must never contain secret values."""


def parse_database_url(raw: str | None, name: str = "MOIM_DATABASE_URL") -> str:
    if not raw or not raw.strip():
        raise ConfigError(f"{name} is required")
    try:
        url = make_url(raw.strip())
    except ArgumentError:
        raise ConfigError(f"{name} is not a valid database URL") from None
    if url.get_backend_name() != "postgresql":
        raise ConfigError(f"{name} must be a PostgreSQL URL")
    if url.drivername != "postgresql+psycopg":
        raise ConfigError(f"{name} must use the postgresql+psycopg driver")
    if not url.host or not url.database:
        raise ConfigError(f"{name} must include host and database name")
    return url.render_as_string(hide_password=False)


def redact_url(raw: str) -> str:
    return make_url(raw).render_as_string(hide_password=True)


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str = field(repr=False)

    @property
    def safe_database_url(self) -> str:
        return redact_url(self.database_url)

    def __repr__(self) -> str:  # keep secrets out of logs/tracebacks
        return f"Settings(env={self.env!r}, database_url={self.safe_database_url!r})"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    env = (environ.get("MOIM_ENV") or "").strip()
    if env not in ENVIRONMENTS:
        raise ConfigError(f"MOIM_ENV must be one of {', '.join(ENVIRONMENTS)}")
    url = parse_database_url(environ.get("MOIM_DATABASE_URL"))
    database = make_url(url).database or ""
    if env == "test" and not database.endswith("_test"):
        raise ConfigError("test environment requires a database name ending with _test")
    if env != "test" and database.endswith("_test"):
        raise ConfigError("a *_test database may only be used with MOIM_ENV=test")
    return Settings(env=env, database_url=url)
