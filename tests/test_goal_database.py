"""Real PostgreSQL tests. Every test rolls back its outer transaction.

No SQLite substitution: without local DB setup these tests explicitly skip.
"""
import json
from uuid import uuid4
from datetime import timedelta
import pytest

sa=pytest.importorskip('sqlalchemy')
from wemeet_ai.persistence import GoalRepository, GoalConflict
from wemeet_ai.persistence import schema as db
from wemeet_ai.shared.contracts import Plan, Task
from _data import sample

@pytest.fixture
def pg():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as k:url=winreg.QueryValueEx(k,'WEMEET_GOAL_DATABASE_URL')[0]
    except (ImportError,OSError):pytest.skip('Real PostgreSQL setup not available')
    engine=sa.create_engine(url,hide_parameters=True,connect_args={'connect_timeout':3})
    with engine.connect() as c:
        transaction=c.begin()
        try:yield GoalRepository(connection=c)
        finally:transaction.rollback()
    engine.dispose()

def seeded(pg):
    from wemeet_ai.shared.contracts import ConfirmedGoal
    gid=str(uuid4());goal=ConfirmedGoal.model_validate(sample('D'))
    pg.save_group(gid,'검증 그룹',[m.model_dump() for m in goal.members])
    version=str(uuid4());pg.save_shared_goal(gid,version,goal,source_ref=version)
    return gid,version,goal

def plan_for(goal,owner=None):
    return Plan(plan_version_id=str(uuid4()),goal=goal.goal,start_date=goal.start_date,end_date=goal.end_date,
        owner_member_id=owner,tasks=[Task(task_id=str(uuid4()),owner_member_id=owner,title='초안 정리',deliverable='공동 목표에 맞는 진행안 초안',
            completion_criteria=['참여 방식을 선택할 수 있다'],due_date=goal.start_date+timedelta(days=2),estimated_minutes=20)])

def member_goal(pg,gid,shared,member):
    version=str(uuid4());profile={'current_level':'일부 경험','weekly_available_minutes':30,'confirmed':True,
        'constraints':['PRIVATE_CANARY_월요일은_개인_병원_일정'],'private_notes':['PRIVATE_CANARY_개인사유']}
    pg.save_member_goal(gid,member,version,shared,{'title':'참여 설계','deliverable':'진행안 한 장','completion_criteria':['참여 방식 선택 가능'],'confirmed':True},profile,source_ref=version)
    return version

def test_postgres_roundtrip_public_private_and_no_other_owner(pg):
    gid,shared,goal=seeded(pg);mid=goal.members[0].member_id;mg=member_goal(pg,gid,shared,mid)
    plan=plan_for(goal,mid);pg.save_plan(gid,shared,plan,source_ref=plan.plan_version_id,member_goal_version_id=mg)
    public=json.dumps(pg.public_group(gid),default=str,ensure_ascii=False)
    private=pg.private_member(gid,mid)
    assert 'PRIVATE_CANARY' not in public and 'weekly_available_minutes' not in public
    assert private['member_goals'][0]['profile']['weekly_available_minutes']==30
    assert len(private['plans'][0]['tasks'])==1
    assert pg.private_member(gid,'not-the-owner')['plans']==[]

def test_immutable_versions_and_idempotent_retry(pg):
    gid,shared,goal=seeded(pg);plan=plan_for(goal)
    pg.save_plan(gid,shared,plan,source_ref=plan.plan_version_id)
    pg.save_plan(gid,shared,plan,source_ref=plan.plan_version_id)
    assert len(pg.public_group(gid)['group_plans'])==1
    changed=plan.model_copy(deep=True);changed.tasks[0].title='변경한 과제'
    with pytest.raises(GoalConflict):pg.save_plan(gid,shared,changed,source_ref=plan.plan_version_id)
    with pytest.raises(sa.exc.DBAPIError):
        with pg.transaction() as c:c.execute(db.tasks.update().where(db.tasks.c.plan_version_id==plan.plan_version_id).values(title='overwrite'))
    changed.previous_plan_version_id=plan.plan_version_id;changed.plan_version_id=str(uuid4());changed.tasks[0].task_id=str(uuid4())
    pg.save_plan(gid,shared,changed,source_ref=changed.plan_version_id)
    assert len(pg.public_group(gid)['group_plans'])==2

def test_time_budget_and_date_validation_before_insert(pg):
    gid,shared,goal=seeded(pg);mid=goal.members[0].member_id;mg=member_goal(pg,gid,shared,mid)
    plan=plan_for(goal,mid);plan.tasks[0].estimated_minutes=60
    with pytest.raises(ValueError,match='TIME_BUDGET_EXCEEDED'):pg.save_plan(gid,shared,plan,source_ref='budget',member_goal_version_id=mg)
    plan.tasks[0].estimated_minutes=20;plan.tasks[0].due_date=goal.end_date+timedelta(days=1)
    with pytest.raises(ValueError,match='DATE_OUTSIDE_PERIOD'):pg.save_plan(gid,shared,plan,source_ref='date',member_goal_version_id=mg)
    assert pg.private_member(gid,mid)['plans']==[]

def test_foreign_keys_reject_cross_group_goal_and_mismatched_owner(pg):
    gid,shared,goal=seeded(pg);other,other_shared,_=seeded(pg)
    with pytest.raises(sa.exc.DBAPIError):
        member_goal(pg,gid,other_shared,goal.members[0].member_id)
    assert pg.public_group(gid)['member_goals']==[]

