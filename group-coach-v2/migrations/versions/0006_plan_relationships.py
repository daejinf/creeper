"""Keep personal and shared goal references identical; no data rewriting."""

from alembic import op
import sqlalchemy as sa

revision = "0006_plan_relationships"
down_revision = "0005_current_personal_plan"
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    mismatch = c.scalar(
        sa.text(
            "SELECT count(*) FROM wemeet_coach.plans p JOIN wemeet_coach.personal_goals g ON g.id=p.personal_goal_id WHERE p.goal_id<>g.goal_id OR p.owner<>g.owner OR p.group_id<>g.group_id"
        )
    )
    duplicate = c.scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT group_id,goal_id FROM wemeet_coach.plans WHERE owner IS NULL GROUP BY group_id,goal_id HAVING count(*)>1)x"
        )
    )
    if mismatch or duplicate:
        raise RuntimeError(
            "Plan relationships require review; no historical rows were modified"
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_goal_plan ON wemeet_coach.plans(group_id,goal_id) WHERE owner IS NULL"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION wemeet_coach.check_personal_plan_goal() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.owner IS NOT NULL AND NOT EXISTS(SELECT 1 FROM wemeet_coach.personal_goals g
        WHERE g.id=NEW.personal_goal_id AND g.group_id=NEW.group_id AND g.owner=NEW.owner AND g.goal_id=NEW.goal_id)
      THEN RAISE EXCEPTION 'personal plan must reference its own shared goal'; END IF;
      IF NEW.owner IS NOT NULL AND NEW.id IS DISTINCT FROM NEW.body->>'plan_version_id'
      THEN RAISE EXCEPTION 'personal plan body version mismatch'; END IF;
      RETURN NEW; END $$"""
    )
    op.execute(
        "CREATE TRIGGER personal_plan_goal BEFORE INSERT ON wemeet_coach.plans FOR EACH ROW EXECUTE FUNCTION wemeet_coach.check_personal_plan_goal()"
    )


def downgrade():
    raise RuntimeError("Restore an explicit backup; immutable goals are not rewritten")
