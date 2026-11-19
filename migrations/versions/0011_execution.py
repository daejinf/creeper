"""stage 5: execution records (completions and submissions), attachments, M2 reviews and the execution audit log

Revision ID: 0011
Revises: 0010

New tables only. A record is one row of the original `팀원 완료 기록`: a manual completion (checkbox), a submission
for one of the submitter's own confirmed todos (`task_id` + the confirmation revision it was made against), or an
`기타` note with no todo (and then no file). Files and the M2 review hang off the record and are deleted with it
(`되돌리기` deletes permanently, D-D1 D5). The audit log keeps metadata only (no file name, memo or title), so it
survives that deletion. Downgrade drops only these tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

FILE_KINDS = "('PNG','JPEG','WEBP','PDF','DOCX','HWP','HWPX','TXT','MD','PPTX','XLSX')"
EVENT_KINDS = ("('COMPLETE','SUBMIT','NOTE','UNDO','REVIEW_START','REVIEW_RETRY','REVIEW_DONE','REVIEW_FAILED',"
               "'REVIEW_SKIPPED')")


def upgrade() -> None:
    op.create_table(
        "execution_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=True),
        sa.Column("confirmation_revision", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(40), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("marks_done", sa.Boolean(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_records")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_execution_records_group_id_groups")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_execution_records_user_id_users")),
        sa.ForeignKeyConstraint(
            ["group_id", "user_id", "source_assignment_revision", "confirmation_revision", "task_id"],
            ["todo_confirmed_items.group_id", "todo_confirmed_items.user_id",
             "todo_confirmed_items.source_assignment_revision", "todo_confirmed_items.confirmation_revision",
             "todo_confirmed_items.task_id"],
            name="fk_execution_records_confirmed_item",
        ),
        sa.UniqueConstraint("group_id", "user_id", "action_id", name="uq_execution_records_action"),
        sa.CheckConstraint(
            "(task_id is null) = (confirmation_revision is null) and (task_id is null) = (source_assignment_revision is null)",
            name=op.f("ck_execution_records_task_ref"),
        ),
        sa.CheckConstraint("task_id is not null or (not marks_done and file_count = 0)", name=op.f("ck_execution_records_etc")),
        sa.CheckConstraint("memo is null or char_length(memo) <= 1000", name=op.f("ck_execution_records_memo")),
        sa.CheckConstraint("file_count between 0 and 5", name=op.f("ck_execution_records_file_count")),
    )
    op.create_index("uq_execution_records_done", "execution_records", ["task_id"], unique=True,
                    postgresql_where=sa.text("marks_done"))
    op.create_index("ix_execution_records_group", "execution_records", ["group_id", "created_at"])

    op.create_table(
        "execution_files",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("review_kind", sa.String(8), nullable=True),
        sa.Column("embedded_images", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_files")),
        sa.ForeignKeyConstraint(["record_id"], ["execution_records.id"], ondelete="CASCADE",
                                name=op.f("fk_execution_files_record_id_execution_records")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_execution_files_group_id_groups")),
        sa.UniqueConstraint("record_id", "position", name="uq_execution_files_position"),
        sa.CheckConstraint("kind in " + FILE_KINDS, name=op.f("ck_execution_files_kind")),
        sa.CheckConstraint("review_kind is null or review_kind in ('IMAGE','PDF','TEXT')",
                           name=op.f("ck_execution_files_review_kind")),
        sa.CheckConstraint("size_bytes between 1 and 10485760", name=op.f("ck_execution_files_size")),
    )
    op.create_index("ix_execution_files_group", "execution_files", ["group_id"])

    op.create_table(
        "execution_reviews",
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("skip_reason", sa.String(16), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(14), nullable=True),
        sa.Column("criteria_results", postgresql.JSONB(), nullable=True),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("reviewed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedded_images_skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("record_id", name=op.f("pk_execution_reviews")),
        sa.ForeignKeyConstraint(["record_id"], ["execution_records.id"], ondelete="CASCADE",
                                name=op.f("fk_execution_reviews_record_id_execution_records")),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED')",
                           name=op.f("ck_execution_reviews_state")),
        sa.CheckConstraint("skip_reason is null or skip_reason in ('UNSUPPORTED','OVER_LIMIT','DISABLED')",
                           name=op.f("ck_execution_reviews_skip_reason")),
        sa.CheckConstraint("(state = 'SKIPPED') = (skip_reason is not null)", name=op.f("ck_execution_reviews_skip_iff")),
        sa.CheckConstraint("decision is null or decision in ('PASS','REVIEW_NEEDED','NEEDS_INPUT')",
                           name=op.f("ck_execution_reviews_decision")),
        sa.CheckConstraint("(state = 'SUCCEEDED') = (decision is not null)", name=op.f("ck_execution_reviews_decision_iff")),
    )

    op.create_table(
        "execution_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("record_id", sa.String(36), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=True),
        sa.Column("confirmation_revision", sa.Integer(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_events")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name=op.f("fk_execution_events_group_id_groups")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_execution_events_user_id_users")),
        sa.CheckConstraint("kind in " + EVENT_KINDS, name=op.f("ck_execution_events_kind")),
    )
    op.create_index("ix_execution_events_group", "execution_events", ["group_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_execution_events_group", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_table("execution_reviews")
    op.drop_index("ix_execution_files_group", table_name="execution_files")
    op.drop_table("execution_files")
    op.drop_index("ix_execution_records_group", table_name="execution_records")
    op.drop_index("uq_execution_records_done", table_name="execution_records")
    op.drop_table("execution_records")
