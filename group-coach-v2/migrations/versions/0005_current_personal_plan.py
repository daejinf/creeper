"""Explicit personal version pointer, recovered from immutable execution events."""
from alembic import op
revision='0005_current_personal_plan'
down_revision='0004_task_provenance'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('ALTER TABLE wemeet_coach.private_states ADD COLUMN IF NOT EXISTS current_plan_id TEXT')
    op.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='uq_owned_plan' AND connamespace='wemeet_coach'::regnamespace) THEN ALTER TABLE wemeet_coach.plans ADD CONSTRAINT uq_owned_plan UNIQUE(group_id,id,owner); END IF; END $$")
    op.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_current_personal_plan' AND connamespace='wemeet_coach'::regnamespace) THEN ALTER TABLE wemeet_coach.private_states ADD CONSTRAINT fk_current_personal_plan FOREIGN KEY(group_id,current_plan_id,owner) REFERENCES wemeet_coach.plans(group_id,id,owner); END IF; END $$")
    # Do not guess from names or timestamps. Only an explicit saved plan/edit event
    # with matching current conditions can populate a legacy pointer.
    op.execute('''WITH candidates AS (
      SELECT p.group_id,p.owner,p.id,max(e.seq) AS event_seq
      FROM wemeet_coach.plans p
      JOIN wemeet_coach.private_states s ON s.group_id=p.group_id AND s.owner=p.owner
      JOIN wemeet_coach.personal_goals mg ON mg.id=p.personal_goal_id
      JOIN wemeet_coach.goals g ON g.id=p.goal_id
      JOIN wemeet_coach.rooms r ON r.id=p.group_id AND r.approved_proposal_id=g.proposal_id
      JOIN wemeet_coach.events e ON e.group_id=p.group_id AND e.owner=p.owner AND
        ((e.kind='PLAN_EDITED' AND e.body->>'plan_version_id'=p.id) OR
         (e.kind='AI_REPLY' AND EXISTS(SELECT 1 FROM wemeet_coach.operations o
           WHERE o.group_id=p.group_id AND o.owner=p.owner AND o.state='COMPLETE'
             AND o.result->>'status'='OK' AND o.result->'data'->'plan'->>'plan_version_id'=p.id
             AND o.result->'meta'->>'run_id'=e.body->>'run_id')))
      WHERE mg.private_conditions->>'_source_private_revision'=s.revision::text
      GROUP BY p.group_id,p.owner,p.id
    ), ranked AS (SELECT *,row_number() OVER(PARTITION BY group_id,owner ORDER BY event_seq DESC) AS rank FROM candidates)
    UPDATE wemeet_coach.private_states s SET current_plan_id=x.id FROM ranked x
      WHERE x.rank=1 AND s.group_id=x.group_id AND s.owner=x.owner AND s.current_plan_id IS NULL''')

def downgrade():raise RuntimeError('Explicit restore required; current pointers are not dropped automatically')
