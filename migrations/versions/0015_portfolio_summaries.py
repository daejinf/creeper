"""stage 8D: portfolio summaries (D-I2): one project summary per project, one portfolio summary per user

Revision ID: 0015
Revises: 0014

One new table, owned by one user like the other `portfolio_*` tables: `user_id` (users ON DELETE CASCADE) and, for a
project summary, a composite foreign key to the project's (id, user_id) with CASCADE (a deleted project takes its
summary). `scope` is PROJECT (project_id set) or PORTFOLIO (project_id null); a partial unique index keeps one of each.

`content` is the last good result (kept when a later run fails); `state` is the latest run. `basis` records what the
content was made from (READY item ids, or the project summaries' generation times) so the API can show "오래됨"
without regenerating anything (RAG_DESIGN 2.5.1-2.5.2). `last_action_id` makes the owner's `만들기` idempotent.

The searchable `project_summary` node lives in `portfolio_nodes` (kind already allowed there since 0014). Nothing else
changes. Downgrade drops this table only (summaries are lost; the RAG copies stay).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36)),
        sa.Column("scope", sa.String(9), nullable=False),
        sa.Column("state", sa.String(7), nullable=False),
        sa.Column("content", postgresql.JSONB),
        sa.Column("basis", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_action_id", sa.String(64)),
        sa.Column("stage_meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id", "user_id"], ["portfolio_projects.id", "portfolio_projects.user_id"],
                                ondelete="CASCADE", name="fk_portfolio_summaries_project"),
        sa.CheckConstraint("scope in ('PROJECT','PORTFOLIO')", name=op.f("ck_portfolio_summaries_scope")),
        sa.CheckConstraint("(scope = 'PROJECT') = (project_id is not null)", name=op.f("ck_portfolio_summaries_project")),
        sa.CheckConstraint("state in ('RUNNING','READY','FAILED')", name=op.f("ck_portfolio_summaries_state")),
        sa.CheckConstraint("(state = 'FAILED') = (error_code is not null)", name=op.f("ck_portfolio_summaries_failed_iff")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_portfolio_summaries_revision")),
    )
    op.create_index("uq_portfolio_summaries_project", "portfolio_summaries", ["user_id", "project_id"], unique=True,
                    postgresql_where=sa.text("scope = 'PROJECT'"))
    op.create_index("uq_portfolio_summaries_portfolio", "portfolio_summaries", ["user_id"], unique=True,
                    postgresql_where=sa.text("scope = 'PORTFOLIO'"))
    op.create_index("ix_portfolio_summaries_state", "portfolio_summaries", ["state"])


def downgrade() -> None:
    op.drop_table("portfolio_summaries")
