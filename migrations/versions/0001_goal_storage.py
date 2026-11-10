"""Initial immutable goal/plan schema. Frozen DDL; no runtime model imports."""
from alembic import op
revision = "0001_goal_storage"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    """CREATE SCHEMA IF NOT EXISTS wemeet_goal""",
    """CREATE TABLE wemeet_goal.groups (
	group_id TEXT NOT NULL, 
	title TEXT NOT NULL, 
	PRIMARY KEY (group_id)
)""",
    """CREATE TABLE wemeet_goal.members (
	group_id TEXT NOT NULL, 
	member_id TEXT NOT NULL, 
	nickname TEXT NOT NULL, 
	PRIMARY KEY (group_id, member_id), 
	FOREIGN KEY(group_id) REFERENCES wemeet_goal.groups (group_id)
)""",
    """CREATE TABLE wemeet_goal.shared_goals (
	goal_version_id TEXT NOT NULL, 
	group_id TEXT NOT NULL, 
	source_ref TEXT NOT NULL, 
	title TEXT NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	completion_criteria JSONB NOT NULL, 
	verification_method TEXT NOT NULL, 
	privacy_scope TEXT NOT NULL, 
	conditions JSONB NOT NULL, 
	content_hash TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (goal_version_id), 
	FOREIGN KEY(group_id) REFERENCES wemeet_goal.groups (group_id), 
	UNIQUE (group_id, goal_version_id), 
	CHECK (end_date > start_date), 
	CHECK (privacy_scope IN ('OBJECTIVE_ONLY','PRIVATE','CUSTOM')), 
	UNIQUE (source_ref)
)""",
    """CREATE TABLE wemeet_goal.member_goals (
	member_goal_version_id TEXT NOT NULL, 
	group_id TEXT NOT NULL, 
	member_id TEXT NOT NULL, 
	shared_goal_version_id TEXT NOT NULL, 
	source_ref TEXT NOT NULL, 
	title TEXT NOT NULL, 
	deliverable TEXT NOT NULL, 
	completion_criteria JSONB NOT NULL, 
	content_hash TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (member_goal_version_id), 
	FOREIGN KEY(group_id, member_id) REFERENCES wemeet_goal.members (group_id, member_id), 
	FOREIGN KEY(group_id, shared_goal_version_id) REFERENCES wemeet_goal.shared_goals (group_id, goal_version_id), 
	UNIQUE (group_id, member_goal_version_id, member_id), 
	UNIQUE (group_id, member_goal_version_id, member_id, shared_goal_version_id), 
	UNIQUE (source_ref)
)""",
    """CREATE TABLE wemeet_goal.record_heads (
	group_id TEXT NOT NULL, 
	source_revision INTEGER NOT NULL, 
	current_shared_goal_version_id TEXT, 
	current_member_goals JSONB NOT NULL, 
	current_plans JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (group_id), 
	FOREIGN KEY(group_id, current_shared_goal_version_id) REFERENCES wemeet_goal.shared_goals (group_id, goal_version_id)
)""",
    """CREATE TABLE wemeet_goal.plan_versions (
	plan_version_id TEXT NOT NULL, 
	group_id TEXT NOT NULL, 
	shared_goal_version_id TEXT NOT NULL, 
	member_goal_version_id TEXT, 
	owner_member_id TEXT, 
	scope TEXT NOT NULL, 
	previous_plan_version_id TEXT, 
	source_ref TEXT NOT NULL, 
	title TEXT NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	status TEXT DEFAULT 'DRAFT' NOT NULL, 
	weekly_outcomes JSONB NOT NULL, 
	adaptation_rules JSONB NOT NULL, 
	suggestions JSONB NOT NULL, 
	validation JSONB NOT NULL, 
	content_hash TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (plan_version_id), 
	FOREIGN KEY(group_id, shared_goal_version_id) REFERENCES wemeet_goal.shared_goals (group_id, goal_version_id), 
	FOREIGN KEY(group_id, member_goal_version_id, owner_member_id, shared_goal_version_id) REFERENCES wemeet_goal.member_goals (group_id, member_goal_version_id, member_id, shared_goal_version_id), 
	UNIQUE (group_id, plan_version_id), 
	FOREIGN KEY(group_id, previous_plan_version_id) REFERENCES wemeet_goal.plan_versions (group_id, plan_version_id), 
	CHECK ((scope='GROUP' AND owner_member_id IS NULL AND member_goal_version_id IS NULL) OR (scope='MEMBER' AND owner_member_id IS NOT NULL AND member_goal_version_id IS NOT NULL)), 
	CHECK (status='DRAFT'), 
	CHECK (end_date > start_date), 
	UNIQUE (source_ref)
)""",
    """CREATE TABLE wemeet_goal.private_profiles (
	member_goal_version_id TEXT NOT NULL, 
	group_id TEXT NOT NULL, 
	member_id TEXT NOT NULL, 
	current_level TEXT NOT NULL, 
	weekly_available_minutes INTEGER NOT NULL, 
	details JSONB NOT NULL, 
	PRIMARY KEY (member_goal_version_id), 
	FOREIGN KEY(group_id, member_goal_version_id, member_id) REFERENCES wemeet_goal.member_goals (group_id, member_goal_version_id, member_id), 
	CHECK (weekly_available_minutes > 0 AND weekly_available_minutes <= 10080)
)""",
    """CREATE TABLE wemeet_goal.evaluation_baselines (
	baseline_id TEXT NOT NULL, 
	plan_version_id TEXT NOT NULL, 
	group_id TEXT NOT NULL, 
	confirmed_by TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (baseline_id), 
	FOREIGN KEY(group_id, plan_version_id) REFERENCES wemeet_goal.plan_versions (group_id, plan_version_id), 
	FOREIGN KEY(group_id, confirmed_by) REFERENCES wemeet_goal.members (group_id, member_id), 
	UNIQUE (plan_version_id)
)""",
    """CREATE TABLE wemeet_goal.milestones (
	plan_version_id TEXT NOT NULL, 
	position INTEGER NOT NULL, 
	title TEXT NOT NULL, 
	due_date DATE NOT NULL, 
	description TEXT NOT NULL, 
	PRIMARY KEY (plan_version_id, position), 
	FOREIGN KEY(plan_version_id) REFERENCES wemeet_goal.plan_versions (plan_version_id)
)""",
    """CREATE TABLE wemeet_goal.tasks (
	plan_version_id TEXT NOT NULL, 
	task_id TEXT NOT NULL, 
	owner_member_id TEXT, 
	title TEXT NOT NULL, 
	deliverable TEXT NOT NULL, 
	completion_criteria JSONB NOT NULL, 
	due_date DATE NOT NULL, 
	estimated_minutes INTEGER NOT NULL, 
	difficulty TEXT NOT NULL, 
	PRIMARY KEY (plan_version_id, task_id), 
	FOREIGN KEY(plan_version_id) REFERENCES wemeet_goal.plan_versions (plan_version_id), 
	CHECK (estimated_minutes > 0), 
	CHECK (difficulty IN ('EASY','MEDIUM','HARD'))
)""",
    """CREATE TABLE wemeet_goal.task_dependencies (
	plan_version_id TEXT NOT NULL, 
	task_id TEXT NOT NULL, 
	depends_on_task_id TEXT NOT NULL, 
	PRIMARY KEY (plan_version_id, task_id, depends_on_task_id), 
	FOREIGN KEY(plan_version_id, task_id) REFERENCES wemeet_goal.tasks (plan_version_id, task_id), 
	FOREIGN KEY(plan_version_id, depends_on_task_id) REFERENCES wemeet_goal.tasks (plan_version_id, task_id), 
	CHECK (task_id <> depends_on_task_id)
)""",
    """CREATE INDEX ix_shared_group_date ON wemeet_goal.shared_goals (group_id, created_at)""",
    """CREATE INDEX ix_member_group_owner ON wemeet_goal.member_goals (group_id, member_id)""",
    """CREATE INDEX ix_plan_group_owner ON wemeet_goal.plan_versions (group_id, owner_member_id)""",
    """CREATE INDEX ix_task_due ON wemeet_goal.tasks (plan_version_id, due_date)""",
    """CREATE FUNCTION wemeet_goal.reject_snapshot_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Goal/plan snapshots are immutable; create a new version'; END $$""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.shared_goals FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.member_goals FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.private_profiles FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.plan_versions FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.tasks FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.milestones FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.task_dependencies FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON wemeet_goal.evaluation_baselines FOR EACH ROW EXECUTE FUNCTION wemeet_goal.reject_snapshot_change()""",
    """CREATE FUNCTION wemeet_goal.check_task_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p wemeet_goal.plan_versions%ROWTYPE;
BEGIN SELECT * INTO p FROM wemeet_goal.plan_versions WHERE plan_version_id=NEW.plan_version_id;
IF NEW.due_date < p.start_date OR NEW.due_date > p.end_date THEN RAISE EXCEPTION 'Task date outside plan'; END IF;
IF p.scope='MEMBER' AND NEW.owner_member_id IS DISTINCT FROM p.owner_member_id THEN RAISE EXCEPTION 'Task owner mismatch'; END IF;
IF NEW.owner_member_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM wemeet_goal.members m WHERE m.group_id=p.group_id AND m.member_id=NEW.owner_member_id) THEN RAISE EXCEPTION 'Unknown task owner'; END IF;
RETURN NEW; END $$""",
    """CREATE TRIGGER task_scope BEFORE INSERT ON wemeet_goal.tasks FOR EACH ROW EXECUTE FUNCTION wemeet_goal.check_task_scope()""",
 ]

def upgrade():
    for statement in DDL:
        op.execute(statement)

def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Back up goal versions before an explicit schema rollback.")
