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

# ---- stage 3: personal interview and role preference ----------------------------------------------------------------

# One role-candidate set per ProjectBrief revision (candidate revision == source ProjectBrief revision). Created
# PENDING together with the brief revision; the candidates are written once, on SUCCEEDED, and never changed.
role_candidate_sets = Table(
    "role_candidate_sets",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("source_project_brief_revision", Integer, primary_key=True),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("last_action_id", String(64)),  # the explicit retry that started the current attempt (idempotency)
    Column("candidates", JSONB),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "source_project_brief_revision"], ["project_briefs.group_id", "project_briefs.revision"]
    ),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("(state = 'SUCCEEDED') = (candidates is not null)", name="candidates_iff_succeeded"),
)

# One row per user message and the AI reply to it (one Timely call per message). action_id makes a resend idempotent.
interview_turns = Table(
    "interview_turns",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("action_id", String(64), nullable=False),
    Column("user_message", Text, nullable=False),
    Column("ai_reply", Text),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("answer_question_id", String(64)),  # set when the message is a button answer (D-B2)
    Column("answer_option_id", String(64)),
    Column("state_revision", Integer),  # the member_states revision this turn produced
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["group_id"], ["groups.id"]),
    ForeignKeyConstraint(["user_id"], ["users.id"]),
    UniqueConstraint("group_id", "user_id", "action_id"),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("seq between 1 and 10", name="seq"),
    CheckConstraint("(answer_question_id is null) = (answer_option_id is null)", name="answer_pair"),
    CheckConstraint("char_length(user_message) between 1 and 300", name="message_length"),
    CheckConstraint("(state = 'SUCCEEDED') = (ai_reply is not null)", name="reply_iff_succeeded"),
    # One unresolved turn per member at a time: a FAILED turn must be retried before the next message.
    Index(
        "uq_interview_turns_open", "group_id", "user_id", unique=True,
        postgresql_where=text("state in ('PENDING','RUNNING','FAILED')"),
    ),
    comment="DEPRECATED since D-B3 (2026-11-19): kept for the stage 4 post-assignment interview, not written by stage 3",
)

# The structured private MemberState, one immutable row per revision. Only its owner ever reads it.
member_states = Table(
    "member_states",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("source_turn_seq", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_turn_seq"],
        ["interview_turns.group_id", "interview_turns.user_id", "interview_turns.seq"],
    ),
    CheckConstraint("revision >= 1", name="revision"),
    comment="DEPRECATED since D-B3 (2026-11-19): kept for the stage 4 post-assignment interview, not written by stage 3",
)

# Immutable preference submissions (1st, 2nd, 3rd choice of one candidate revision). A new revision exists only after
# the previous one went stale because the ProjectBrief (and so the candidates) changed.
preference_submissions = Table(
    "preference_submissions",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("source_candidate_revision", Integer, nullable=False),
    Column("action_id", String(64), nullable=False),
    Column("rank1", String(16), nullable=False),
    Column("rank2", String(16), nullable=False),
    Column("rank3", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["user_id"], ["users.id"]),
    ForeignKeyConstraint(
        ["group_id", "source_candidate_revision"],
        ["role_candidate_sets.group_id", "role_candidate_sets.source_project_brief_revision"],
    ),
    UniqueConstraint("group_id", "user_id", "action_id"),
    CheckConstraint("revision >= 1", name="revision"),
    CheckConstraint("rank1 <> rank2 and rank1 <> rank3 and rank2 <> rank3", name="distinct_ranks"),
)

# Per-member stored status: INTERVIEW -> WAITING_ASSIGNMENT -> ROLE_TODOS. Back to INTERVIEW when the candidates went
# stale. ROLE_TODOS keeps the submission the assignment was made from and names the assignment revision the member sees.
member_progress = Table(
    "member_progress",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("status", String(20), nullable=False),
    Column("submission_revision", Integer),
    Column("assignment_revision", Integer),
    Column("interview_closed_at", DateTime(timezone=True), comment="DEPRECATED since D-B3 (2026-11-19): kept for the stage 4 post-assignment interview, not written by stage 3"),  # D-B2 value kept; stage 3 ignores it
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["user_id"], ["users.id"]),
    ForeignKeyConstraint(
        ["group_id", "user_id", "submission_revision"],
        ["preference_submissions.group_id", "preference_submissions.user_id", "preference_submissions.revision"],
    ),
    ForeignKeyConstraint(["group_id", "assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"]),
    CheckConstraint("status in ('INTERVIEW','WAITING_ASSIGNMENT','ROLE_TODOS')", name="status"),
    CheckConstraint("(status = 'INTERVIEW') = (submission_revision is null)", name="submission_unless_interview"),
    CheckConstraint("(status = 'ROLE_TODOS') = (assignment_revision is not null)", name="assignment_iff_role_todos"),
)

# ---- stage 4 (session A): role assignment, team coverage, post-assignment interview -------------------------------

