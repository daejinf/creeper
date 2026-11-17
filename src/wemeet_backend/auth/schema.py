"""Core table definitions for auth (own MetaData; schema ``wemeet_auth``).

Must stay in sync with migrations/versions/0005_auth.py (which uses frozen DDL).
This module never creates tables; Alembic does, run manually by the operator.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

SCHEMA = "wemeet_auth"

metadata = sa.MetaData(schema=SCHEMA)

users = sa.Table(
    "users",
    metadata,
    sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("username", sa.Text, nullable=False),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("last_login_at", sa.DateTime(timezone=True)),
    sa.Column("nickname", sa.Text, nullable=False),  # display name; duplicates allowed (no unique)
    sa.UniqueConstraint("username", name="uq_auth_users_username"),
    sa.CheckConstraint("username ~ '^[a-z0-9_]{3,20}$'", name="ck_auth_users_username"),
    sa.CheckConstraint(
        "char_length(nickname) BETWEEN 2 AND 10 AND nickname = btrim(nickname)", name="ck_auth_users_nickname"
    ),
)

auth_sessions = sa.Table(
    "auth_sessions",
    metadata,
    sa.Column("session_hash", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        UUID(as_uuid=True),
        sa.ForeignKey(f"{SCHEMA}.users.user_id", ondelete="CASCADE", name="fk_auth_sessions_user"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("session_hash ~ '^[0-9a-f]{64}$'", name="ck_auth_sessions_hash"),
    sa.CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry"),
    sa.Index("ix_auth_sessions_user_id", "user_id"),
    sa.Index("ix_auth_sessions_expires_at", "expires_at"),
)
