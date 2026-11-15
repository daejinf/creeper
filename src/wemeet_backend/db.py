"""Lazy SQLAlchemy engine, serverless-friendly (NullPool by default).

No module-level connection; nothing touches the DB until get_engine() is called.
"""

from collections.abc import Iterator
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from .config import Settings, load_settings

_engine: Engine | None = None
_lock = Lock()


def build_engine(settings: Settings) -> Engine:
    connect_args: dict = {}
    if settings.db_prepare_off:
        connect_args["prepare_threshold"] = None  # psycopg3: required by transaction-mode poolers
    if settings.db_pool_mode == "null":
        return create_engine(settings.database_url, connect_args=connect_args, poolclass=NullPool)
    return create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_recycle=300,
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = build_engine(load_settings())
    return _engine


def reset_engine() -> None:
    """Dispose and forget the cached engine (tests / config change)."""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def get_connection() -> Iterator[Connection]:
    """Per-request transaction: commits on success, rolls back on error (FastAPI dependency)."""
    with get_engine().begin() as conn:
        yield conn
