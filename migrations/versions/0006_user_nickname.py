"""Add users.nickname (display name; duplicates allowed). Additive; existing rows backfilled."""
from alembic import op

revision = "0006_user_nickname"
down_revision = "0005_auth"
branch_labels = None
depends_on = None

DDL = [
    """ALTER TABLE wemeet_auth.users ADD COLUMN nickname TEXT""",
    # Existing users: nickname = username, cut to the first 10 chars (username is 3-20 chars).
    """UPDATE wemeet_auth.users SET nickname = left(username, 10)""",
    """ALTER TABLE wemeet_auth.users ALTER COLUMN nickname SET NOT NULL""",
    # Length/trim only. The allowed-character rule lives in the app so backfilled values
    # (e.g. a username made only of underscores) can never make this migration fail.
    """ALTER TABLE wemeet_auth.users ADD CONSTRAINT ck_auth_users_nickname
	CHECK (char_length(nickname) BETWEEN 2 AND 10 AND nickname = btrim(nickname))""",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Back up wemeet_auth.users before an explicit rollback.")
