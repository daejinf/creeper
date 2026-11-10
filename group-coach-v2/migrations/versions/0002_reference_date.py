from alembic import op
revision='0002_reference_date'
down_revision='0001_coach'
branch_labels=None
depends_on=None
def upgrade():
    # Initial schema uses current metadata on a new installation; existing
    # isolated installations gain the same fields without touching old DBs.
    op.execute('ALTER TABLE wemeet_coach.rooms ADD COLUMN IF NOT EXISTS reference_date DATE NOT NULL DEFAULT CURRENT_DATE')
    op.execute("ALTER TABLE wemeet_coach.rooms ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Seoul'")
def downgrade():raise RuntimeError('Restore an explicit backup instead')
