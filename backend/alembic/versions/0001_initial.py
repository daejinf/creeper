"""Initial M0/M1 tables."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_runs_task", "ai_runs", ["task"])
    op.create_index("ix_ai_runs_status", "ai_runs", ["status"])
    op.create_index("ix_ai_runs_input_hash", "ai_runs", ["input_hash"])
    op.create_table(
        "plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("proposal_payload", sa.JSON(), nullable=False),
        sa.Column("previous_plan_id", sa.String(36), nullable=True),
        sa.Column("ai_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("group_id", "version", name="uq_plan_group_version"),
    )
    op.create_index("ix_plans_group_id", "plans", ["group_id"])
    op.create_index("ix_plans_status", "plans", ["status"])
    op.create_index("ix_plans_ai_run_id", "plans", ["ai_run_id"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation", "idempotency_key", name="uq_idempotency_operation_key"),
    )
    op.create_index("ix_idempotency_records_operation", "idempotency_records", ["operation"])
    op.create_index("ix_idempotency_records_idempotency_key", "idempotency_records", ["idempotency_key"])
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_version", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("evaluation_runs")
    op.drop_table("idempotency_records")
    op.drop_table("plans")
    op.drop_table("ai_runs")

