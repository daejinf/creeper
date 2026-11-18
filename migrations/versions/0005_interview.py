"""interview: role candidate sets, interview turns, member states, preference submissions, member progress

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _stamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _usage():
    return [
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "role_candidate_sets",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("source_project_brief_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_action_id", sa.String(64), nullable=True),
        sa.Column("candidates", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        *_usage(),
        *_stamps(),
        sa.PrimaryKeyConstraint("group_id", "source_project_brief_revision", name=op.f("pk_role_candidate_sets")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_project_brief_revision"],
            ["project_briefs.group_id", "project_briefs.revision"],
            name=op.f("fk_role_candidate_sets_group_id_project_briefs"),
        ),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_role_candidate_sets_state")),
        sa.CheckConstraint(
            "(state = 'SUCCEEDED') = (candidates is not null)", name=op.f("ck_role_candidate_sets_candidates_iff_succeeded")
        ),
    )

    op.create_table(
        "interview_turns",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("ai_reply", sa.Text(), nullable=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        *_usage(),
        *_stamps(),
        sa.PrimaryKeyConstraint("group_id", "user_id", "seq", name=op.f("pk_interview_turns")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_interview_turns_group_id_groups")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_interview_turns_user_id_users")),
        sa.UniqueConstraint("group_id", "user_id", "action_id", name=op.f("uq_interview_turns_group_id")),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_interview_turns_state")),
        sa.CheckConstraint("seq between 1 and 8", name=op.f("ck_interview_turns_seq")),
        sa.CheckConstraint("char_length(user_message) between 1 and 300", name=op.f("ck_interview_turns_message_length")),
        sa.CheckConstraint("(state = 'SUCCEEDED') = (ai_reply is not null)", name=op.f("ck_interview_turns_reply_iff_succeeded")),
    )
    op.create_index(
        "uq_interview_turns_open", "interview_turns", ["group_id", "user_id"], unique=True,
        postgresql_where=sa.text("state in ('PENDING','RUNNING','FAILED')"),
    )

    op.create_table(
        "member_states",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("source_turn_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "revision", name=op.f("pk_member_states")),
        sa.ForeignKeyConstraint(
            ["group_id", "user_id", "source_turn_seq"],
            ["interview_turns.group_id", "interview_turns.user_id", "interview_turns.seq"],
            name=op.f("fk_member_states_group_id_interview_turns"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_member_states_revision")),
    )

    op.create_table(
        "preference_submissions",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_candidate_revision", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("rank1", sa.String(16), nullable=False),
        sa.Column("rank2", sa.String(16), nullable=False),
        sa.Column("rank3", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "revision", name=op.f("pk_preference_submissions")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_preference_submissions_user_id_users")),
        sa.ForeignKeyConstraint(
            ["group_id", "source_candidate_revision"],
            ["role_candidate_sets.group_id", "role_candidate_sets.source_project_brief_revision"],
            name=op.f("fk_preference_submissions_group_id_role_candidate_sets"),
        ),
        sa.UniqueConstraint("group_id", "user_id", "action_id", name=op.f("uq_preference_submissions_group_id")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_preference_submissions_revision")),
        sa.CheckConstraint(
            "rank1 <> rank2 and rank1 <> rank3 and rank2 <> rank3", name=op.f("ck_preference_submissions_distinct_ranks")
        ),
    )

    op.create_table(
        "member_progress",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submission_revision", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", name=op.f("pk_member_progress")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_member_progress_user_id_users")),
        sa.ForeignKeyConstraint(
            ["group_id", "user_id", "submission_revision"],
            ["preference_submissions.group_id", "preference_submissions.user_id", "preference_submissions.revision"],
            name=op.f("fk_member_progress_group_id_preference_submissions"),
        ),
        sa.CheckConstraint("status in ('INTERVIEW','WAITING_ASSIGNMENT')", name=op.f("ck_member_progress_status")),
        sa.CheckConstraint(
            "(status = 'WAITING_ASSIGNMENT') = (submission_revision is not null)",
            name=op.f("ck_member_progress_submission_iff_waiting"),
        ),
    )


def downgrade() -> None:
    op.drop_table("member_progress")
    op.drop_table("preference_submissions")
    op.drop_table("member_states")
    op.drop_index("uq_interview_turns_open", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_table("role_candidate_sets")
