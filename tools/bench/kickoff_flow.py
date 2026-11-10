"""Local phase-2 adapters. Core B/C/E stay independently callable and DB-free.

Member selection here is a DEMO identity, not production authentication.
"""
import json
from typing import Any
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import Field
from wemeet_ai.cli import execute
from wemeet_ai.continuity import latest, source_tokens, stale_reasons
from wemeet_ai.shared.contracts import Contract, Message, GoalContext
from wemeet_ai.shared.privacy import sanitize
from wemeet_ai.m1.c_goal_draft import GoalDraft, review_field, confirm_goal
from wemeet_ai.m0.runtime import code_result
from wemeet_ai.m0.versions import result_code_status, reusable_result
from . import kickoff
from .records import repository, saved_call, failure, StorageResponse
from wemeet_ai.persistence.errors import RevisionConflict

router=APIRouter(prefix='/api/kickoff/groups')


class Action(Contract):
    action_id:str=Field(min_length=1,max_length=128)


class ReviewAction(Action):
    field:str
    updates:dict[str,Any]=Field(default_factory=dict)
    actor_member_id:str


class PersonalAction(Action):
    text:str=Field(default='',max_length=12000)
    message_id:str|None=None
    question_id:str|None=None
    option_id:str|None=None


def result_value(response):
    if isinstance(response,JSONResponse):
        if response.status_code not in {200,202}:raise StorageResponse(response)
        return json.loads(response.body)
    return response


def operation(gid,module,action,payload,runner,dependencies):
    """Reuse the durable action, and preserve the shared save-before-HTTP path."""
    repo=repository();old=repo.find_action(gid,action)
    if old:
        if old.module!=module or old.request!=sanitize(payload):raise RevisionConflict('같은 동작에 다른 입력을 사용할 수 없습니다.')
        return old.response
    revision=repo.load(gid).revision
    return result_value(saved_call(module,{'record_id':gid,'action_id':action,
        'expected_revision':revision,'input':payload,'dependencies':dependencies},runner))


def dependencies(view):
    tokens=source_tokens(view)
    return {k:tokens[k] for k in ('public_user_messages','members')}


def review_state(view):
    from wemeet_ai.continuity import reviewed_goal_state
    state=reviewed_goal_state(view)
    ai_draft=latest(view,'C');analysis=latest(view,'B')
    return {**state,'code_status':result_code_status(ai_draft.response if ai_draft else None,'C'),
        'analysis_code_status':result_code_status(analysis.response if analysis else None,'B')}


def group_reply(gid,result,*,reused=False):return sanitize({'result':result,'reused':reused,'group':kickoff.public_view(repository().load(gid))})


@router.post('/{gid}/review')
def build_review(gid:str,body:Action):
    try:
        prior=repository().find_action(gid,body.action_id+':C')
        if prior:
            if prior.module!='C':raise RevisionConflict('다른 동작입니다.')
            return group_reply(gid,prior.response,reused=True)
        view=repository().load(gid)
        current=review_state(view)
        if current['draft'] and not current['stale'] and current['code_status']=='CURRENT' and current['analysis_code_status']=='CURRENT':
            return group_reply(gid,current['last_result'],reused=True)
        if not view.conversation:return group_reply(gid,code_result('B',needed=['목표에 대해 먼저 이야기해주세요.']).model_dump(mode='json'))
        payload={'conversation':[m.model_dump(mode='json') for m in view.conversation[-200:]],
            'members':[m.model_dump() for m in view.members],'reference_date':view.reference_date.isoformat(),'timezone':view.timezone}
        reusable=next((a for a in reversed(view.artifacts) if a.module=='B' and reusable_result(a.response,'B')
            and a.request==payload and not stale_reasons(view,a.dependencies)),None)
        analysis=reusable.response if reusable else operation(gid,'B',body.action_id+':B',payload,
            lambda:execute('B',payload,runtime=kickoff.runtime_for(gid)).model_dump(mode='json'),dependencies(view))
        if analysis['status']!='OK':return group_reply(gid,analysis)
        old=review_state(repository().load(gid))['draft']
        payload={'facts':analysis['data']['facts'],'conflicted_fields':analysis['data'].get('conflicted_fields',[]),
            'previous':old,'members':payload['members'],'reference_date':payload['reference_date'],'mode':'ASSISTED'}
        result=operation(gid,'C',body.action_id+':C',payload,
            lambda:execute('C',payload,runtime=kickoff.runtime_for(gid)).model_dump(mode='json'),dependencies(view))
        return group_reply(gid,result)
    except Exception as exc:return failure(exc)


