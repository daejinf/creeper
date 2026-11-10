"""Independent group coach schema. Never touches the original database."""
from alembic import op
from wemeet_coach.persistence.schema import metadata
revision='0001_coach'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    op.execute('CREATE SCHEMA IF NOT EXISTS wemeet_coach')
    metadata.create_all(op.get_bind())
    op.execute('''CREATE FUNCTION wemeet_coach.immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable snapshot'; END $$''')
    for table in ['events','vote_audit','goals','personal_goals','plans','tasks','baselines']:
        op.execute(f'CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_coach.{table} FOR EACH ROW EXECUTE FUNCTION wemeet_coach.immutable()')
    op.execute('''CREATE FUNCTION wemeet_coach.vote_member() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM wemeet_coach.proposals p, jsonb_array_elements(p.electorate) m WHERE p.id=NEW.proposal_id AND m->>'member_id'=NEW.member_id AND p.state='VOTING') THEN RAISE EXCEPTION 'not an eligible voter'; END IF;
      RETURN NEW; END $$''')
    op.execute('CREATE TRIGGER eligible_voter BEFORE INSERT OR UPDATE ON wemeet_coach.votes FOR EACH ROW EXECUTE FUNCTION wemeet_coach.vote_member()')
    op.execute('''CREATE FUNCTION wemeet_coach.unanimous() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.state='APPROVED' AND OLD.state<>NEW.state THEN
        IF jsonb_array_length(NEW.electorate)=0 OR (SELECT count(*) FROM wemeet_coach.votes WHERE proposal_id=NEW.id AND agree)=0 OR (SELECT count(*) FROM wemeet_coach.votes WHERE proposal_id=NEW.id AND agree)<>jsonb_array_length(NEW.electorate) THEN RAISE EXCEPTION 'unanimity required'; END IF;
      END IF; RETURN NEW; END $$''')
    op.execute('CREATE TRIGGER unanimous_approval BEFORE UPDATE ON wemeet_coach.proposals FOR EACH ROW EXECUTE FUNCTION wemeet_coach.unanimous()')
    op.execute('''CREATE FUNCTION wemeet_coach.check_task() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE p RECORD; g RECORD; BEGIN
      SELECT * INTO p FROM wemeet_coach.plans WHERE id=NEW.plan_id AND group_id=NEW.group_id;
      SELECT * INTO g FROM wemeet_coach.goals WHERE id=p.goal_id;
      IF p.owner IS NOT NULL AND NEW.owner IS DISTINCT FROM p.owner THEN RAISE EXCEPTION 'task owner mismatch'; END IF;
      IF (NEW.body->>'due_date')::date < (g.body->>'start_date')::date OR (NEW.body->>'due_date')::date > (g.body->>'end_date')::date THEN RAISE EXCEPTION 'task outside goal period'; END IF;
      RETURN NEW; END $$''')
    op.execute('CREATE TRIGGER task_scope BEFORE INSERT ON wemeet_coach.tasks FOR EACH ROW EXECUTE FUNCTION wemeet_coach.check_task()')
    op.execute('''CREATE FUNCTION wemeet_coach.check_baseline() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM wemeet_coach.plans p JOIN wemeet_coach.goals g ON g.id=p.goal_id JOIN wemeet_coach.rooms r ON r.id=p.group_id WHERE p.id=NEW.plan_id AND p.group_id=NEW.group_id AND p.owner=NEW.owner AND r.approved_proposal_id=g.proposal_id) THEN RAISE EXCEPTION 'current owner baseline required'; END IF;
      RETURN NEW; END $$''')
    op.execute('CREATE TRIGGER baseline_scope BEFORE INSERT ON wemeet_coach.baselines FOR EACH ROW EXECUTE FUNCTION wemeet_coach.check_baseline()')
def downgrade():raise RuntimeError('Destructive downgrade disabled; restore an explicit backup instead')
