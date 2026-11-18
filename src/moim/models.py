"""Foundation tables (app_meta) and AI job state (ai_jobs). Product tables arrive in later stages."""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Table, UniqueConstraint, func
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
