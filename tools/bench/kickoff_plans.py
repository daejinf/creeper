"""Phase 3: reviewed group/member inputs -> existing D/E/F modules.

Plans remain DRAFT. This local adapter does not activate service goals.
"""
from typing import Any
from fastapi import APIRouter
from pydantic import Field
from wemeet_ai.cli import execute
from wemeet_ai.continuity import latest, source_tokens, stale_reasons, artifact_key
from wemeet_ai.shared.contracts import Contract, GoalContext
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.m0.versions import result_code_status, reusable_result
from wemeet_ai.m1.e_member_plan import MemberState, update_member_condition
from . import kickoff
from .records import repository, failure
from wemeet_ai.persistence.errors import RevisionConflict
from .kickoff_flow import Action, operation, review_state, dependencies, group_reply, member_view

router=APIRouter(prefix='/api/kickoff/groups')


class GenerateAction(Action):
    fresh:bool=False


class ContributionReview(Action):
    title:str=Field(default='',max_length=250)
    deliverable:str=Field(default='',max_length=3000)
    completion_criteria:list[str]=Field(default_factory=list)
    current_level:str=Field(default='',max_length=500)
    weekly_available_minutes:int|None=Field(default=None,gt=0,le=10080)


def state_of(view):
    run=next((a for a in reversed(view.artifacts) if a.module in {'E.coach','E.review'} and
        a.owner_member_id==view.owner_member_id and a.response.get('data')),None)
    if not run:return {}
    data=run.response['data']
    return data if run.module=='E.review' else data.get('state',{})


def goal_context(view):
    goal=review_state(view)['confirmed_goal']
    if not goal:return None
    context={k:v for k,v in goal.items() if k in GoalContext.model_fields}
    context['role']=goal.get('roles',{}).get(view.owner_member_id)
    return context


def plan_dependencies(view,personal=False):
    tokens=source_tokens(view);deps=dependencies(view)
    deps['confirmed_goal']=tokens['confirmed_goal']
    if personal:
        key='private_user_messages:'+view.owner_member_id
        deps[key]=tokens[key]
        for module in ('E.coach','E.review','E.contribution.confirm'):
            key=artifact_key(module,'PRIVATE',view.owner_member_id)
            if key in tokens:deps[key]=tokens[key]
    return deps


def validation(plan,request,personal=False):
    context=request.get('context',request)
    payload={'scope':'MEMBER' if personal else 'GROUP',
        'context':{k:v for k,v in context.items() if k in GoalContext.model_fields}}
    if personal:
        payload.update(member_plans=[plan],weekly_budgets={request['owner_member_id']:request['profile']['weekly_available_minutes']})
    else:payload['group_plan']=plan
    return execute('F',payload).model_dump(mode='json')


def plan_state(view,module,personal=False):
    run=latest(view,module,view.owner_member_id if personal else None)
    attempts=[a for a in view.artifacts if a.module==module and (not personal or a.owner_member_id==view.owner_member_id)]
    return {'result':run.response if run else None,'request':run.request if run else None,
        'code_status':result_code_status(run.response if run else None,module),
        'stale':bool(run and stale_reasons(view,run.dependencies)),
        'validation':validation(run.response['data'],run.request,personal) if run else None,
        'last_attempt':attempts[-1].response if attempts else None}


def group_plan_state(view):return plan_state(view,'D')


def personal_plan_state(view):
    draft=latest(view,'E.draft',view.owner_member_id)
    confirmed=latest(view,'E.contribution.confirm',view.owner_member_id)
    return {'draft':draft.response.get('data') if draft else None,
        'draft_code_status':result_code_status(draft.response if draft else None,'E.draft'),
        'draft_stale':bool(draft and stale_reasons(view,draft.dependencies)),
        'review':confirmed.response.get('data') if confirmed else None,
        'review_stale':bool(confirmed and stale_reasons(view,confirmed.dependencies)),
        'plan':plan_state(view,'E.plan',True)}


def old_action(gid,action,module,owner=None):
    old=repository().find_action(gid,action)
    if old and (old.module!=module or old.owner_member_id!=owner):raise RevisionConflict('다른 동작 또는 구성원의 요청입니다.')
    return old.response if old else None


def private_reply(gid,mid,result,*,reused=False):
    from .kickoff_flow import personal_view
    return {'result':result,'reused':reused,'personal':personal_view(member_view(gid,mid))}


@router.post('/{gid}/plan')
def generate_group(gid:str,body:GenerateAction):
    try:
        old=old_action(gid,body.action_id,'D')
        if old:return group_reply(gid,old,reused=True)
        view=repository().load(gid);goal=review_state(view)['confirmed_goal'];previous=latest(view,'D')
        if not goal:return group_reply(gid,code_result('D',needed=['목표 검토에서 공동 조건을 확인해주세요.']).model_dump(mode='json'))
        if not body.fresh and previous and reusable_result(previous.response,'D') and not stale_reasons(view,previous.dependencies):return group_reply(gid,previous.response,reused=True)
        def run():
            result=execute('D',goal,runtime=kickoff.runtime_for(gid),fresh=body.fresh)
            if result.status=='OK' and previous and result.data.plan_version_id!=previous.response['data']['plan_version_id']:
                result.data.previous_plan_version_id=previous.response['data']['plan_version_id']
            return result.model_dump(mode='json')
        return group_reply(gid,operation(gid,'D',body.action_id,goal,run,plan_dependencies(view)))
    except Exception as exc:return failure(exc)


