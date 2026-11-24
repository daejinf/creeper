"""Ed25519 signatures on confirmed todo revisions (SECURITY.md 6)

Revision ID: 0016
Revises: 0015

`todo_confirmations` gets `signature` (64 bytes) and `signing_kid` (16 hex characters of the public key's SHA-256),
written in the same INSERT as the confirmation. Both null = confirmed before this migration (UNSIGNED, never signed
afterwards: signing contents that may already have changed would be a false guarantee). A check with the migration
time as cutoff makes every later row carry one. An unsigned confirmation is never eligible for an AI PASS completion. The immutability trigger
now also freezes these two columns; everything else about it (0012 purge switch, only `stale_at` may change) stays.
Downgrade restores the 0012 trigger body and drops the columns (signatures are lost).
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_BODY = """
        begin
          if current_setting('moim.lifecycle_purge', true) = 'on' then
            return case when tg_op = 'DELETE' then old else new end;  -- account deletion / group deletion only
          end if;
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

_BODY_0012 = """
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


def upgrade() -> None:
    op.add_column("todo_confirmations", sa.Column("signature", sa.LargeBinary(), nullable=True))
    op.add_column("todo_confirmations", sa.Column("signing_kid", sa.String(16), nullable=True))
    op.create_check_constraint(
        op.f("ck_todo_confirmations_signature"), "todo_confirmations",
        "(signature is null) = (signing_kid is null) and (signature is null or octet_length(signature) = 64)",
    )
    # Every confirmation written from now on must carry a signature. Rows confirmed before this migration (created_at
    # before the cutoff recorded here) may stay unsigned; they can still be marked STALE. Defence in depth only: the
    # app never lets an unsigned confirmation complete a todo with an AI PASS, whatever its created_at says.
    cutoff = op.get_bind().execute(sa.text("select now()")).scalar_one().isoformat()
    op.execute("alter table todo_confirmations add constraint ck_todo_confirmations_signed"
               f" check (signature is not null or created_at < timestamptz '{cutoff}')")
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_BODY}$$;")


def downgrade() -> None:
    op.execute(f"create or replace function moim_todo_confirmed_immutable() returns trigger language plpgsql as $${_BODY_0012}$$;")
    op.drop_constraint(op.f("ck_todo_confirmations_signed"), "todo_confirmations", type_="check")
    op.drop_constraint(op.f("ck_todo_confirmations_signature"), "todo_confirmations", type_="check")
    op.drop_column("todo_confirmations", "signing_kid")
    op.drop_column("todo_confirmations", "signature")
