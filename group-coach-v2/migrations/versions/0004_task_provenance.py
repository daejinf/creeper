"""Column/JSON task identity parity and scoped baseline completion."""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4
import json
revision='0004_task_provenance'
down_revision='0003_identity_baselines'
branch_labels=None
depends_on=None

def upgrade():
    c=op.get_bind()
    invalid=c.scalar(sa.text("SELECT count(*) FROM wemeet_coach.tasks WHERE owner IS DISTINCT FROM body->>'owner_member_id' OR id IS DISTINCT FROM body->>'task_id'"))
    if invalid:raise RuntimeError('Task identity mismatch requires explicit review; rows were not rewritten')
    op.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='ck_task_body_identity' AND connamespace='wemeet_coach'::regnamespace) THEN ALTER TABLE wemeet_coach.tasks ADD CONSTRAINT ck_task_body_identity CHECK(id IS NOT DISTINCT FROM body->>'task_id' AND owner IS NOT DISTINCT FROM body->>'owner_member_id'); END IF; END $$")
    rows=c.execute(sa.text("SELECT p.id,p.group_id,p.body FROM wemeet_coach.plans p JOIN wemeet_coach.goals g ON g.id=p.goal_id JOIN wemeet_coach.rooms r ON r.id=p.group_id AND r.approved_proposal_id=g.proposal_id WHERE p.owner IS NULL AND NOT EXISTS(SELECT 1 FROM wemeet_coach.baselines b WHERE b.plan_id=p.id)")).mappings().all()
    for row in rows:c.execute(sa.text("INSERT INTO wemeet_coach.baselines(id,group_id,plan_id,owner,scope,public_summary) VALUES(:id,:gid,:pid,NULL,'GROUP',CAST(:body AS jsonb))"),{'id':str(uuid4()),'gid':row['group_id'],'pid':row['id'],'body':json.dumps(row['body'],ensure_ascii=False)})

def downgrade():raise RuntimeError('Restore an explicit backup instead')
