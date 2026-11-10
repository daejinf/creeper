"""Local embedded kickoff demo. Public chat and separate goal/private adapters.

Do not expose this demo to a public network. Trusted SW identity/session exchange
are subsequent work, not simulated by the member selector.
"""
import json
from copy import deepcopy
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, field_validator, model_validator

from wemeet_ai.shared.contracts import Contract, Member, Message
from wemeet_ai.shared.privacy import sanitize
from wemeet_ai.m1.a_coaching import CoachRequest, coach_reply
from wemeet_ai.m0 import Runtime, MemoryResultCache
from wemeet_ai.m0.versions import result_code_status, reusable_result
from .records import repository, failure, saved_call
from wemeet_ai.persistence.errors import RevisionConflict

router=APIRouter()
cache=MemoryResultCache()
_locks={}
_guard=threading.Lock()


class GroupInput(Contract):
    title: str = Field(min_length=1,max_length=100)
    members: list[Member] = Field(min_length=1)

    @model_validator(mode='after')
    def unique(self):
        if not self.title.strip() or any(not m.nickname.strip() for m in self.members):raise ValueError('Blank name')
        if len({m.member_id for m in self.members})!=len(self.members):raise ValueError('Duplicate member ID')
        return self


class ChatInput(Contract):
    message_id: str = Field(min_length=1,max_length=128)
    member_id: str = Field(min_length=1)
    text: str = Field(min_length=1,max_length=12000)

    @field_validator('text')
    @classmethod
    def not_blank(cls,value):
        if not value.strip():raise ValueError('Empty message')
        return value.strip()


class CoachInput(Contract):
    action_id: str = Field(min_length=1,max_length=128)
    fresh: bool = False


def public_view(view):
    from .kickoff_flow import review_state
    from .kickoff_plans import group_plan_state
    from .goal_database import storage_status
    runs=[a for a in view.artifacts if a.module=='A' and a.scope=='PUBLIC']
    return sanitize({'group_id':view.record_id,'title':view.title,'revision':view.revision,
        'members':[m.model_dump() for m in view.members],
        'messages':[m.model_dump(mode='json') for m in view.conversation],
        'coach_result':runs[-1].response if runs else None,
        'coach_code_status':result_code_status(runs[-1].response if runs else None,'A'),
        'coach_request':runs[-1].request if runs else None,'review':review_state(view),'group_plan':group_plan_state(view),
        'goal_database':storage_status(view.record_id)})


def stored_public(view):
    return public_view(view)


def runtime_for(group_id):return Runtime(cache=cache,scope_id='kickoff:'+group_id)


@router.get('/kickoff')
def embedded():
    return FileResponse(Path(__file__).with_name('kickoff.html'),headers={
        'Cache-Control':'no-store','Content-Security-Policy':"frame-ancestors 'self'"})


@router.get('/kickoff-demo')
def demo():return FileResponse(Path(__file__).with_name('kickoff-demo.html'),headers={'Cache-Control':'no-store'})


@router.post('/api/kickoff/groups')
def create_group(body:GroupInput):
    try:return stored_public(repository().create(body.title.strip(),members=[m.model_dump() for m in body.members]))
    except Exception as exc:return failure(exc)


@router.get('/api/kickoff/groups/{group_id}')
def get_group(group_id:str):
    try:return public_view(repository().load(group_id))
    except Exception as exc:return failure(exc)


@router.post('/api/kickoff/groups/{group_id}/members')
def add_member(group_id:str,body:Member):
    try:
        if not body.nickname.strip():raise ValueError('Blank name')
        repository().add_member(group_id,body.model_copy(update={'nickname':body.nickname.strip()}).model_dump())
        return stored_public(repository().load(group_id))
    except Exception as exc:return failure(exc)