@router.post('/{gid}/review-fields')
def accept_fields(gid:str,body:ReviewAction):
    try:
        prior=repository().find_action(gid,body.action_id)
        if prior:
            if prior.module!='C.review' or prior.request.get('updates')!=sanitize(body.updates) or prior.request.get('actor_member_id')!=body.actor_member_id:raise RevisionConflict('다른 수정 동작입니다.')
            return group_reply(gid,prior.response,reused=True)
        view=repository().load(gid);state=review_state(view)
        if body.actor_member_id not in {m.member_id for m in view.members}:raise ValueError('Unknown member')
        if not state['draft'] or state['stale']:return group_reply(gid,code_result('C.review',needed=['공개 대화·구성원이 바뀌었습니다. 목표를 다시 정리해주세요.']).model_dump(mode='json'))
        payload={'draft':state['draft'],'updates':body.updates,'field':body.field,'actor_member_id':body.actor_member_id}
        def review():
            draft=GoalDraft.model_validate(state['draft'])
            fields=list(body.updates) if body.field=='*' else [body.field]
            for field in fields:
                value=body.updates.get(field)
                if body.field=='*' and (value is None or value=='' or value==[]):continue
                out=review_field(draft,field,value=value,edit=True,actor_id=body.actor_member_id)
                if out.status!='OK':return out.model_dump(mode='json')
                draft=out.data
            return code_result('C.review',draft).model_dump(mode='json')
        return group_reply(gid,operation(gid,'C.review',body.action_id,payload,review,dependencies(view)))
    except Exception as exc:return failure(exc)


@router.post('/{gid}/confirm')
def confirm(gid:str,body:Action):
    try:
        prior=repository().find_action(gid,body.action_id)
        if prior:
            if prior.module!='C.confirm':raise RevisionConflict('다른 동작입니다.')
            return group_reply(gid,prior.response,reused=True)
        view=repository().load(gid);state=review_state(view)
        if not state['draft'] or state['stale']:return group_reply(gid,code_result('C.confirm',needed=['목표 검토표를 최신 대화로 정리해주세요.']).model_dump(mode='json'))
        payload={'draft':state['draft'],'members':[m.model_dump() for m in view.members]}
        return group_reply(gid,operation(gid,'C.confirm',body.action_id,payload,
            lambda:confirm_goal(GoalDraft.model_validate(state['draft']),members=view.members).model_dump(mode='json'),dependencies(view)))
    except Exception as exc:return failure(exc)


def personal_view(view):
    from .kickoff_plans import personal_plan_state
    state=review_state(view);goal=state['confirmed_goal'];owner=view.owner_member_id
    context=None
    if goal:
        allowed=GoalContext.model_fields
        context={k:v for k,v in goal.items() if k in allowed}
        context['role']=goal.get('roles',{}).get(owner)
    run=next((a for a in reversed(view.artifacts) if a.module in {'E.coach','E.review'} and a.owner_member_id==owner),None)
    result=run.response if run else None
    if run and run.module=='E.review':
        result={**result,'data':{'state':result['data'],'reply':'개인 조건을 검토했습니다.'}}
    return sanitize({'member_id':owner,'context':context,'needs_review':not bool(goal) or state['stale'],
        'messages':[m.model_dump(mode='json') for m in view.private_conversation],
        'result':result,'request':run.request if run else None,'code_status':result_code_status(result,'E.coach'),'planning':personal_plan_state(view)})


