"""The confirmed-todo purge switch allows DELETE only (SECURITY.md 6-3)

Revision ID: 0017
Revises: 0016

0012 added `moim.lifecycle_purge` so account and group deletion can remove confirmed todos. It is a plain session
setting, so the app role (and therefore an SQL injection that can run several statements) can turn it on, and the
0012/0016 trigger then let an UPDATE through as well: a hidden criterion could be rewritten in place. Both callers
(`LifecycleService._purge_group`, `_delete_own_data`) only ever DELETE, so the switch now opens DELETE and nothing
else. An UPDATE with the switch on is refused like any other (only `stale_at` may change). Signatures (0016) still
catch a deleted todo inside a confirmation; a whole deleted confirmation is the audit hash chain's job (next).
Downgrade restores the 0016 body.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_TAIL = """
          if tg_table_name = 'todo_confirmations' and tg_op = 'UPDATE'
             and (new.group_id, new.user_id, new.source_assignment_revision, new.revision, new.draft_revision,
                  new.member_state_revision, new.action_id, new.created_at, new.signature, new.signing_kid)
                 is not distinct from
                 (old.group_id, old.user_id, old.source_assignment_revision, old.revision, old.draft_revision,
                  old.member_state_revision, old.action_id, old.created_at, old.signature, old.signing_kid) then
            return new;  -- only stale_at may change
          end if;
          raise exception 'confirmed todo revisions are immutable (%)', tg_table_name;
        end
"""

_BODY = """
        begin
          if current_setting('moim.lifecycle_purge', true) = 'on' and tg_op = 'DELETE' then
            return old;  -- account deletion / group deletion only, and only ever a DELETE
          end if;""" + _TAIL

_BODY_0016 = """
        begin
          if current_setting('moim.lifecycle_purge', true) = 'on' then
            return case when tg_op = 'DELETE' then old else new end;  -- account deletion / group deletion only
          end if;""" + _TAIL


def upgrade() -> None:
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_BODY}$$;")


def downgrade() -> None:
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_BODY_0016}$$;")