@router.post('/{gid}/members/{mid}/contribution/draft')
def draft_contribution(gid:str,mid:str,body:GenerateAction):
    try:
        old=old_action(gid,body.action_id,'E.draft',mid)
        if old:return private_reply(gid,mid,old,reused=True)
        view=member_view(gid,mid);context=goal_context(view);previous=latest(view,'E.draft',mid)
        if not context:return private_reply(gid,mid,code_result('E.draft',needed=['공동 조건을 먼저 확인해주세요.']).model_dump(mode='json'))
        if not body.fresh and previous and reusable_result(previous.response,'E.draft') and not stale_reasons(view,previous.dependencies):return private_reply(gid,mid,previous.response,reused=True)
        payload={'context':context,'owner_member_id':mid,'state':state_of(view),
            'conversation':[m.model_dump(mode='json') for m in view.private_conversation[-200:]],'reference_date':view.reference_date.isoformat()}
        return private_reply(gid,mid,operation(gid,'E.draft',body.action_id,payload,
            lambda:execute('E.draft',payload,runtime=kickoff.runtime_for(gid),fresh=body.fresh).model_dump(mode='json'),plan_dependencies(view,True)))
    except Exception as exc:return failure(exc)


@router.post('/{gid}/members/{mid}/contribution/confirm')
def confirm_contribution(gid:str,mid:str,body:ContributionReview):
    try:
        old=old_action(gid,body.action_id,'E.contribution.confirm',mid)
        if old:
            stored=old['data']
            incoming={'title':body.title.strip(),'deliverable':body.deliverable.strip(),
                'completion_criteria':[x.strip() for x in body.completion_criteria if x.strip()]}
            if any(stored['contribution'][k]!=value for k,value in incoming.items()) or stored['profile']['current_level']!=body.current_level.strip() or stored['profile']['weekly_available_minutes']!=body.weekly_available_minutes:
                raise RevisionConflict('수정한 조건은 새 동작 ID로 확인해주세요.')
            return private_reply(gid,mid,old,reused=True)
        view=member_view(gid,mid);context=goal_context(view)
        missing=[]
        for field in ('title','deliverable','current_level'):
            if not getattr(body,field).strip():missing.append(field)
        criteria=[x.strip() for x in body.completion_criteria if x.strip()]
        if not criteria:missing.append('completion_criteria')
        if not body.weekly_available_minutes:missing.append('weekly_available_minutes')
        if not context:missing.append('confirmed_goal')
        if missing:return private_reply(gid,mid,code_result('E.contribution.confirm',needed=missing).model_dump(mode='json'))
        state=MemberState.model_validate(state_of(view))
        state.owner_member_id=mid
        for field in ('current_level','weekly_available_minutes'):
            state=update_member_condition(state,field,value=getattr(body,field),actor_id=mid).data
        state_payload={'owner_member_id':mid,'state':state.model_dump(mode='json')}
        operation(gid,'E.review',body.action_id+':profile',state_payload,
            lambda:code_result('E.review',state).model_dump(mode='json'),plan_dependencies(view,True))
        view=member_view(gid,mid)
        if goal_context(view)!=context:return private_reply(gid,mid,code_result('E.contribution.confirm',needed=['공동 조건이 바뀌었습니다. 다시 확인해주세요.']).model_dump(mode='json'))
        payload={'context':context,'owner_member_id':mid,
            'contribution':{'title':body.title.strip(),'deliverable':body.deliverable.strip(),'completion_criteria':criteria,'confirmed':True},
            'profile':{'current_level':body.current_level.strip(),'weekly_available_minutes':body.weekly_available_minutes,'confirmed':True}}
        # Dependency on the previous confirmation would invalidate this new review itself.
        deps=plan_dependencies(view,True);deps.pop(artifact_key('E.contribution.confirm','PRIVATE',mid),None)
        return private_reply(gid,mid,operation(gid,'E.contribution.confirm',body.action_id,payload,
            lambda:code_result('E.contribution.confirm',payload).model_dump(mode='json'),deps))
    except Exception as exc:return failure(exc)


@router.post('/{gid}/members/{mid}/plan')
def generate_personal(gid:str,mid:str,body:GenerateAction):
    try:
        old=old_action(gid,body.action_id,'E.plan',mid)
        if old:return private_reply(gid,mid,old,reused=True)
        view=member_view(gid,mid);confirmed=latest(view,'E.contribution.confirm',mid);previous=latest(view,'E.plan',mid)
        if not goal_context(view) or not confirmed or stale_reasons(view,confirmed.dependencies):
            return private_reply(gid,mid,code_result('E.plan',needed=['기여 목표·수준·시간을 검토하고 확인해주세요.']).model_dump(mode='json'))
        if not body.fresh and previous and reusable_result(previous.response,'E.plan') and not stale_reasons(view,previous.dependencies):return private_reply(gid,mid,previous.response,reused=True)
        payload=confirmed.response['data']
        def run():
            result=execute('E.plan',payload,runtime=kickoff.runtime_for(gid),fresh=body.fresh)
            if result.status=='OK' and previous and result.data.plan_version_id!=previous.response['data']['plan_version_id']:
                result.data.previous_plan_version_id=previous.response['data']['plan_version_id']
            return result.model_dump(mode='json')
        return private_reply(gid,mid,operation(gid,'E.plan',body.action_id,payload,run,plan_dependencies(view,True)))
    except Exception as exc:return failure(exc)
