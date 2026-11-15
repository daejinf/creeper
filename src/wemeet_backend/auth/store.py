"""DB access for auth. Every function takes a Connection; the caller owns the transaction."""

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from .schema import auth_sessions, users


def create_user(conn: Connection, username: str, password_hash: str) -> uuid.UUID | None:
    """Insert a user; return its id, or None if the username is already taken."""
    user_id = uuid.uuid4()
    stmt = (
        pg_insert(users)
        .values(user_id=user_id, username=username, password_hash=password_hash)
        .on_conflict_do_nothing(index_elements=[users.c.username])
        .returning(users.c.user_id)
    )
    return conn.execute(stmt).scalar()


def get_user_by_username(conn: Connection, username: str) -> Any | None:
    return conn.execute(
        sa.select(users.c.user_id, users.c.username, users.c.password_hash).where(users.c.username == username)
    ).first()


def touch_last_login(conn: Connection, user_id: uuid.UUID) -> None:
    conn.execute(sa.update(users).where(users.c.user_id == user_id).values(last_login_at=sa.func.now()))


def create_session(
    conn: Connection,
    session_hash: str,
    user_id: uuid.UUID,
    days: int,
    *,
    created_at: dt.datetime | None = None,
    expires_at: dt.datetime | None = None,
) -> None:
    """Store a session. Defaults use the DB clock: now() and now() + days."""
    values: dict[str, Any] = {
        "session_hash": session_hash,
        "user_id": user_id,
        "expires_at": expires_at if expires_at is not None else sa.func.now() + dt.timedelta(days=days),
    }
    if created_at is not None:
        values["created_at"] = created_at
    conn.execute(sa.insert(auth_sessions).values(**values))


def get_user_by_session(conn: Connection, session_hash: str) -> Any | None:
    """Return (user_id, username) for a live session; expiry is checked here, at read time."""
    return conn.execute(
        sa.select(users.c.user_id, users.c.username)
        .select_from(auth_sessions.join(users, users.c.user_id == auth_sessions.c.user_id))
        .where(auth_sessions.c.session_hash == session_hash, auth_sessions.c.expires_at > sa.func.now())
    ).first()


def delete_session(conn: Connection, session_hash: str) -> int:
    return conn.execute(sa.delete(auth_sessions).where(auth_sessions.c.session_hash == session_hash)).rowcount


def purge_expired(conn: Connection) -> int:
    """Delete expired sessions. Manual use only; never called automatically."""
    return conn.execute(sa.delete(auth_sessions).where(auth_sessions.c.expires_at <= sa.func.now())).rowcount
