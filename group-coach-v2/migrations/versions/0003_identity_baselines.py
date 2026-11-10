"""Stable SW identity, scoped immutable baselines and relationship constraints."""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4
revision='0003_identity_baselines'
down_revision='0002_reference_date'
branch_labels=None
depends_on=None

def constraint(table,name,definition):
    op.execute(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='{name}' AND connamespace='wemeet_coach'::regnamespace) THEN ALTER TABLE wemeet_coach.{table} ADD CONSTRAINT {name} {definition}; END IF; END $$")

def upgrade():
    c=op.get_bind()
    checks={
      'current proposal':"SELECT count(*) FROM wemeet_coach.rooms r LEFT JOIN wemeet_coach.proposals p ON p.id=r.approved_proposal_id AND p.group_id=r.id WHERE r.approved_proposal_id IS NOT NULL AND p.id IS NULL",
      'private proposal':"SELECT count(*) FROM wemeet_coach.private_states s LEFT JOIN wemeet_coach.proposals p ON p.id=s.proposal_id AND p.group_id=s.group_id WHERE s.proposal_id IS NOT NULL AND p.id IS NULL",
      'plan owner':"SELECT count(*) FROM wemeet_coach.plans WHERE (owner IS NULL)<>(personal_goal_id IS NULL)"}
    for label,query in checks.items():
        if c.scalar(sa.text(query)):raise RuntimeError('Migration held for relationship review: '+label)
    op.execute('CREATE TABLE IF NOT EXISTS wemeet_coach.users(external_user_id TEXT PRIMARY KEY,created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
    op.execute('ALTER TABLE wemeet_coach.members ADD COLUMN IF NOT EXISTS external_user_id TEXT REFERENCES wemeet_coach.users(external_user_id)')
    constraint('members','uq_member_external_user','UNIQUE(group_id,external_user_id)')
    constraint('rooms','fk_current_proposal','FOREIGN KEY(id,approved_proposal_id) REFERENCES wemeet_coach.proposals(group_id,id) DEFERRABLE INITIALLY DEFERRED')
    constraint('private_states','fk_private_proposal','FOREIGN KEY(group_id,proposal_id) REFERENCES wemeet_coach.proposals(group_id,id)')
    constraint('plans','fk_plan_owner','FOREIGN KEY(group_id,owner) REFERENCES wemeet_coach.members(group_id,id)')
    constraint('plans','ck_plan_scope','CHECK((owner IS NULL AND personal_goal_id IS NULL) OR (owner IS NOT NULL AND personal_goal_id IS NOT NULL))')
    op.execute("ALTER TABLE wemeet_coach.baselines ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'PERSONAL'")
    op.execute('ALTER TABLE wemeet_coach.baselines ALTER COLUMN owner DROP NOT NULL')
    constraint('baselines','ck_baseline_scope',"CHECK((scope='GROUP' AND owner IS NULL) OR (scope='PERSONAL' AND owner IS NOT NULL))")
    op.execute('''CREATE OR REPLACE FUNCTION wemeet_coach.check_baseline() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM wemeet_coach.plans p JOIN wemeet_coach.goals g ON g.id=p.goal_id JOIN wemeet_coach.rooms r ON r.id=p.group_id
        WHERE p.id=NEW.plan_id AND p.group_id=NEW.group_id AND p.owner IS NOT DISTINCT FROM NEW.owner AND r.approved_proposal_id=g.proposal_id
        AND ((NEW.scope='GROUP' AND p.owner IS NULL) OR (NEW.scope='PERSONAL' AND p.owner IS NOT NULL))) THEN RAISE EXCEPTION 'current scoped baseline required'; END IF; RETURN NEW; END $$''')
    rows=c.execute(sa.text('''SELECT p.id,p.group_id,p.body FROM wemeet_coach.plans p JOIN wemeet_coach.goals g ON g.id=p.goal_id
      JOIN wemeet_coach.rooms r ON r.id=p.group_id AND r.approved_proposal_id=g.proposal_id
      WHERE p.owner IS NULL AND NOT EXISTS(SELECT 1 FROM wemeet_coach.baselines b WHERE b.plan_id=p.id)''')).mappings().all()
    import json
    for row in rows:
        c.execute(sa.text("INSERT INTO wemeet_coach.baselines(id,group_id,plan_id,owner,scope,public_summary) VALUES(:id,:gid,:pid,NULL,'GROUP',CAST(:body AS jsonb))"),{'id':str(uuid4()),'gid':row['group_id'],'pid':row['id'],'body':json.dumps(row['body'],ensure_ascii=False)})
    op.execute('GRANT SELECT,INSERT ON wemeet_coach.users TO wemeet_coach_app')
    op.execute('GRANT UPDATE(external_user_id) ON wemeet_coach.members TO wemeet_coach_app')

def downgrade():raise RuntimeError('Restore an explicit backup; identity/history must not be dropped')
