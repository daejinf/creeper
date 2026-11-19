"""stage 4 session A: ROLE_TODOS, role assignment revisions, team coverage, post-assignment interview

Revision ID: 0008
Revises: 0007

Nothing existing is deleted: the stage 3 interview tables (interview_turns, member_states,
member_progress.interview_closed_at) and every preference submission stay as they are. The one-way
`submission_iff_waiting` check is replaced so a ROLE_TODOS member keeps the submission they were assigned from.
Downgrade turns ROLE_TODOS members back into WAITING_ASSIGNMENT (their submission is kept) and drops only the
stage 4 tables created here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _usage():
    return [
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    ]


INTERVIEW_KEY = ["group_id", "user_id", "source_assignment_revision"]
INTERVIEW_REF = ["role_interviews.group_id", "role_interviews.user_id", "role_interviews.source_assignment_revision"]


def upgrade() -> None:
    op.create_table(
        "role_assignments",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_cohort_revision", sa.Integer(), nullable=False),
        sa.Column("source_candidate_revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("state", sa.String(8), nullable=False),
        sa.Column("stale_reason", sa.String(32), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draw_audit", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "revision", name=op.f("pk_role_assignments")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_role_assignments_group_id_groups")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_candidate_revision"],
            ["role_candidate_sets.group_id", "role_candidate_sets.source_project_brief_revision"],
            name=op.f("fk_role_assignments_group_id_role_candidate_sets"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_role_assignments_revision")),
        sa.CheckConstraint("operation in ('INITIAL','REASSIGN')", name=op.f("ck_role_assignments_operation")),
        sa.CheckConstraint("state in ('CURRENT','STALE')", name=op.f("ck_role_assignments_state")),
        sa.CheckConstraint("(state = 'STALE') = (stale_at is not null)", name=op.f("ck_role_assignments_stale_pair")),
    )
    op.create_index(
        "uq_role_assignments_current", "role_assignments", ["group_id"], unique=True, postgresql_where=sa.text("state = 'CURRENT'")
    )

    op.create_table(
        "role_assignment_members",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("assignment_revision", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("submission_revision", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.String(8), nullable=False),
        sa.Column("role_id", sa.String(16), nullable=False),
        sa.Column("role_name", sa.String(30), nullable=False),
        sa.Column("role_description", sa.String(120), nullable=False),
        sa.Column("assigned_rank", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("group_id", "assignment_revision", "user_id", name=op.f("pk_role_assignment_members")),
        sa.ForeignKeyConstraint(
            ["group_id", "assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"],
            name=op.f("fk_role_assignment_members_group_id_role_assignments"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "user_id", "submission_revision"],
            ["preference_submissions.group_id", "preference_submissions.user_id", "preference_submissions.revision"],
            name=op.f("fk_role_assignment_members_group_id_preference_submissions"),
        ),
        sa.UniqueConstraint("group_id", "assignment_revision", "slot_id", name="uq_role_assignment_members_slot"),
        sa.CheckConstraint("assigned_rank is null or assigned_rank between 1 and 3", name=op.f("ck_role_assignment_members_rank")),
    )

    op.create_table(
        "team_coverages",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_action_id", sa.String(64), nullable=True),
        sa.Column("parts", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        *_usage(),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "source_assignment_revision", name=op.f("pk_team_coverages")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_assignment_revision"], ["role_assignments.group_id", "role_assignments.revision"],
            name=op.f("fk_team_coverages_group_id_role_assignments"),
        ),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_team_coverages_state")),
        sa.CheckConstraint("(state = 'SUCCEEDED') = (parts is not null)", name=op.f("ck_team_coverages_parts_iff_succeeded")),
    )

    op.create_table(
        "role_interviews",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("questions_asked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "source_assignment_revision", name=op.f("pk_role_interviews")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_assignment_revision", "user_id"],
            ["role_assignment_members.group_id", "role_assignment_members.assignment_revision", "role_assignment_members.user_id"],
            name=op.f("fk_role_interviews_group_id_role_assignment_members"),
        ),
        sa.CheckConstraint("state in ('ASKING','CONFIRMING','DONE')", name=op.f("ck_role_interviews_state")),
        sa.CheckConstraint("questions_asked between 0 and 5", name=op.f("ck_role_interviews_questions_asked")),
    )

    op.create_table(
        "role_interview_turns",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("answer_question_id", sa.String(64), nullable=True),
        sa.Column("answer_option_id", sa.String(64), nullable=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("question", postgresql.JSONB(), nullable=True),
        sa.Column("state_revision", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        *_usage(),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "source_assignment_revision", "seq", name=op.f("pk_role_interview_turns")),
        sa.ForeignKeyConstraint(INTERVIEW_KEY, INTERVIEW_REF, name=op.f("fk_role_interview_turns_group_id_role_interviews")),
        sa.UniqueConstraint("group_id", "user_id", "action_id", name="uq_role_interview_turns_action"),
        sa.CheckConstraint("kind in ('OPEN','ANSWER','EDIT')", name=op.f("ck_role_interview_turns_kind")),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_role_interview_turns_state")),
        sa.CheckConstraint("seq between 0 and 40", name=op.f("ck_role_interview_turns_seq")),
        sa.CheckConstraint("(kind = 'OPEN') = (user_message is null)", name=op.f("ck_role_interview_turns_message_unless_open")),
        sa.CheckConstraint(
            "user_message is null or char_length(user_message) between 1 and 300", name=op.f("ck_role_interview_turns_message_length")
        ),
        sa.CheckConstraint(
            "(answer_question_id is null) = (answer_option_id is null)", name=op.f("ck_role_interview_turns_answer_pair")
        ),
    )
    op.create_index(
        "uq_role_interview_turns_open", "role_interview_turns", ["group_id", "user_id", "source_assignment_revision"],
        unique=True, postgresql_where=sa.text("state in ('PENDING','RUNNING','FAILED')"),
    )

    op.create_table(
        "role_member_states",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("source_turn_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "source_assignment_revision", "revision", name=op.f("pk_role_member_states")),
        sa.ForeignKeyConstraint(INTERVIEW_KEY, INTERVIEW_REF, name=op.f("fk_role_member_states_group_id_role_interviews")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_role_member_states_revision")),
    )

    op.create_table(
        "role_interview_snapshots",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("member_state_revision", sa.Integer(), nullable=False),
        sa.Column("provider_input", postgresql.JSONB(), nullable=False),
        sa.Column("private_constraints", postgresql.JSONB(), nullable=False),
        sa.Column("deadline_limits", postgresql.JSONB(), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "source_assignment_revision", name=op.f("pk_role_interview_snapshots")),
        sa.ForeignKeyConstraint(INTERVIEW_KEY, INTERVIEW_REF, name=op.f("fk_role_interview_snapshots_group_id_role_interviews")),
    )

    # member_progress: ROLE_TODOS keeps its submission and names the assignment revision.
    op.add_column("member_progress", sa.Column("assignment_revision", sa.Integer(), nullable=True))
    op.drop_constraint(op.f("ck_member_progress_submission_iff_waiting"), "member_progress", type_="check")
    op.drop_constraint(op.f("ck_member_progress_status"), "member_progress", type_="check")
    op.create_check_constraint(
        op.f("ck_member_progress_status"), "member_progress", "status in ('INTERVIEW','WAITING_ASSIGNMENT','ROLE_TODOS')"
    )
    op.create_check_constraint(
        op.f("ck_member_progress_submission_unless_interview"), "member_progress", "(status = 'INTERVIEW') = (submission_revision is null)"
    )
    op.create_check_constraint(
        op.f("ck_member_progress_assignment_iff_role_todos"), "member_progress",
        "(status = 'ROLE_TODOS') = (assignment_revision is not null)",
    )
    op.create_foreign_key(
        op.f("fk_member_progress_group_id_role_assignments"), "member_progress", "role_assignments",
        ["group_id", "assignment_revision"], ["group_id", "revision"],
    )


def downgrade() -> None:
    op.execute(
        "update member_progress set status = 'WAITING_ASSIGNMENT', assignment_revision = null where status = 'ROLE_TODOS'"
    )
    op.drop_constraint(op.f("fk_member_progress_group_id_role_assignments"), "member_progress", type_="foreignkey")
    op.drop_constraint(op.f("ck_member_progress_assignment_iff_role_todos"), "member_progress", type_="check")
    op.drop_constraint(op.f("ck_member_progress_submission_unless_interview"), "member_progress", type_="check")
    op.drop_constraint(op.f("ck_member_progress_status"), "member_progress", type_="check")
    op.create_check_constraint(op.f("ck_member_progress_status"), "member_progress", "status in ('INTERVIEW','WAITING_ASSIGNMENT')")
    op.create_check_constraint(
        op.f("ck_member_progress_submission_iff_waiting"), "member_progress",
        "(status = 'WAITING_ASSIGNMENT') = (submission_revision is not null)",
    )
    op.drop_column("member_progress", "assignment_revision")
    op.drop_table("role_interview_snapshots")
    op.drop_table("role_member_states")
    op.drop_index("uq_role_interview_turns_open", table_name="role_interview_turns")
    op.drop_table("role_interview_turns")
    op.drop_table("role_interviews")
    op.drop_table("team_coverages")
    op.drop_table("role_assignment_members")
    op.drop_index("uq_role_assignments_current", table_name="role_assignments")
    op.drop_table("role_assignments")
