"""SQLAlchemy engine/session. Every connection runs in UTC."""
from __future__ import annotations

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
