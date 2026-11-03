"""Add conversation evaluation, decision topics, options and votes."""
from alembic import op
import sqlalchemy as sa

revision = "0003_conversation_evaluation"
down_revision = "0002_coaching_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kickoff_sessions", sa.Column("latest_evaluation_id", sa.String(36), nullable=True))
    op.add_column("kickoff_sessions", sa.Column("messages_since_evaluation", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "evaluation_snapshots",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False), sa.Column("evaluated_through_message_id", sa.String(36), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=False), sa.Column("ai_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "version", name="uq_evaluation_session_version"),
    )
    op.create_index("ix_evaluation_snapshots_session_id", "evaluation_snapshots", ["session_id"])
    op.create_index("ix_evaluation_snapshots_ai_run_id", "evaluation_snapshots", ["ai_run_id"])
    op.create_table(
        "decision_topics",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False), sa.Column("dimension", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False), sa.Column("recommendation_reason", sa.Text(), nullable=True),
        sa.Column("inferred", sa.Boolean(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("approved_option_id", sa.String(36), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ["snapshot_id", "session_id", "dimension", "status"]:
        op.create_index(f"ix_decision_topics_{column}", "decision_topics", [column])
    op.create_table(
        "decision_options",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("topic_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(500), nullable=False), sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False), sa.Column("proposer_member_ids", sa.JSON(), nullable=False),
        sa.Column("recommended", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_options_topic_id", "decision_options", ["topic_id"])
    op.create_table(
        "decision_votes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("topic_id", sa.String(36), nullable=False),
        sa.Column("option_id", sa.String(36), nullable=True), sa.Column("member_id", sa.String(80), nullable=False),
        sa.Column("choice", sa.String(20), nullable=False), sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("topic_id", "member_id", "option_id", name="uq_decision_vote_target"),
    )
    for column in ["topic_id", "option_id", "member_id"]:
        op.create_index(f"ix_decision_votes_{column}", "decision_votes", [column])


def downgrade() -> None:
    op.drop_table("decision_votes")
    op.drop_table("decision_options")
    op.drop_table("decision_topics")
    op.drop_table("evaluation_snapshots")
    op.drop_column("kickoff_sessions", "messages_since_evaluation")
    op.drop_column("kickoff_sessions", "latest_evaluation_id")
