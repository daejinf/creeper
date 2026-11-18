"""Foundation tables (app_meta) and AI job state (ai_jobs). Product tables arrive in later stages."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from .db import metadata

app_meta = Table(
    "app_meta",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", String(256), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# One row per AI operation. State lives here, never in process memory, so a refresh or restart can recover it.
ai_jobs = Table(
    "ai_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("operation_id", String(128), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("input", JSONB, nullable=False),
    Column("result", JSONB),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("operation_id"),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("revision >= 1", name="revision"),
    Index("ix_ai_jobs_state", "state"),
)

users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(16), nullable=False),
    Column("display_name", String(30), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("notify_opt_in", Boolean, nullable=False, server_default=false()),
    Column("terms_accepted_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("username"),
    CheckConstraint("username ~ '^[a-z0-9]{4,16}$'", name="username"),
)

# Server-side sessions. Only the sha256 of the cookie token is stored.
auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("keep", Boolean, nullable=False, server_default=false()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_auth_sessions_user_id", "user_id"),
    Index("ix_auth_sessions_absolute_expires_at", "absolute_expires_at"),
)

# Fixed-window counters with an optional lock, shared by every process.
auth_throttle = Table(
    "auth_throttle",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
    Column("locked_until", DateTime(timezone=True)),
)
