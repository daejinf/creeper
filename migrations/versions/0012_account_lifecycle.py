"""stage 6: account lifecycle (D-D1 D6/D7): deleted-user tombstones and a scoped purge switch for confirmed todos

Revision ID: 0012
Revises: 0011

- `users.deleted_at`, and `username` may be NULL, but only on a deleted-user tombstone (D7: the username is released at
  once). A tombstone is a fresh row with a new id ("탈퇴한 사용자"); the deleted account's own row is removed, so
  nothing kept for the team (the immutable assignment record, cohort history, audit metadata) points at the old id.
- `role_interview_snapshots.purged_at`: when a member leaves a group (D6) the private interview content of the
  snapshot is erased (`'{}'`) and this is set. The row stays only as the key their confirmed todos hang off.
- `lifecycle_events`: the audit log of leaving, leader hand-off, group deletion, account deletion, password and profile
  changes. Metadata only (kind, user id, group id, a short code); no foreign keys, so it outlives a deleted group.
- The immutability trigger of confirmed todos (0009) now lets UPDATE/DELETE through only when the transaction set
  `moim.lifecycle_purge = 'on'` (`SET LOCAL`), which only the stage 6 lifecycle service does, for account deletion and
  for deleting a group whose last member left. Everything else is refused exactly as before.

Downgrade restores the 0009 trigger and drops the columns. It refuses to run while a tombstone (NULL username) exists,
because the old NOT NULL constraint cannot hold then; removing tombstones would edit kept team records.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_TRIGGER_BODY = """
        begin
          if current_setting('moim.lifecycle_purge', true) = 'on' then
            return case when tg_op = 'DELETE' then old else new end;  -- account deletion / group deletion only
          end if;
          if tg_table_name = 'todo_confirmations' and tg_op = 'UPDATE'
             and (new.group_id, new.user_id, new.source_assignment_revision, new.revision, new.draft_revision,
                  new.member_state_revision, new.action_id, new.created_at)
                 is not distinct from
                 (old.group_id, old.user_id, old.source_assignment_revision, old.revision, old.draft_revision,
                  old.member_state_revision, old.action_id, old.created_at) then
            return new;  -- only stale_at may change
          end if;
          raise exception 'confirmed todo revisions are immutable (%)', tg_table_name;
        end
"""

_OLD_TRIGGER_BODY = """
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
        end
"""


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("users", "username", existing_type=sa.String(16), nullable=True)
    op.drop_constraint(op.f("ck_users_username"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_username"), "users", "username is null or username ~ '^[a-z0-9]{4,16}$'")
    op.create_check_constraint(op.f("ck_users_tombstone"), "users", "(username is null) = (deleted_at is not null)")
    op.add_column("role_interview_snapshots", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_TRIGGER_BODY}$$;")
    op.create_table(
        "lifecycle_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("group_id", sa.String(36), nullable=True),
        sa.Column("detail", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind in ('LEAVE','LEADER_HANDOFF','GROUP_DELETED','ACCOUNT_DELETED','PASSWORD_CHANGED','PROFILE_UPDATED')",
            name=op.f("ck_lifecycle_events_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_events")),
    )
    op.create_index("ix_lifecycle_events_created", "lifecycle_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("select count(*) from users where username is null")).scalar():
        raise RuntimeError("deleted-user tombstones exist; 0012 cannot be downgraded without editing kept team records")
    op.drop_index("ix_lifecycle_events_created", table_name="lifecycle_events")
    op.drop_table("lifecycle_events")
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_OLD_TRIGGER_BODY}$$;")
    op.drop_column("role_interview_snapshots", "purged_at")
    op.drop_constraint(op.f("ck_users_tombstone"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_username"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_username"), "users", "username ~ '^[a-z0-9]{4,16}$'")
    op.alter_column("users", "username", existing_type=sa.String(16), nullable=False)
    op.drop_column("users", "deleted_at")
