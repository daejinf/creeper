"""One database snapshot -> relational goals, in the caller's transaction."""
from datetime import datetime
from types import SimpleNamespace
from .repository import GoalRepository
from . import schema as db
import sqlalchemy as sa
from ..continuity import reviewed_goal_state, stale_reasons, planning_dependencies

def matches(goal,context,owner=None):
    if any(goal.get(k,[] if k=='milestones' else None)!=context.get(k,[] if k=='milestones' else None) for k in ('goal','start_date','end_date','completion_criteria','verification_method','privacy_scope','milestones')):return False
    for key,default in (('roles',{}),('checkins',[]),('frequency_per_week',None),('execution_method','')):
        if key in context and goal.get(key,default)!=context[key]:return False
    return not owner or goal.get('roles',{}).get(owner)==context.get('role')

def project_record(store,connection,record_id):
    view=store.load(record_id,internal=True,connection=connection)
    if view.model_extra:raise ValueError('Unexpected record data')
    work=GoalRepository(connection=connection)
    work.save_group(record_id,view.title,[m.model_dump() for m in view.members])
    shared=[(SimpleNamespace(artifact_id=r.goal_version_id),r.conditions) for r in connection.execute(sa.select(db.shared_goals).where(db.shared_goals.c.group_id==record_id).order_by(db.shared_goals.c.created_at))]
    personal={};plans={};heads={};member_heads={}
    for a in view.artifacts:
        if a.response.get('status')!='OK':continue
        value=a.response.get('data') or {};stamp=datetime.fromisoformat(a.created_at) if a.created_at else None
        if a.module=='C.confirm' and not a.input_stale:
            work.save_shared_goal(record_id,a.artifact_id,value,source_ref=a.artifact_id,created_at=stamp)
            shared.append((a,value))
        elif a.module=='E.contribution.confirm' and not a.input_stale:
            match=next(((old,g) for old,g in reversed(shared) if matches(g,value['context'],a.owner_member_id)),None)
            saved=connection.execute(sa.select(db.member_goals).where(db.member_goals.c.member_goal_version_id==a.artifact_id)).mappings().first()
            if saved:match=next(((old,g) for old,g in shared if old.artifact_id==saved['shared_goal_version_id']),None)
            if not match:raise ValueError('개인 기여 목표의 공동 목표 버전이 없습니다.')
            work.save_member_goal(record_id,a.owner_member_id,a.artifact_id,match[0].artifact_id,value['contribution'],value['profile'],source_ref=a.artifact_id,created_at=stamp)
            personal[a.owner_member_id]=(a,match[0].artifact_id)
        elif a.module in {'D','E.plan'}:
            owner=a.owner_member_id;context=a.request.get('context',a.request)
            match=next(((old,g) for old,g in reversed(shared) if matches(g,context,owner)),None)
            saved=connection.execute(sa.select(db.plans).where(db.plans.c.plan_version_id==value.get('plan_version_id'))).mappings().first()
            if saved:match=next(((old,g) for old,g in shared if old.artifact_id==saved['shared_goal_version_id']),None)
            if not match and a.module=='D' and a.request.get('confirmed'):
                origin=SimpleNamespace(artifact_id='input:'+a.artifact_id)
                work.save_shared_goal(record_id,origin.artifact_id,a.request,source_ref=origin.artifact_id,created_at=stamp)
                shared.append((origin,a.request));match=shared[-1]
            if not match and a.module=='E.plan':
                # Direct E is supported with SW-confirmed data, without A-D history.
                from ..shared.contracts import ConfirmedGoal
                context=a.request['context'];origin=SimpleNamespace(artifact_id='input:'+a.artifact_id)
                goal=ConfirmedGoal(**{k:v for k,v in context.items() if k in ConfirmedGoal.model_fields},members=view.members,execution_method='SW 확인 입력',confirmed=True)
                work.save_shared_goal(record_id,origin.artifact_id,goal,source_ref=origin.artifact_id,created_at=stamp)
                shared.append((origin,goal.model_dump(mode='json')));match=shared[-1]
            if not match:raise ValueError('계획의 확인된 공동 목표가 없습니다.')
            member=personal.get(owner) if owner else None
            if saved and saved['member_goal_version_id']:
                old=connection.execute(sa.select(db.member_goals).where(db.member_goals.c.member_goal_version_id==saved['member_goal_version_id'])).mappings().one()
                member=(SimpleNamespace(artifact_id=old['member_goal_version_id']),old['shared_goal_version_id'])
            if owner and not member:
                origin=SimpleNamespace(artifact_id='contribution:'+a.artifact_id,dependencies={},input_stale=a.input_stale)
                work.save_member_goal(record_id,owner,origin.artifact_id,match[0].artifact_id,a.request['contribution'],a.request['profile'],source_ref=origin.artifact_id,created_at=stamp)
                personal[owner]=(origin,match[0].artifact_id);member=personal[owner]
            work.save_plan(record_id,match[0].artifact_id,value,source_ref=a.artifact_id,member_goal_version_id=member[0].artifact_id if member else None,created_at=stamp)
            plans[owner or 'GROUP']=(a,match[0].artifact_id)
    current=reviewed_goal_state(view)['confirmed_goal']
    if not current and shared and not any(a.module=='C.confirm' for a in view.artifacts):current=shared[-1][1]
    # Select the snapshot matching the confirmed conditions, never simply the
    # most recently encountered historical/direct-input version.
    current_match=next(((a,g) for a,g in reversed(shared) if matches(g,current)),None) if current else None
    shared_id=current_match[0].artifact_id if current_match else None
    confirmed=next((a for a in reversed(view.artifacts) if a.module=='C.confirm' and a.response.get('status')=='OK' and not a.input_stale),None)
    if current and confirmed:shared_id=confirmed.artifact_id
    for owner,(a,gid) in personal.items():
        owner_view=store.load(record_id,owner_member_id=owner,connection=connection)
        if shared_id==gid and not a.input_stale and not stale_reasons(owner_view,planning_dependencies('E.contribution.confirm',a.dependencies)):member_heads[owner]=a.artifact_id
    for owner,(a,gid) in plans.items():
        owner_view=store.load(record_id,owner_member_id=None if owner=='GROUP' else owner,connection=connection)
        if shared_id==gid and not a.input_stale and not stale_reasons(owner_view,planning_dependencies(a.module,a.dependencies)):heads[owner]=a.response['data']['plan_version_id']
    work.set_heads(record_id,view.revision,shared_id,member_heads,heads)