def test_baseline_exact_plan_task_and_old_version_preserved(pg):
    gid,shared,goal=seeded(pg);mid=goal.members[0].member_id;mg=member_goal(pg,gid,shared,mid);plan=plan_for(goal,mid)
    pg.save_plan(gid,shared,plan,source_ref='first',member_goal_version_id=mg)
    pg.set_heads(gid,1,shared,{mid:mg},{mid:plan.plan_version_id})
    with pytest.raises(ValueError):pg.confirm_baseline(gid,plan.plan_version_id,goal.members[1].member_id)
    baseline=pg.confirm_baseline(gid,plan.plan_version_id,mid)
    assert baseline==pg.confirm_baseline(gid,plan.plan_version_id,mid)
    context=pg.evaluation_context(gid,plan.plan_version_id,plan.tasks[0].task_id,owner_member_id=mid)
    assert context['baseline_id']==baseline['baseline_id'] and context['member_goal_version_id']==mg
    with pytest.raises(ValueError):pg.evaluation_context(gid,plan.plan_version_id,plan.tasks[0].task_id,owner_member_id='other')
    pg.set_heads(gid,2,None,{}, {})
    with pytest.raises(GoalConflict):pg.confirm_baseline(gid,plan.plan_version_id,mid)
    assert pg.evaluation_context(gid,plan.plan_version_id,plan.tasks[0].task_id,owner_member_id=mid)['baseline_id']==baseline['baseline_id']

def test_postgres_transaction_rollback_preserves_success(pg):
    gid,shared,goal=seeded(pg);first=plan_for(goal)
    pg.save_plan(gid,shared,first,source_ref='success')
    with pytest.raises(sa.exc.DBAPIError):
        with pg.transaction() as c:
            c.execute(db.baselines.insert().values(baseline_id=str(uuid4()),plan_version_id='missing-plan',group_id=gid,confirmed_by=goal.members[0].member_id))
    assert pg.public_group(gid)['group_plans'][0]['plan_version_id']==first.plan_version_id

def test_unconfirmed_goal_never_saved(pg):
    gid,shared,goal=seeded(pg);goal.confirmed=False
    with pytest.raises(ValueError):pg.save_shared_goal(gid,str(uuid4()),goal,source_ref='unconfirmed')
    assert len(pg.public_group(gid)['shared_goals'])==1


def test_saved_record_projection_and_replay_are_idempotent(pg,tmp_path,monkeypatch):
    from wemeet_ai.persistence import PostgresRecordStore
    from wemeet_ai.continuity import Artifact
    from wemeet_ai.m0.runtime import code_result
    from wemeet_ai.shared.contracts import ConfirmedGoal
    source=PostgresRecordStore(connection=pg.connection);goal=ConfirmedGoal.model_validate(sample('D'))
    view=source.create(members=[m.model_dump() for m in goal.members]);gid=view.record_id
    plan=plan_for(goal)
    source.save_artifact(gid,Artifact(artifact_id=str(uuid4()),action_id='plan',module='D',scope='PUBLIC',request=goal.model_dump(mode='json'),response=code_result('D',plan).model_dump(mode='json')))
    source.repair_projection(gid);source.repair_projection(gid)
    assert len(pg.public_group(gid)['group_plans'])==1
    bad=plan.model_copy(deep=True);bad.plan_version_id=str(uuid4());bad.tasks[0].due_date=goal.end_date+timedelta(days=1)
    with pytest.raises(ValueError):
        with pg.transaction():
            source.save_artifact(gid,Artifact(artifact_id=str(uuid4()),action_id='invalid-later',module='D',scope='PUBLIC',request=goal.model_dump(mode='json'),response=code_result('D',bad).model_dump(mode='json')))
    assert len(pg.public_group(gid)['group_plans'])==1



def test_database_itself_rejects_bad_task_scope(pg):
    gid,shared,goal=seeded(pg);plan=plan_for(goal)
    pg.save_plan(gid,shared,plan,source_ref='valid')
    task=plan.tasks[0].model_dump(exclude={'dependencies'})
    for changes in [{'due_date':goal.end_date+timedelta(days=1)},{'owner_member_id':'unknown-person'}]:
        with pytest.raises(sa.exc.DBAPIError):
            with pg.transaction() as c:c.execute(db.tasks.insert().values(plan_version_id=plan.plan_version_id,**{**task,'task_id':str(uuid4()),**changes}))
    assert len(pg.public_group(gid)['group_plans'][0]['tasks'])==1

def test_canonical_storage_status_does_not_reuse_old_sync_failure(pg,monkeypatch):
    from wemeet_ai.persistence import PostgresRecordStore
    from tools.bench import goal_database
    monkeypatch.setenv('WEMEET_GOAL_DATABASE_URL','configured-in-test-fixture')
    store=PostgresRecordStore(connection=pg.connection)
    gid=store.create('상태 검사').record_id
    with pg.transaction() as c:c.execute(db.sync_attempts.insert().values(record_id=gid,status='FAILED',error_code='LEGACY_FAILED'))
    monkeypatch.setattr(goal_database,'goal_repository',lambda:pg)
    status=goal_database.storage_status(gid)
    assert status['status']=='CONNECTED' and status['atomic_storage'] and status['record_revision']==0
