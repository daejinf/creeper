"""stage 4 session B: todo draft, AI steps, direct-edit actions, immutable confirmation revisions

Revision ID: 0009
Revises: 0008

Only new tables. Every draft row is linked to its `source_assignment_revision` through the interview snapshot and the
team coverage of that revision, so session C can mark them STALE. Confirmations and their items are immutable: a
trigger refuses DELETE, and UPDATE except `todo_confirmations.stale_at`. Downgrade drops only what is created here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

NOW = sa.func.now()
KEY = ["group_id", "user_id", "source_assignment_revision"]
REF = ["todo_drafts.group_id", "todo_drafts.user_id", "todo_drafts.source_assignment_revision"]


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _key():
    return [
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "todo_drafts",
        *_key(),
        sa.Column("member_state_revision", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_item_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("mode", sa.String(10), server_default="EDITING", nullable=False),
        sa.Column("confirmed_revision", sa.Integer(), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint(*KEY, name=op.f("pk_todo_drafts")),
        sa.ForeignKeyConstraint(
            KEY,
            ["role_interview_snapshots.group_id", "role_interview_snapshots.user_id",
             "role_interview_snapshots.source_assignment_revision"],
            name=op.f("fk_todo_drafts_group_id_role_interview_snapshots"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "source_assignment_revision"],
            ["team_coverages.group_id", "team_coverages.source_assignment_revision"],
            name=op.f("fk_todo_drafts_group_id_team_coverages"),
        ),
        sa.CheckConstraint("draft_revision >= 0", name=op.f("ck_todo_drafts_draft_revision")),
        sa.CheckConstraint("mode in ('EDITING','CONFIRMED')", name=op.f("ck_todo_drafts_mode")),
        sa.CheckConstraint("mode = 'EDITING' or confirmed_revision is not null", name=op.f("ck_todo_drafts_confirmed_has_revision")),
    )
    op.create_table(
        "todo_items",
        sa.Column("task_id", sa.String(36), nullable=False),
        *_key(),
        sa.Column("item_no", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("part_id", sa.String(8), nullable=True),
        sa.Column("phase", sa.String(10), nullable=True),
        sa.Column("milestone_id", sa.String(8), nullable=True),
        sa.Column("deliverable", sa.Text(), nullable=True),
        sa.Column("completion_criteria", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_types", postgresql.JSONB(), nullable=True),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("criteria_for_title", sa.String(60), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_todo_items")),
        sa.ForeignKeyConstraint(KEY, REF, name=op.f("fk_todo_items_group_id_todo_drafts")),
        sa.UniqueConstraint(*KEY, "item_no", name="uq_todo_items_item_no"),
        sa.CheckConstraint("char_length(title) between 1 and 60", name=op.f("ck_todo_items_title")),
        sa.CheckConstraint("origin in ('AI','USER')", name=op.f("ck_todo_items_origin")),
        sa.CheckConstraint(
            "(deliverable is null) = (completion_criteria is null) and (deliverable is null) = (evidence_types is null)"
            " and (deliverable is null) = (criteria_for_title is null) and (deliverable is null) = (estimated_minutes is null)",
            name=op.f("ck_todo_items_criteria_together"),
        ),
    )
    op.create_table(
        "todo_ai_steps",
        *_key(),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("base_draft_revision", sa.Integer(), nullable=True),
        sa.Column("result_draft_revision", sa.Integer(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint(*KEY, "seq", name=op.f("pk_todo_ai_steps")),
        sa.ForeignKeyConstraint(KEY, REF, name=op.f("fk_todo_ai_steps_group_id_todo_drafts")),
        sa.UniqueConstraint("group_id", "user_id", "action_id", name="uq_todo_ai_steps_action"),
        sa.CheckConstraint("kind in ('DRAFT','REVISE','CRITERIA')", name=op.f("ck_todo_ai_steps_kind")),
        sa.CheckConstraint("state in ('PENDING','RUNNING','SUCCEEDED','FAILED')", name=op.f("ck_todo_ai_steps_state")),
        sa.CheckConstraint("(kind = 'REVISE') = (user_message is not null)", name=op.f("ck_todo_ai_steps_message_iff_revise")),
        sa.CheckConstraint(
            "user_message is null or char_length(user_message) between 1 and 300", name=op.f("ck_todo_ai_steps_message_length")
        ),
    )
    op.create_index(
        "uq_todo_ai_steps_running", "todo_ai_steps", KEY, unique=True,
        postgresql_where=sa.text("state in ('PENDING','RUNNING')"),
    )
    op.create_table(
        "todo_actions",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("source_assignment_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("group_id", "user_id", "action_id", name=op.f("pk_todo_actions")),
        sa.ForeignKeyConstraint(KEY, REF, name=op.f("fk_todo_actions_group_id_todo_drafts")),
    )
    op.create_table(
        "todo_confirmations",
        *_key(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("member_state_revision", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint(*KEY, "revision", name=op.f("pk_todo_confirmations")),
        sa.ForeignKeyConstraint(KEY, REF, name=op.f("fk_todo_confirmations_group_id_todo_drafts")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_todo_confirmations_revision")),
    )
    op.create_table(
        "todo_confirmed_items",
        *_key(),
        sa.Column("confirmation_revision", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("part_id", sa.String(8), nullable=False),
        sa.Column("deliverable", sa.Text(), nullable=False),
        sa.Column("completion_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_types", postgresql.JSONB(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(*KEY, "confirmation_revision", "task_id", name=op.f("pk_todo_confirmed_items")),
        sa.ForeignKeyConstraint(
            [*KEY, "confirmation_revision"],
            ["todo_confirmations.group_id", "todo_confirmations.user_id", "todo_confirmations.source_assignment_revision",
             "todo_confirmations.revision"],
            name=op.f("fk_todo_confirmed_items_group_id_todo_confirmations"),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["todo_items.task_id"], name=op.f("fk_todo_confirmed_items_task_id_todo_items")),
    )
    op.execute(
        """
        create function moim_todo_confirmed_immutable() returns trigger language plpgsql as $$
        begin
          if tg_table_name = 'todo_confirmations' and tg_op = 'UPDATE'
             and (new.group_id, new.user_id, new.source_assignment_revision, new.revision, new.draft_revision,
                  new.member_state_revision, new.action_id, new.created_at)
                 is not distinct from
                 (old.group_id, old.user_id, old.source_assignment_revision, old.revision, old.draft_revision,
                  old.member_state_revision, old.action_id, old.created_at) then
            return new;  -- only stale_at may change
          end if;
          raise exception 'confirmed todo revisions are immutable (%)', tg_table_name;
        end $$;
        """
    )
    op.execute(
        "create trigger trg_todo_confirmations_immutable before update or delete on todo_confirmations"
        " for each row execute function moim_todo_confirmed_immutable()"
    )
    op.execute(
        "create trigger trg_todo_confirmed_items_immutable before update or delete on todo_confirmed_items"
        " for each row execute function moim_todo_confirmed_immutable()"
    )


def downgrade() -> None:
    op.execute("drop trigger if exists trg_todo_confirmed_items_immutable on todo_confirmed_items")
    op.execute("drop trigger if exists trg_todo_confirmations_immutable on todo_confirmations")
    op.execute("drop function if exists moim_todo_confirmed_immutable()")
    op.drop_table("todo_confirmed_items")
    op.drop_table("todo_confirmations")
    op.drop_table("todo_actions")
    op.drop_index("uq_todo_ai_steps_running", table_name="todo_ai_steps")
    op.drop_table("todo_ai_steps")
    op.drop_table("todo_items")
    op.drop_table("todo_drafts")
