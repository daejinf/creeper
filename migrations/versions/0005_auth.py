"""Auth tables (users, auth_sessions) in their own schema. Additive only; frozen DDL."""
from alembic import op

revision = "0005_auth"
down_revision = "0004_private_owner_fk"
branch_labels = None
depends_on = None

DDL = [
    """CREATE SCHEMA wemeet_auth""",
    """CREATE TABLE wemeet_auth.users (
	user_id UUID NOT NULL,
	username TEXT NOT NULL,
	password_hash TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	last_login_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (user_id),
	CONSTRAINT uq_auth_users_username UNIQUE (username),
	CONSTRAINT ck_auth_users_username CHECK (username ~ '^[a-z0-9_]{3,20}$')
)""",
    """CREATE TABLE wemeet_auth.auth_sessions (
	session_hash TEXT NOT NULL,
	user_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (session_hash),
	CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id) REFERENCES wemeet_auth.users (user_id) ON DELETE CASCADE,
	CONSTRAINT ck_auth_sessions_hash CHECK (session_hash ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_auth_sessions_expiry CHECK (expires_at > created_at)
)""",
    """CREATE INDEX ix_auth_sessions_user_id ON wemeet_auth.auth_sessions (user_id)""",
    """CREATE INDEX ix_auth_sessions_expires_at ON wemeet_auth.auth_sessions (expires_at)""",
    # Conditional grant: only when the app role already exists in this cluster.
    # Roles are never created here. Least privilege: no DDL, no UPDATE except last_login_at.
    """DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wemeet_app') THEN
    GRANT USAGE ON SCHEMA wemeet_auth TO wemeet_app;
    GRANT SELECT, INSERT ON wemeet_auth.users TO wemeet_app;
    GRANT UPDATE (last_login_at) ON wemeet_auth.users TO wemeet_app;
    GRANT SELECT, INSERT, DELETE ON wemeet_auth.auth_sessions TO wemeet_app;
  END IF;
END $$""",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Back up wemeet_auth before an explicit schema rollback.")
