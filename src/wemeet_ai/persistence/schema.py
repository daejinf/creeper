"""Relational goal snapshots; private conditions never live in public rows."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData(schema='wemeet_goal')
S = 'wemeet_goal.'
def col(name, kind=sa.Text, **kw): return sa.Column(name, kind, **kw)
def created(): return col('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

groups = sa.Table('groups', metadata, col('group_id', primary_key=True), col('title', nullable=False))
members = sa.Table('members', metadata, col('group_id', primary_key=True),
    col('member_id', primary_key=True), col('nickname', nullable=False),
    sa.ForeignKeyConstraint(['group_id'], [S+'groups.group_id']))

shared_goals = sa.Table('shared_goals', metadata,
    col('goal_version_id', primary_key=True), col('group_id', nullable=False), col('source_ref', nullable=False, unique=True),
    col('title', nullable=False), col('start_date', sa.Date, nullable=False), col('end_date', sa.Date, nullable=False),
    col('completion_criteria', JSONB, nullable=False), col('verification_method', nullable=False),
    col('privacy_scope', nullable=False), col('conditions', JSONB, nullable=False), col('content_hash', nullable=False), created(),
    sa.ForeignKeyConstraint(['group_id'], [S+'groups.group_id']),
    sa.UniqueConstraint('group_id','goal_version_id'),
    sa.CheckConstraint('end_date > start_date'),
    sa.CheckConstraint("privacy_scope IN ('OBJECTIVE_ONLY','PRIVATE','CUSTOM')"))

member_goals = sa.Table('member_goals', metadata,
    col('member_goal_version_id', primary_key=True), col('group_id', nullable=False), col('member_id', nullable=False),
    col('shared_goal_version_id', nullable=False), col('source_ref', nullable=False, unique=True),
    col('title', nullable=False), col('deliverable', nullable=False), col('completion_criteria', JSONB, nullable=False),
    col('content_hash', nullable=False), created(),
    sa.ForeignKeyConstraint(['group_id','member_id'], [S+'members.group_id',S+'members.member_id']),
    sa.ForeignKeyConstraint(['group_id','shared_goal_version_id'], [S+'shared_goals.group_id',S+'shared_goals.goal_version_id']),
    sa.UniqueConstraint('group_id','member_goal_version_id','member_id'),
    sa.UniqueConstraint('group_id','member_goal_version_id','member_id','shared_goal_version_id'))

private_profiles = sa.Table('private_profiles', metadata,
    col('member_goal_version_id', primary_key=True), col('group_id', nullable=False), col('member_id', nullable=False),
    col('current_level', nullable=False), col('weekly_available_minutes', sa.Integer, nullable=False), col('details', JSONB, nullable=False),
    sa.ForeignKeyConstraint(['group_id','member_goal_version_id','member_id'], [S+'member_goals.group_id',S+'member_goals.member_goal_version_id',S+'member_goals.member_id']),
    sa.CheckConstraint('weekly_available_minutes > 0 AND weekly_available_minutes <= 10080'))

plans = sa.Table('plan_versions', metadata,
    col('plan_version_id', primary_key=True), col('group_id', nullable=False), col('shared_goal_version_id', nullable=False),
    col('member_goal_version_id'), col('owner_member_id'), col('scope', nullable=False),
    col('previous_plan_version_id'), col('source_ref', nullable=False, unique=True),
    col('title', nullable=False), col('start_date', sa.Date, nullable=False), col('end_date', sa.Date, nullable=False),
    col('status', nullable=False, server_default='DRAFT'), col('weekly_outcomes', JSONB, nullable=False),
    col('adaptation_rules', JSONB, nullable=False), col('suggestions', JSONB, nullable=False),
    col('validation', JSONB, nullable=False), col('content_hash', nullable=False), created(),
    sa.ForeignKeyConstraint(['group_id','shared_goal_version_id'], [S+'shared_goals.group_id',S+'shared_goals.goal_version_id']),
    sa.ForeignKeyConstraint(['group_id','member_goal_version_id','owner_member_id','shared_goal_version_id'],
        [S+'member_goals.group_id',S+'member_goals.member_goal_version_id',S+'member_goals.member_id',S+'member_goals.shared_goal_version_id']),
    sa.UniqueConstraint('group_id','plan_version_id'),
    sa.ForeignKeyConstraint(['group_id','previous_plan_version_id'], [S+'plan_versions.group_id',S+'plan_versions.plan_version_id']),
    sa.CheckConstraint("(scope='GROUP' AND owner_member_id IS NULL AND member_goal_version_id IS NULL) OR (scope='MEMBER' AND owner_member_id IS NOT NULL AND member_goal_version_id IS NOT NULL)"),
    sa.CheckConstraint("status='DRAFT'"), sa.CheckConstraint('end_date > start_date'))

tasks = sa.Table('tasks', metadata,
    col('plan_version_id', primary_key=True), col('task_id', primary_key=True), col('owner_member_id'),
    col('title', nullable=False), col('deliverable', nullable=False), col('completion_criteria', JSONB, nullable=False),
    col('due_date', sa.Date, nullable=False), col('estimated_minutes', sa.Integer, nullable=False), col('difficulty', nullable=False),
    sa.ForeignKeyConstraint(['plan_version_id'], [S+'plan_versions.plan_version_id']),
    sa.CheckConstraint('estimated_minutes > 0'), sa.CheckConstraint("difficulty IN ('EASY','MEDIUM','HARD')"))

milestones = sa.Table('milestones', metadata,
    col('plan_version_id', primary_key=True), col('position', sa.Integer, primary_key=True),
    col('title', nullable=False), col('due_date', sa.Date, nullable=False), col('description', nullable=False),
    sa.ForeignKeyConstraint(['plan_version_id'], [S+'plan_versions.plan_version_id']))

dependencies = sa.Table('task_dependencies', metadata,
    col('plan_version_id', primary_key=True), col('task_id', primary_key=True), col('depends_on_task_id', primary_key=True),
    sa.ForeignKeyConstraint(['plan_version_id','task_id'], [S+'tasks.plan_version_id',S+'tasks.task_id']),
    sa.ForeignKeyConstraint(['plan_version_id','depends_on_task_id'], [S+'tasks.plan_version_id',S+'tasks.task_id']),
    sa.CheckConstraint('task_id <> depends_on_task_id'))

baselines = sa.Table('evaluation_baselines', metadata,
    col('baseline_id', primary_key=True), col('plan_version_id', nullable=False, unique=True),
    col('group_id', nullable=False), col('confirmed_by', nullable=False), created(),
    sa.ForeignKeyConstraint(['group_id','plan_version_id'], [S+'plan_versions.group_id',S+'plan_versions.plan_version_id']),
    sa.ForeignKeyConstraint(['group_id','confirmed_by'], [S+'members.group_id',S+'members.member_id']))

# Mutable projection pointers; changing a pointer never rewrites a version snapshot.
heads = sa.Table('record_heads', metadata,
    col('group_id', primary_key=True), col('source_revision', sa.Integer, nullable=False), col('current_shared_goal_version_id'),
    col('current_member_goals', JSONB, nullable=False), col('current_plans', JSONB, nullable=False), created(),
    sa.ForeignKeyConstraint(['group_id','current_shared_goal_version_id'], [S+'shared_goals.group_id',S+'shared_goals.goal_version_id']))

sa.Index('ix_shared_group_date', shared_goals.c.group_id, shared_goals.c.created_at)
sa.Index('ix_member_group_owner', member_goals.c.group_id, member_goals.c.member_id)
sa.Index('ix_plan_group_owner', plans.c.group_id, plans.c.owner_member_id)
sa.Index('ix_task_due', tasks.c.plan_version_id, tasks.c.due_date)

# Diagnostics can refer to a legacy record whose domain transaction was rejected.
# They are not goals and cannot be used as an evaluation baseline.
sync_attempts = sa.Table('sync_attempts', metadata,
    col('record_id', primary_key=True), col('status', nullable=False), col('error_code'), created(),
    sa.CheckConstraint("status IN ('SAVED','FAILED')"))
