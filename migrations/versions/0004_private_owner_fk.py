"""Enforce private journal ownership in PostgreSQL, not only in Python."""
from alembic import op
revision='0004_private_owner_fk'
down_revision='0003_unified_journal'
branch_labels=None
depends_on=None
TABLES=('conversation_messages','module_inputs','execution_artifacts','module_operations')
def upgrade():
    for table in TABLES:
        op.execute(f"ALTER TABLE wemeet_goal.{table} ADD COLUMN private_owner_id TEXT GENERATED ALWAYS AS (NULLIF(owner,'')) STORED")
        op.execute(f'ALTER TABLE wemeet_goal.{table} ADD CONSTRAINT fk_{table}_owner FOREIGN KEY(record_id,private_owner_id) REFERENCES wemeet_goal.members(group_id,member_id)')
def downgrade():
    for table in reversed(TABLES):op.execute(f'ALTER TABLE wemeet_goal.{table} DROP COLUMN private_owner_id')
