"""D-B2: button answers, 10-turn budget, member-chosen interview close

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_turns", sa.Column("answer_question_id", sa.String(64), nullable=True))
    op.add_column("interview_turns", sa.Column("answer_option_id", sa.String(64), nullable=True))
    op.drop_constraint(op.f("ck_interview_turns_seq"), "interview_turns", type_="check")
    op.create_check_constraint(op.f("ck_interview_turns_seq"), "interview_turns", "seq between 1 and 10")
    op.create_check_constraint(
        op.f("ck_interview_turns_answer_pair"), "interview_turns", "(answer_question_id is null) = (answer_option_id is null)"
    )
    op.add_column("member_progress", sa.Column("interview_closed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("member_progress", "interview_closed_at")
    op.drop_constraint(op.f("ck_interview_turns_answer_pair"), "interview_turns", type_="check")
    op.drop_constraint(op.f("ck_interview_turns_seq"), "interview_turns", type_="check")
    op.create_check_constraint(op.f("ck_interview_turns_seq"), "interview_turns", "seq between 1 and 8")
    op.drop_column("interview_turns", "answer_option_id")
    op.drop_column("interview_turns", "answer_question_id")
