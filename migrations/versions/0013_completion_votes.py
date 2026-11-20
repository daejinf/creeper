"""D-H1: a todo is completed only by an AI PASS or a team majority; leader group deletion

Revision ID: 0013
Revises: 0012

`execution_records.completion` is the state of a completion attempt (a submission with files for one of the
submitter's own todos): REVIEWING (M2 running) -> DONE (PASS) or VOTING (any other M2 outcome: hold, unsupported,
over the limit, failed) -> DONE (majority of the other current members agree) or REJECTED (that majority can no longer
be reached). A newer attempt for the same todo turns an open VOTING one into WITHDRAWN. Notes (no files, or `기타`)
keep `completion` null. `marks_done` stays and always equals `completion = 'DONE'` (every count that reads it keeps
working). `completed_by` says who completed it: AI, TEAM, SELF (no other member left to ask) or MANUAL (the stage 5
checkbox, before this revision).

`execution_votes` holds one vote per (record, member). Only votes of current active members count (computed, never
stored). Votes go with their record (cascade) and with a deleted account (cascade).

Existing rows: marks_done -> DONE/MANUAL. Downgrade drops the new table and columns only.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

OLD_EVENTS = ("('COMPLETE','SUBMIT','NOTE','UNDO','REVIEW_START','REVIEW_RETRY','REVIEW_DONE','REVIEW_FAILED',"
              "'REVIEW_SKIPPED')")
NEW_EVENTS = ("('COMPLETE','SUBMIT','NOTE','UNDO','REVIEW_START','REVIEW_RETRY','REVIEW_DONE','REVIEW_FAILED',"
              "'REVIEW_SKIPPED','VOTE','SETTLED')")


def upgrade() -> None:
    op.add_column("execution_records", sa.Column("completion", sa.String(10), nullable=True))
    op.add_column("execution_records", sa.Column("completed_by", sa.String(6), nullable=True))
    op.execute("update execution_records set completion = 'DONE', completed_by = 'MANUAL' where marks_done")
    op.create_check_constraint(
        op.f("ck_execution_records_completion"), "execution_records",
        "completion is null or completion in ('REVIEWING','VOTING','DONE','REJECTED','WITHDRAWN')")
    op.create_check_constraint(
        op.f("ck_execution_records_done_iff"), "execution_records", "marks_done = coalesce(completion = 'DONE', false)")
    op.create_check_constraint(
        op.f("ck_execution_records_completed_by"), "execution_records",
        "(completed_by is not null) = coalesce(completion = 'DONE', false)"
        " and (completed_by is null or completed_by in ('AI','TEAM','SELF','MANUAL'))")
    op.create_check_constraint(
        op.f("ck_execution_records_completion_task"), "execution_records", "completion is null or task_id is not null")
    op.create_index("uq_execution_records_open", "execution_records", ["task_id"], unique=True,
                    postgresql_where=sa.text("completion in ('REVIEWING','VOTING','DONE')"))

    op.create_table(
        "execution_votes",
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("agree", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("record_id", "user_id", name=op.f("pk_execution_votes")),
        sa.ForeignKeyConstraint(["record_id"], ["execution_records.id"], ondelete="CASCADE",
                                name=op.f("fk_execution_votes_record_id_execution_records")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name=op.f("fk_execution_votes_user_id_users")),
    )

    op.drop_constraint(op.f("ck_execution_events_kind"), "execution_events", type_="check")
    op.create_check_constraint(op.f("ck_execution_events_kind"), "execution_events", "kind in " + NEW_EVENTS)


def downgrade() -> None:
    op.execute("delete from execution_events where kind in ('VOTE','SETTLED')")
    op.drop_constraint(op.f("ck_execution_events_kind"), "execution_events", type_="check")
    op.create_check_constraint(op.f("ck_execution_events_kind"), "execution_events", "kind in " + OLD_EVENTS)
    op.drop_table("execution_votes")
    op.drop_index("uq_execution_records_open", table_name="execution_records")
    for name in ("completion_task", "completed_by", "done_iff", "completion"):
        op.drop_constraint(op.f(f"ck_execution_records_{name}"), "execution_records", type_="check")
    op.drop_column("execution_records", "completed_by")
    op.drop_column("execution_records", "completion")
