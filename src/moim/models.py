"""Foundation tables, AI job state, auth tables and the stage 2 group/ProjectBrief tables."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
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

# ---- stage 2: groups ------------------------------------------------------------------------------------------------

# FORMING = invite room, ACTIVE = ProjectBrief submitted and cohort fixed, CANCELLED = closed room.
groups = Table(
    "groups",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("status", String(16), nullable=False),
    Column("cohort_revision", Integer, nullable=False, server_default="0"),
    Column("brief_revision", Integer, nullable=False, server_default="0"),  # current ProjectBrief revision (0 = none yet)
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("activated_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
    CheckConstraint("status in ('FORMING','CANCELLED','ACTIVE')", name="status"),
    CheckConstraint(
        "(status = 'ACTIVE' and cohort_revision >= 1 and brief_revision >= 1 and activated_at is not null)"
        " or (status <> 'ACTIVE' and cohort_revision = 0 and brief_revision = 0)",
        name="active_consistency",
    ),
    Index("ix_groups_owner_id", "owner_id"),
    # One live invite room per creator: repeated create requests reuse it, concurrent ones cannot make a second.
    Index("uq_groups_owner_forming", "owner_id", unique=True, postgresql_where=text("status = 'FORMING'")),
)

group_members = Table(
    "group_members",
    metadata,
    Column("group_id", String(36), ForeignKey("groups.id"), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("role", String(8), nullable=False),
    Column("joined_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("role in ('LEADER','MEMBER')", name="role"),
    Index("ix_group_members_user_id", "user_id"),
    Index("uq_group_members_leader", "group_id", unique=True, postgresql_where=text("role = 'LEADER'")),
)

# Codes are never reused (unique over all rows), so a discarded code can never open a different room.
invite_codes = Table(
    "invite_codes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), ForeignKey("groups.id"), nullable=False),
    Column("code", String(6), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column("revoke_reason", String(16)),
    UniqueConstraint("code"),
    CheckConstraint("code ~ '^[0-9]{6}$'", name="code"),
    CheckConstraint("revoke_reason is null or revoke_reason in ('REISSUED','ACTIVATED','CANCELLED')", name="revoke_reason"),
    CheckConstraint("(revoked_at is null) = (revoke_reason is null)", name="revoked_pair"),
    Index("uq_invite_codes_live", "group_id", unique=True, postgresql_where=text("revoked_at is null")),
)

project_briefs = Table(
    "project_briefs",
    metadata,
    Column("group_id", String(36), ForeignKey("groups.id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("name", String(30), nullable=False),
    Column("content", Text, nullable=False, server_default=""),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    Column("deliverable_types", JSONB, nullable=False),
    Column("milestones", JSONB, nullable=False),
    Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("revision >= 1", name="revision"),
    CheckConstraint("char_length(name) between 1 and 30", name="name"),
    CheckConstraint("char_length(content) <= 1000", name="content"),
    CheckConstraint("end_date >= start_date", name="period"),
)

# The members fixed at activation. Later stages read this, never the live member list.
activation_cohorts = Table(
    "activation_cohorts",
    metadata,
    Column("group_id", String(36), ForeignKey("groups.id"), primary_key=True),
    Column("cohort_revision", Integer, primary_key=True),
    Column("activated_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("cohort_revision >= 1", name="revision"),
)

cohort_members = Table(
    "cohort_members",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("cohort_revision", Integer, primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    ForeignKeyConstraint(
        ["group_id", "cohort_revision"], ["activation_cohorts.group_id", "activation_cohorts.cohort_revision"]
    ),
)

# The one-sentence team goal shown in the group header, derived from one ProjectBrief revision. Never an input.
goal_displays = Table(
    "goal_displays",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("brief_revision", Integer, primary_key=True),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("text", String(200)),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["group_id", "brief_revision"], ["project_briefs.group_id", "project_briefs.revision"]),
    CheckConstraint("state in ('EMPTY','PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("(state = 'SUCCEEDED') = (text is not null)", name="text_iff_succeeded"),
)