@router.post('/api/kickoff/groups/{group_id}/messages')
def send_message(group_id:str,body:ChatInput):
    try:
        repo=repository();view=repo.load(group_id)
        if body.member_id not in {m.member_id for m in view.members}:raise ValueError('Unknown member')
        old=next((m for m in view.conversation if m.message_id==body.message_id),None)
        if old:
            if old.role!='user' or old.speaker_id!=body.member_id or old.text!=sanitize(body.text):raise RevisionConflict('Message ID reused')
        else:
            repo.append_message(group_id,Message(message_id=body.message_id,speaker_id=body.member_id,
                text=body.text,created_at=datetime.now(timezone.utc)),scope='PUBLIC')
        return stored_public(repo.load(group_id))
    except Exception as exc:return failure(exc)


@router.post('/api/kickoff/groups/{group_id}/coach')
def coach(group_id:str,body:CoachInput):
    # Same action retries reuse the saved result, even if new chat arrived later.
    with _guard:lock=_locks.setdefault((group_id,body.action_id),threading.RLock())
    with lock:
        try:
            repo=repository();prior=repo.find_action(group_id,body.action_id)
            if prior:
                if prior.module!='A':raise RevisionConflict('Action belongs to another module')
                result=prior.response
            else:
                view=repo.load(group_id)
                recent=next((a for a in reversed(view.artifacts) if a.module=='A' and a.response.get('status')=='OK'),None)
                user_messages=lambda messages:[m for m in messages if m.get('role')=='user']
                if not body.fresh and recent and reusable_result(recent.response,'A') and recent.request.get('members')==[m.model_dump() for m in view.members] and user_messages(recent.request.get('conversation',[]))==user_messages([m.model_dump(mode='json') for m in view.conversation]):
                    # A later transient failure must not remain the visible result
                    # after a successful retry reused an already valid response.
                    last=next((a for a in reversed(view.artifacts) if a.module=='A'),None)
                    if last and last.artifact_id!=recent.artifact_id:
                        reused=deepcopy(recent.response);meta=reused['meta']
                        meta.update(source_run_id=meta['run_id'],run_id=str(uuid4()),cached_usage=deepcopy(meta['usage']),
                            usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},execution_source='CACHE',
                            cache_hit=True,transport_attempts=0,repair_attempts=0,provider_request_ids=[],latency_ms=0)
                        response=saved_call('A',{'record_id':group_id,'action_id':body.action_id,
                            'expected_revision':view.revision,'input':recent.request},lambda:reused)
                        if isinstance(response,JSONResponse) and response.status_code!=200:return response
                        reused=json.loads(response.body) if isinstance(response,JSONResponse) else response
                        return sanitize({'result':reused,'group':public_view(repo.load(group_id)),'reused':True})
                    return sanitize({'result':recent.response,'group':public_view(view),'reused':True})
                request=CoachRequest(conversation=view.conversation[-200:],members=view.members,
                    reference_date=view.reference_date,timezone=view.timezone)
                def invoke():
                    result=coach_reply(request,runtime=runtime_for(group_id),fresh=body.fresh)
                    if len(view.conversation)>200:result.warnings.append('최근 200개 메시지를 참고했습니다. 전체 대화는 저장돼 있습니다.')
                    return result.model_dump(mode='json')
                response=saved_call('A',{'record_id':group_id,'action_id':body.action_id,
                    'expected_revision':view.revision,'input':request.model_dump(mode='json')},invoke)
                if isinstance(response,JSONResponse) and response.status_code!=200:return response
                result=json.loads(response.body) if isinstance(response,JSONResponse) else response
            # AI replies belong to the public coaching panel, not a member's private history.
            if result.get('status')=='OK' and not result.get('meta',{}).get('cache_hit'):
                artifact=repo.find_action(group_id,body.action_id)
                repo.append_message(group_id,Message(message_id='coach:'+result['meta']['run_id'],
                    role='assistant',text=result['data']['reply'],created_at=artifact.created_at),scope='PUBLIC')
            return sanitize({'result':result,'group':public_view(repo.load(group_id))})
        except Exception as exc:return failure(exc)
