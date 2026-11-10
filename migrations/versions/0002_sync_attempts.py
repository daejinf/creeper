"""Persist sync failure status independently from immutable goal snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0002_sync_attempts'
down_revision = '0001_goal_storage'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('sync_attempts',
        sa.Column('record_id',sa.Text,primary_key=True),
        sa.Column('status',sa.Text,nullable=False),
        sa.Column('error_code',sa.Text),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('SAVED','FAILED')"),schema='wemeet_goal')

def downgrade():
    raise RuntimeError('Back up diagnostics before explicit schema rollback')
