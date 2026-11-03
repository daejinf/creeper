"""Add kickoff coaching sessions and dynamic readiness."""
from alembic import op
import sqlalchemy as sa

revision = "0002_coaching_sessions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("session_id", sa.String(36), nullable=True))
    op.create_index("ix_plans_session_id", "plans", ["session_id"])
    op.create_table(
        "kickoff_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(80), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_plan_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kickoff_sessions_group_id", "kickoff_sessions", ["group_id"])
    op.create_index("ix_kickoff_sessions_status", "kickoff_sessions", ["status"])
    op.create_table(
        "session_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.String(80), nullable=False),
        sa.Column("nickname", sa.String(40), nullable=False),
        sa.Column("avatar_color", sa.String(20), nullable=False),
        sa.Column("intake_status", sa.String(30), nullable=False),
        sa.UniqueConstraint("session_id", "member_id", name="uq_session_member"),
    )
    op.create_index("ix_session_members_session_id", "session_members", ["session_id"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.String(80), nullable=True),
        sa.Column("author_type", sa.String(20), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("message_type", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_table(
        "coaching_states",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("next_action", sa.String(50), nullable=False),
        sa.Column("plan_ready", sa.Boolean(), nullable=False),
        sa.Column("last_question_dimension", sa.String(80), nullable=True),
        sa.Column("human_messages_since_ai", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "coaching_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("dimension", sa.String(80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("owner_member_id", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_coaching_facts_session_id", "coaching_facts", ["session_id"])
    op.create_index("ix_coaching_facts_dimension", "coaching_facts", ["dimension"])
    op.create_table(
        "private_intakes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.String(80), nullable=False),
        sa.Column("skill_level", sa.String(30), nullable=False),
        sa.Column("weekly_available_minutes", sa.Integer(), nullable=False),
        sa.Column("preferred_times", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "member_id", name="uq_private_intake_member"),
    )
    op.create_index("ix_private_intakes_session_id", "private_intakes", ["session_id"])


def downgrade() -> None:
    op.drop_table("private_intakes")
    op.drop_table("coaching_facts")
    op.drop_table("coaching_states")
    op.drop_table("conversation_messages")
    op.drop_table("session_members")
    op.drop_table("kickoff_sessions")
    op.drop_index("ix_plans_session_id", table_name="plans")
    op.drop_column("plans", "session_id")

