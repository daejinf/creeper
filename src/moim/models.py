"""Foundation tables, AI job state, auth tables and the stage 2 group/ProjectBrief tables."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
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
    Column("username", String(16)),  # NULL only on a deleted-user tombstone (stage 6, D-D1 D7)
    Column("display_name", String(30), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("notify_opt_in", Boolean, nullable=False, server_default=false()),
    Column("terms_accepted_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("deleted_at", DateTime(timezone=True)),
    UniqueConstraint("username"),
    CheckConstraint("username is null or username ~ '^[a-z0-9]{4,16}$'", name="username"),
    CheckConstraint("(username is null) = (deleted_at is not null)", name="tombstone"),
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
    Column("purged_at", DateTime(timezone=True)),  # stage 6: private content erased when the member left (D6)
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision"],
        ["role_interviews.group_id", "role_interviews.user_id", "role_interviews.source_assignment_revision"],
    ),
)

# ---- stage 4 session B: todo draft, edits, confirmation -------------------------------------------------
# One todo draft per member and assignment revision, made from the confirmed interview snapshot and the fixed team
# coverage of that revision (both FKs below are the `source_assignment_revision` link session C marks STALE).
# `draft_revision` grows with every saved change (0 = nothing generated yet). Owner-only.
todo_drafts = Table(
    "todo_drafts",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("member_state_revision", Integer, nullable=False),
    Column("draft_revision", Integer, nullable=False, server_default="0"),
    Column("next_item_no", Integer, nullable=False, server_default="1"),
    Column("mode", String(10), nullable=False, server_default="EDITING"),
    Column("confirmed_revision", Integer),
    Column("stale_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision"],
        ["role_interview_snapshots.group_id", "role_interview_snapshots.user_id",
         "role_interview_snapshots.source_assignment_revision"],
    ),
    ForeignKeyConstraint(
        ["group_id", "source_assignment_revision"],
        ["team_coverages.group_id", "team_coverages.source_assignment_revision"],
    ),
    CheckConstraint("draft_revision >= 0", name="draft_revision"),
    CheckConstraint("mode in ('EDITING','CONFIRMED')", name="mode"),
    CheckConstraint("mode = 'EDITING' or confirmed_revision is not null", name="confirmed_has_revision"),
)

_DRAFT_KEY = ["group_id", "user_id", "source_assignment_revision"]
_DRAFT_REF = ["todo_drafts.group_id", "todo_drafts.user_id", "todo_drafts.source_assignment_revision"]

# The working copy. `task_id` is given by the server when a todo is first made and never reused (a deleted todo keeps
# its row with `deleted_at`). `item_no` is the draft-local handle of the owner's browser; `task_id` never leaves the
# server. The hidden criteria (deliverable, completion criteria, evidence types, estimate) are valid only while
# `criteria_for_title` equals the current title.
todo_items = Table(
    "todo_items",
    metadata,
    Column("task_id", String(36), primary_key=True),
    Column("group_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("source_assignment_revision", Integer, nullable=False),
    Column("item_no", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("title", String(60), nullable=False),
    Column("due_date", Date),
    Column("origin", String(8), nullable=False),
    Column("part_id", String(8)),
    Column("phase", String(10)),
    Column("milestone_id", String(8)),
    Column("deliverable", Text),
    Column("completion_criteria", JSONB),
    Column("evidence_types", JSONB),
    Column("estimated_minutes", Integer),
    Column("criteria_for_title", String(60)),
    Column("deleted_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(_DRAFT_KEY, _DRAFT_REF),
    UniqueConstraint("group_id", "user_id", "source_assignment_revision", "item_no", name="uq_todo_items_item_no"),
    CheckConstraint("char_length(title) between 1 and 60", name="title"),
    CheckConstraint("origin in ('AI','USER')", name="origin"),
    CheckConstraint(
        "(deliverable is null) = (completion_criteria is null) and (deliverable is null) = (evidence_types is null)"
        " and (deliverable is null) = (criteria_for_title is null) and (deliverable is null) = (estimated_minutes is null)",
        name="criteria_together",
    ),
)

# Every AI step of a draft: DRAFT (first generation), REVISE (one natural-language change request; `user_message` is
# the member's own request) and CRITERIA (hidden criteria for changed / added todos, started by `투두 확정하기`).
# Background PENDING -> RUNNING -> SUCCEEDED | FAILED; a failure is an explicit retry, never an automatic one.
todo_ai_steps = Table(
    "todo_ai_steps",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("kind", String(10), nullable=False),
    Column("action_id", String(64), nullable=False),
    Column("user_message", Text),
    Column("state", String(12), nullable=False),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("base_draft_revision", Integer),
    Column("result_draft_revision", Integer),
    Column("reply", Text),
    Column("error_code", String(64)),
    Column("error_message", String(500)),
    Column("model", String(64)),
    Column("request_id", String(128)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(_DRAFT_KEY, _DRAFT_REF),
    UniqueConstraint("group_id", "user_id", "action_id", name="uq_todo_ai_steps_action"),
    CheckConstraint("kind in ('DRAFT','REVISE','CRITERIA')", name="kind"),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="state"),
    CheckConstraint("(kind = 'REVISE') = (user_message is not null)", name="message_iff_revise"),
    CheckConstraint("user_message is null or char_length(user_message) between 1 and 300", name="message_length"),
    Index(
        "uq_todo_ai_steps_running", "group_id", "user_id", "source_assignment_revision", unique=True,
        postgresql_where=text("state in ('PENDING','RUNNING')"),
    ),
)

# Idempotency of the plain (non-AI) actions: direct edits, confirm and unlock.
todo_actions = Table(
    "todo_actions",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("action_id", String(64), primary_key=True),
    Column("source_assignment_revision", Integer, nullable=False),
    Column("kind", String(10), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(_DRAFT_KEY, _DRAFT_REF),
)

# One immutable revision per `투두 확정하기`. A trigger allows no UPDATE except `stale_at` (session C) and no DELETE.
# The group sees the newest revision until a newer one is confirmed (D-B1 B7).
todo_confirmations = Table(
    "todo_confirmations",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("draft_revision", Integer, nullable=False),
    Column("member_state_revision", Integer, nullable=False),
    Column("action_id", String(64), nullable=False),
    Column("stale_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(_DRAFT_KEY, _DRAFT_REF),
    CheckConstraint("revision >= 1", name="revision"),
)

# The todos of one confirmation: visible fields and hidden criteria fixed together. Immutable (trigger: no UPDATE, no
# DELETE), so a stage 5 attachment bound to (task_id, confirmation revision) always finds the criteria it was made for.
todo_confirmed_items = Table(
    "todo_confirmed_items",
    metadata,
    Column("group_id", String(36), primary_key=True),
    Column("user_id", String(36), primary_key=True),
    Column("source_assignment_revision", Integer, primary_key=True),
    Column("confirmation_revision", Integer, primary_key=True),
    Column("task_id", String(36), ForeignKey("todo_items.task_id"), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("title", String(60), nullable=False),
    Column("due_date", Date, nullable=False),
    Column("part_id", String(8), nullable=False),
    Column("deliverable", Text, nullable=False),
    Column("completion_criteria", JSONB, nullable=False),
    Column("evidence_types", JSONB, nullable=False),
    Column("estimated_minutes", Integer, nullable=False),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision", "confirmation_revision"],
        ["todo_confirmations.group_id", "todo_confirmations.user_id", "todo_confirmations.source_assignment_revision",
         "todo_confirmations.revision"],
    ),
)

# ---- stage 4 session C: reassignment requests (D-B1 B4) ---------------------------------------------------------
# A leader asks to reassign one member of the CURRENT assignment revision; only that member's own consent makes the
# server write a new assignment revision (operation REASSIGN) from the same inputs (cohort, candidate revision and
# preference submissions). The request never carries a role: neither the leader nor the member picks one.
# At most one open request per group. Nothing here is shown to anyone but the leader and the target.
role_reassignments = Table(
    "role_reassignments",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), nullable=False),
    Column("source_assignment_revision", Integer, nullable=False),
    Column("target_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("requested_by", String(36), ForeignKey("users.id"), nullable=False),
    Column("request_action_id", String(64), nullable=False),
    Column("state", String(12), nullable=False),
    Column("response_action_id", String(64)),
    Column("result_assignment_revision", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("decided_at", DateTime(timezone=True)),
    ForeignKeyConstraint(["group_id"], ["groups.id"]),
    ForeignKeyConstraint(["group_id", "source_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"]),
    ForeignKeyConstraint(["group_id", "result_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"]),
    UniqueConstraint("group_id", "requested_by", "request_action_id", name="uq_role_reassignments_action"),
    CheckConstraint("state in ('REQUESTED','ACCEPTED','DECLINED','SUPERSEDED')", name="state"),
    CheckConstraint("(state = 'ACCEPTED') = (result_assignment_revision is not null)", name="result_iff_accepted"),
    CheckConstraint("(state = 'REQUESTED') = (decided_at is null)", name="decided_unless_requested"),
    Index("uq_role_reassignments_open", "group_id", unique=True, postgresql_where=text("state = 'REQUESTED'")),
)


# ---- stage 5: execution records, attachments, M2 reviews, audit (D-D1) ----------------------------------------------
# One record = one row of `팀원 완료 기록`. A todo record points at exactly one confirmed item of its submitter
# (task_id + the confirmation revision it was made against); an `기타` note points at none and carries no file.
# Files and the review are deleted with the record (`되돌리기`, D-D1 D5). The audit log keeps metadata only.
FILE_KINDS = ("PNG", "JPEG", "WEBP", "PDF", "DOCX", "HWP", "HWPX", "TXT", "MD", "PPTX", "XLSX")

execution_records = Table(
    "execution_records",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), ForeignKey("groups.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("action_id", String(64), nullable=False),
    Column("task_id", String(36)),
    Column("source_assignment_revision", Integer),
    Column("confirmation_revision", Integer),
    Column("title", String(40)),
    Column("memo", Text),
    Column("marks_done", Boolean, nullable=False),
    Column("file_count", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # D-H1 (0013): completion attempt state; marks_done always equals completion = 'DONE'
    Column("completion", String(10)),
    Column("completed_by", String(6)),
    ForeignKeyConstraint(
        ["group_id", "user_id", "source_assignment_revision", "confirmation_revision", "task_id"],
        ["todo_confirmed_items.group_id", "todo_confirmed_items.user_id", "todo_confirmed_items.source_assignment_revision",
         "todo_confirmed_items.confirmation_revision", "todo_confirmed_items.task_id"],
        name="fk_execution_records_confirmed_item",
    ),
    UniqueConstraint("group_id", "user_id", "action_id", name="uq_execution_records_action"),
    CheckConstraint("(task_id is null) = (confirmation_revision is null) and (task_id is null) = (source_assignment_revision is null)",
                    name="task_ref"),
    CheckConstraint("task_id is not null or (not marks_done and file_count = 0)", name="etc"),
    CheckConstraint("memo is null or char_length(memo) <= 1000", name="memo"),
    CheckConstraint("file_count between 0 and 5", name="file_count"),
    CheckConstraint("completion is null or completion in ('REVIEWING','VOTING','DONE','REJECTED','WITHDRAWN')",
                    name="completion"),
    CheckConstraint("marks_done = coalesce(completion = 'DONE', false)", name="done_iff"),
    CheckConstraint("(completed_by is not null) = coalesce(completion = 'DONE', false)"
                    " and (completed_by is null or completed_by in ('AI','TEAM','SELF','MANUAL'))", name="completed_by"),
    CheckConstraint("completion is null or task_id is not null", name="completion_task"),
    Index("uq_execution_records_done", "task_id", unique=True, postgresql_where=text("marks_done")),
    Index("uq_execution_records_open", "task_id", unique=True,
          postgresql_where=text("completion in ('REVIEWING','VOTING','DONE')")),
    Index("ix_execution_records_group", "group_id", "created_at"),
)

execution_files = Table(
    "execution_files",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("record_id", String(36), ForeignKey("execution_records.id", ondelete="CASCADE"), nullable=False),
    Column("group_id", String(36), ForeignKey("groups.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("review_kind", String(8)),
    Column("embedded_images", Boolean, nullable=False, server_default=false()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("record_id", "position", name="uq_execution_files_position"),
    CheckConstraint("kind in ('PNG','JPEG','WEBP','PDF','DOCX','HWP','HWPX','TXT','MD','PPTX','XLSX')", name="kind"),
    CheckConstraint("review_kind is null or review_kind in ('IMAGE','PDF','TEXT')", name="review_kind"),
    CheckConstraint("size_bytes between 1 and 10485760", name="size"),
    Index("ix_execution_files_group", "group_id"),
)

execution_reviews = Table(
    "execution_reviews",
    metadata,
    Column("record_id", String(36), ForeignKey("execution_records.id", ondelete="CASCADE"), primary_key=True),
    Column("state", String(10), nullable=False),
    Column("skip_reason", String(16)),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("decision", String(14)),
    Column("criteria_results", JSONB),
    Column("guidance", Text),
    Column("reviewed_files", Integer, nullable=False, server_default="0"),
    Column("excluded_files", Integer, nullable=False, server_default="0"),
    Column("embedded_images_skipped", Boolean, nullable=False, server_default=false()),
    Column("error_code", String(64)),
    Column("model", String(64)),
    Column("request_id", String(128)),
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("total_tokens", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED')", name="state"),
    CheckConstraint("skip_reason is null or skip_reason in ('UNSUPPORTED','OVER_LIMIT','DISABLED')", name="skip_reason"),
    CheckConstraint("(state = 'SKIPPED') = (skip_reason is not null)", name="skip_iff"),
    CheckConstraint("decision is null or decision in ('PASS','REVIEW_NEEDED','NEEDS_INPUT')", name="decision"),
    CheckConstraint("(state = 'SUCCEEDED') = (decision is not null)", name="decision_iff"),
)

# D-H1 (0013): one vote per (record, member). Only current active members' votes count (computed, never stored).
execution_votes = Table(
    "execution_votes",
    metadata,
    Column("record_id", String(36), ForeignKey("execution_records.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("agree", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

execution_events = Table(
    "execution_events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("group_id", String(36), ForeignKey("groups.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("record_id", String(36)),
    Column("task_id", String(36)),
    Column("source_assignment_revision", Integer),
    Column("confirmation_revision", Integer),
    Column("file_count", Integer, nullable=False, server_default="0"),
    Column("detail", String(32)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "kind in ('COMPLETE','SUBMIT','NOTE','UNDO','REVIEW_START','REVIEW_RETRY','REVIEW_DONE','REVIEW_FAILED','REVIEW_SKIPPED',"
        "'VOTE','SETTLED')",
        name="kind",
    ),
    Index("ix_execution_events_group", "group_id", "created_at"),
)


# ---- stage 6: account lifecycle audit (D-D1 D6/D7) -----------------------------------------------------------------
# Metadata only: what happened, to which group id, by which user id. No name, username, text or file name. No foreign
# keys on purpose: the record outlives a deleted group, and a deleted account's id is replaced by its tombstone id.
LIFECYCLE_KINDS = ("LEAVE", "LEADER_HANDOFF", "GROUP_DELETED", "ACCOUNT_DELETED", "PASSWORD_CHANGED", "PROFILE_UPDATED")

lifecycle_events = Table(
    "lifecycle_events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("user_id", String(36)),
    Column("group_id", String(36)),
    Column("detail", String(32)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "kind in ('LEAVE','LEADER_HANDOFF','GROUP_DELETED','ACCOUNT_DELETED','PASSWORD_CHANGED','PROFILE_UPDATED')",
        name="kind",
    ),
    Index("ix_lifecycle_events_created", "created_at"),
)


# ---- stage 8A: portfolio RAG copies (D-I1) ---------------------------------------------------------------------------
# A private copy owned by one user, built from the attachments of that user's own completed records. Every row carries
# `user_id` (users ON DELETE CASCADE) and every child row repeats it in a composite foreign key, so a row can never hang
# under another user's parent. The source columns (`source_group_id`, `source_record_id`, `source_task_id`,
# `source_file_id`) deliberately have NO foreign key to the group tables: the copy outlives a left or deleted group
# (D-I1 I4). No original file bytes, no question/answer table (RAG_DESIGN 3.7), no database extension.
PORTFOLIO_JOB_STATES = ("QUEUED", "EXTRACTING", "DESCRIBING", "UNDERSTANDING", "EMBEDDING", "READY", "FAILED")
PORTFOLIO_STAGES = ("EXTRACTING", "DESCRIBING", "UNDERSTANDING", "EMBEDDING")

portfolio_projects = Table(
    "portfolio_projects",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("source_group_id", String(36), nullable=False),
    Column("name", String(30), nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("role_name", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("id", "user_id", name="uq_portfolio_projects_owner"),
    UniqueConstraint("user_id", "source_group_id", name="uq_portfolio_projects_source"),
)

portfolio_items = Table(
    "portfolio_items",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", String(36), nullable=False),
    Column("source_record_id", String(36), nullable=False),
    Column("source_task_id", String(36), nullable=False),
    Column("task_title", String(60), nullable=False),
    Column("due_date", Date, nullable=False),
    Column("role_name", String(30), nullable=False),
    Column("memo", Text),
    Column("file_count", Integer, nullable=False),
    Column("completed_by", String(6), nullable=False),
    Column("review_decision", String(14)),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["project_id", "user_id"], ["portfolio_projects.id", "portfolio_projects.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_items_project"),
    UniqueConstraint("source_record_id", name="uq_portfolio_items_source_record"),
    UniqueConstraint("id", "user_id", name="uq_portfolio_items_owner"),
    CheckConstraint("completed_by in ('AI','TEAM','SELF','MANUAL')", name="completed_by"),
    CheckConstraint("review_decision is null or review_decision in ('PASS','REVIEW_NEEDED','NEEDS_INPUT')",
                    name="review_decision"),
    CheckConstraint("file_count between 1 and 5", name="file_count"),
    Index("ix_portfolio_items_project", "user_id", "project_id"),
)

portfolio_documents = Table(
    "portfolio_documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("item_id", String(36), nullable=False),
    Column("position", Integer, nullable=False),
    Column("source_file_id", String(36), nullable=False),
    Column("file_name", String(255), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("status", String(10), nullable=False),
    Column("error_code", String(32)),
    Column("truncated", Boolean, nullable=False, server_default=false()),
    Column("truncated_reason", String(32)),
    Column("char_count", Integer, nullable=False, server_default="0"),
    Column("blocks", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["item_id", "user_id"], ["portfolio_items.id", "portfolio_items.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_documents_item"),
    UniqueConstraint("id", "user_id", name="uq_portfolio_documents_owner"),
    UniqueConstraint("item_id", "position", name="uq_portfolio_documents_position"),
    CheckConstraint("kind in ('PNG','JPEG','WEBP','PDF','DOCX','HWP','HWPX','TXT','MD','PPTX','XLSX')", name="kind"),
    CheckConstraint("status in ('EXTRACTED','NAME_ONLY','REJECTED')", name="status"),
    CheckConstraint("(status = 'REJECTED') = (error_code is not null)", name="error_iff"),
    CheckConstraint("truncated = (truncated_reason is not null)", name="truncated_iff"),
)

portfolio_nodes = Table(
    "portfolio_nodes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", String(36), nullable=False),
    Column("item_id", String(36)),
    Column("document_id", String(36)),
    Column("kind", String(16), nullable=False),
    Column("position", Integer, nullable=False),
    Column("locator", JSONB, nullable=False),
    Column("text", Text, nullable=False),
    Column("context", Text),
    Column("index_text", Text, nullable=False),
    Column("bigrams", JSONB, nullable=False),
    Column("token_estimate", Integer, nullable=False),
    Column("prev_id", String(36)),
    Column("next_id", String(36)),
    Column("source_block_ids", JSONB, nullable=False),
    Column("embedding", LargeBinary),
    Column("embed_model", String(64)),
    Column("embed_dim", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["project_id", "user_id"], ["portfolio_projects.id", "portfolio_projects.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_nodes_project"),
    ForeignKeyConstraint(["item_id", "user_id"], ["portfolio_items.id", "portfolio_items.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_nodes_item"),
    ForeignKeyConstraint(["document_id", "user_id"], ["portfolio_documents.id", "portfolio_documents.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_nodes_document"),
    CheckConstraint("kind in ('chunk','doc_summary','project_summary')", name="kind"),
    CheckConstraint("kind = 'project_summary' or item_id is not null", name="item_ref"),
    CheckConstraint("kind <> 'chunk' or document_id is not null", name="chunk_doc"),
    CheckConstraint("(embedding is null) = (embed_model is null) and (embedding is null) = (embed_dim is null)",
                    name="embed_meta"),
    Index("ix_portfolio_nodes_user", "user_id", "project_id"),
)

portfolio_jobs = Table(
    "portfolio_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("item_id", String(36), nullable=False),
    Column("state", String(14), nullable=False),
    Column("resume_stage", String(14)),
    Column("failed_stage", String(14)),
    Column("error_code", String(64)),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("stage_meta", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    ForeignKeyConstraint(["item_id", "user_id"], ["portfolio_items.id", "portfolio_items.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_jobs_item"),
    UniqueConstraint("item_id", name="uq_portfolio_jobs_item"),
    CheckConstraint("state in ('QUEUED','EXTRACTING','DESCRIBING','UNDERSTANDING','EMBEDDING','READY','FAILED')",
                    name="state"),
    CheckConstraint("resume_stage is null or resume_stage in ('EXTRACTING','DESCRIBING','UNDERSTANDING','EMBEDDING')",
                    name="resume_stage"),
    CheckConstraint("failed_stage is null or failed_stage in ('EXTRACTING','DESCRIBING','UNDERSTANDING','EMBEDDING')",
                    name="failed_stage"),
    CheckConstraint("(state = 'FAILED') = (failed_stage is not null) and (state = 'FAILED') = (error_code is not null)",
                    name="failed_iff"),
    CheckConstraint("revision >= 1", name="revision"),
    Index("ix_portfolio_jobs_state", "state"),
)

# ---- stage 8D: portfolio summaries (D-I2) ---------------------------------------------------------------------------

portfolio_summaries = Table(
    "portfolio_summaries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", String(36)),
    Column("scope", String(9), nullable=False),
    Column("state", String(7), nullable=False),
    Column("content", JSONB),
    Column("basis", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error_code", String(64)),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("last_action_id", String(64)),
    Column("stage_meta", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("generated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["project_id", "user_id"], ["portfolio_projects.id", "portfolio_projects.user_id"],
                         ondelete="CASCADE", name="fk_portfolio_summaries_project"),
    CheckConstraint("scope in ('PROJECT','PORTFOLIO')", name="scope"),
    CheckConstraint("(scope = 'PROJECT') = (project_id is not null)", name="project"),
    CheckConstraint("state in ('RUNNING','READY','FAILED')", name="state"),
    CheckConstraint("(state = 'FAILED') = (error_code is not null)", name="failed_iff"),
    CheckConstraint("revision >= 1", name="revision"),
    Index("uq_portfolio_summaries_project", "user_id", "project_id", unique=True,
          postgresql_where=text("scope = 'PROJECT'")),
    Index("uq_portfolio_summaries_portfolio", "user_id", unique=True, postgresql_where=text("scope = 'PORTFOLIO'")),
    Index("ix_portfolio_summaries_state", "state"),
)
