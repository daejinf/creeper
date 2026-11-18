"""D-B3: mark the personal interview data as deprecated (comments only; nothing is deleted or changed)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NOTE = "DEPRECATED since D-B3 (2026-11-19): kept for the stage 4 post-assignment interview, not written by stage 3"


def upgrade() -> None:
    op.create_table_comment("interview_turns", NOTE)
    op.create_table_comment("member_states", NOTE)
    op.alter_column("member_progress", "interview_closed_at", existing_type=sa.DateTime(timezone=True), existing_nullable=True, comment=NOTE)


def downgrade() -> None:
    op.alter_column("member_progress", "interview_closed_at", existing_type=sa.DateTime(timezone=True), existing_nullable=True, existing_comment=NOTE, comment=None)
    op.drop_table_comment("member_states")
    op.drop_table_comment("interview_turns")