def member_view(gid,mid):
    view=repository().load(gid,owner_member_id=mid)
    if mid not in {m.member_id for m in view.members}:raise ValueError('Unknown member')
    return view


@router.get('/{gid}/members/{mid}/personal')
def get_personal(gid:str,mid:str):
    try:return personal_view(member_view(gid,mid))
    except Exception as exc:return failure(exc)


@router.post('/{gid}/members/{mid}/personal/coach')
def personal_coach(gid:str,mid:str,body:PersonalAction):
    try:
        repo=repository();view=member_view(gid,mid);data=personal_view(view)
        prior=repo.find_action(gid,body.action_id)
        if prior:
            if prior.module!='E.coach' or prior.owner_member_id!=mid:raise RevisionConflict('다른 구성원의 동작입니다.')
            result=prior.response
        else:
            if not data['context'] or data['needs_review']:return {'result':code_result('E.coach',needed=['새 대화의 변경 요청을 공동 목표 검토에 반영해주세요. 기존 목표와 대화는 보존되어 있습니다.']).model_dump(mode='json'),'personal':data}
            original_dependencies=dependencies(view)
            original_dependencies['confirmed_goal']=source_tokens(view)['confirmed_goal']
            state=(data['result'] or {}).get('data',{}).get('state',{})
            answer=None
            if body.question_id or body.option_id:
                question=state.get('question') or {}
                if body.question_id!=question.get('question_id'):raise RevisionConflict('이미 답한 질문입니다. 현재 질문을 확인해주세요.')
                option=next((o for o in question.get('options',[]) if o['option_id']==body.option_id),None)
                if not option:raise ValueError('Unknown option')
                text=option['label']
            else:text=body.text.strip()
            if text:
                if not body.message_id:raise ValueError('Message ID required')
                old=next((m for m in view.private_conversation if m.message_id==body.message_id),None)
                if old and (old.role!='user' or old.text!=sanitize(text)):raise RevisionConflict('같은 메시지에 다른 답변입니다.')
                if not old:repo.append_message(gid,Message(message_id=body.message_id,speaker_id=mid,text=text,
                    created_at=datetime.now(timezone.utc)),scope='PRIVATE',owner_member_id=mid)
                if body.question_id:answer={'question_id':body.question_id,'option_id':body.option_id,'message_id':body.message_id}
            view=member_view(gid,mid)
            if stale_reasons(view,original_dependencies) or not review_state(view)['confirmed_goal']:
                return {'result':code_result('E.coach',needed=['공동 조건이 바뀌었습니다. 목표 검토에서 다시 확인해주세요. 답변은 저장했습니다.']).model_dump(mode='json'),'personal':personal_view(view)}
            payload={'context':data['context'],'owner_member_id':mid,'state':state,
                'conversation':[m.model_dump(mode='json') for m in view.private_conversation[-200:]],
                'reference_date':view.reference_date.isoformat(),'answer':answer}
            deps=original_dependencies
            result=operation(gid,'E.coach',body.action_id,payload,
                lambda:execute('E.coach',payload,runtime=kickoff.runtime_for(gid)).model_dump(mode='json'),deps)
        if result.get('status')=='OK' and result.get('data',{}).get('reply'):
            artifact=repo.find_action(gid,body.action_id)
            repo.append_message(gid,Message(message_id='personal-coach:'+result['meta']['run_id'],role='assistant',
                text=result['data']['reply'],created_at=artifact.created_at),scope='PRIVATE',owner_member_id=mid)
        return sanitize({'result':result,'personal':personal_view(member_view(gid,mid))})
    except Exception as exc:return failure(exc)