# One immutable assignment revision per server assignment run. Exactly one CURRENT revision per group; a reassignment
# (stage 4 session C) or a ProjectBrief/cohort change marks it STALE and never edits it. `draw_audit` records every
# server random draw (D-B1 B4) and is never shown to anyone.
role_assignments = Table(
    "role_assignments",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("source_cohort_revision", Integer, nullable=False),
    Column("source_candidate_revision", Integer, nullable=False),
    Column("operation", String(16), nullable=False),
    Column("state", String(8), nullable=False),
    Column("stale_reason", String(32)),
    Column("stale_at", DateTime(timezone=True)),
    Column("draw_audit", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["group_id"], ["groups.id"]),
    ForeignKeyConstraint(
        ["group_id", "source_candidate_revision"],
        ["role_candidate_sets.group_id", "role_candidate_sets.source_project_brief_revision"],
    ),
    CheckConstraint("revision >= 1", name="revision"),
    CheckConstraint("operation in ('INITIAL','REASSIGN')", name="operation"),
    CheckConstraint("state in ('CURRENT','STALE')", name="state"),
    CheckConstraint("(state = 'STALE') = (stale_at is not null)", name="stale_pair"),
    Index("uq_role_assignments_current", "group_id", unique=True, postgresql_where=text("state = 'CURRENT'")),
)

# The members of one assignment revision: the submission each one was assigned from, the role, the rank it matched
# (null = server random from the remaining roles) and an anonymous slot id ("s1"...) used in every AI input.
role_assignment_members = Table(
    "role_assignment_members",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("assignment_revision", Integer, primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("submission_revision", Integer, nullable=False),
    Column("slot_id", String(8), nullable=False),
    Column("role_id", String(16), nullable=False),
    Column("role_name", String(30), nullable=False),
    Column("role_description", String(120), nullable=False),
    Column("assigned_rank", Integer),
    ForeignKeyConstraint(["group_id", "assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"]),
    ForeignKeyConstraint(
        ["group_id", "user_id", "submission_revision"],
        ["preference_submissions.group_id", "preference_submissions.user_id", "preference_submissions.revision"],
    ),
    UniqueConstraint("group_id", "assignment_revision", "slot_id", name="uq_role_assignment_members_slot"),
    CheckConstraint("assigned_rank is null or assigned_rank between 1 and 3", name="rank"),
)

# The internal team coverage of one assignment revision: which slot owns which deliverable part of the ProjectBrief.
# One Timely call, written once on SUCCEEDED; a failure is FAILED with an explicit retry. Never shown as a plan.
team_coverages = Table(
    "team_coverages",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("last_action_id", String(64)),
    Column("parts", JSONB),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("stale_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["group_id", "source_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"]),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("(state = 'SUCCEEDED') = (parts is not null)", name="parts_iff_succeeded"),
)

# One post-assignment interview per member and assignment revision. `state` is the ROLE_TODOS sub-state:
# ASKING (AI questions) -> CONFIRMING (member checks/edits what was understood) -> DONE (input snapshot written).
role_interviews = Table(
    "role_interviews",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("state", String(12), nullable=False),
    Column("questions_asked", Integer, nullable=False, server_default="0"),
    Column("state_revision", Integer, nullable=False, server_default="0"),
    Column("stale_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "source_assignment_revision", "user_id"],
        ["role_assignment_members.group_id", "role_assignment_members.assignment_revision", "role_assignment_members.user_id"],
    ),
    CheckConstraint("state in ('ASKING','CONFIRMING','DONE')", name="state"),
    CheckConstraint("questions_asked between 0 and 5", name="questions_asked"),
)

# One row per interview step. OPEN = the AI's first question (no member message), ANSWER = a member answer (one Timely
# call unless none is needed), EDIT = a confirm-step action (never a Timely call). action_id makes a resend idempotent;
# `question` is the question shown after this step, with its button values (owner-only).
role_interview_turns = Table(
    "role_interview_turns",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("action_id", String(64), nullable=False),
    Column("user_message", Text),
    Column("answer_question_id", String(64)),
    Column("answer_option_id", String(64)),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("reply", Text),
    Column("question", JSONB),
    Column("state_revision", Integer),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision"],
        ["role_interviews.group_id", "role_interviews.user_id", "role_interviews.source_assignment_revision"],
    ),
    UniqueConstraint("group_id", "user_id", "action_id", name="uq_role_interview_turns_action"),
    CheckConstraint("kind in ('OPEN','ANSWER','EDIT')", name="kind"),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("seq between 0 and 40", name="seq"),
    CheckConstraint("(kind = 'OPEN') = (user_message is null)", name="message_unless_open"),
    CheckConstraint("user_message is null or char_length(user_message) between 1 and 300", name="message_length"),
    CheckConstraint("(answer_question_id is null) = (answer_option_id is null)", name="answer_pair"),
    Index(
        "uq_role_interview_turns_open", "group_id", "user_id", "source_assignment_revision", unique=True,
        postgresql_where=text("state in ('PENDING','RUNNING','FAILED')"),
    ),
)

# The private MemberState of the post-assignment interview, one immutable row per revision. Owner-only.
role_member_states = Table(
    "role_member_states",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("source_turn_seq", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision"],
        ["role_interviews.group_id", "role_interviews.user_id", "role_interviews.source_assignment_revision"],
    ),
    CheckConstraint("revision >= 1", name="revision"),
)

# The confirmed interview input for the todo draft (stage 4 session B), written once when the member confirms.
# `provider_input` holds only D-B1 B3 fields (a KNOWN value or "UNKNOWN"); the raw constraints stay in
# `private_constraints` and the date/weekday limits for the server's deadline check in `deadline_limits`.
role_interview_snapshots = Table(
    "role_interview_snapshots",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("member_state_revision", Integer, nullable=False),
    Column("provider_input", JSONB, nullable=False),
    Column("private_constraints", JSONB, nullable=False),
    Column("deadline_limits", JSONB, nullable=False),
    Column("stale_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision"],
        ["role_interviews.group_id", "role_interviews.user_id", "role_interviews.source_assignment_revision"],
    ),
)
