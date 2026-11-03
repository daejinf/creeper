"""Add hybrid evaluation state and option source."""
from alembic import op
import sqlalchemy as sa

revision = "0004_hybrid_evaluation"
down_revision = "0003_conversation_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_snapshots", sa.Column("evaluation_status", sa.String(30), nullable=False, server_default="LOCAL_READY"))
    op.add_column("evaluation_snapshots", sa.Column("evaluated_message_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("evaluation_snapshots", sa.Column("ai_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("evaluation_snapshots", sa.Column("error_code", sa.String(60), nullable=True))
    op.add_column("evaluation_snapshots", sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_evaluation_snapshots_evaluation_status", "evaluation_snapshots", ["evaluation_status"])
    op.add_column("decision_options", sa.Column("source", sa.String(20), nullable=False, server_default="AI"))


def downgrade() -> None:
    op.drop_column("decision_options", "source")
    op.drop_index("ix_evaluation_snapshots_evaluation_status", table_name="evaluation_snapshots")
    for name in ["enriched_at", "error_code", "ai_attempts", "evaluated_message_count", "evaluation_status"]:
        op.drop_column("evaluation_snapshots", name)
