"""ai_jobs: persisted AI job state (PENDING/RUNNING/SUCCEEDED/FAILED)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_jobs")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_ai_jobs_operation_id")),
        sa.CheckConstraint(
            "state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_ai_jobs_state")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_ai_jobs_revision")),
    )
    op.create_index("ix_ai_jobs_state", "ai_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_state", table_name="ai_jobs")
    op.drop_table("ai_jobs")
