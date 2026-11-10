"""Optional record-to-request preparation. Pure Python: no DB, I/O or model calls.

SW may implement RecordStore using its own DB, or call A-F directly without records.
"""
from copy import deepcopy
import hashlib
import json
from datetime import date
from typing import Any, Literal, Protocol
from pydantic import Field, ValidationError, model_validator
from .shared.contracts import Contract, Message, Member

Scope = Literal['PUBLIC','PRIVATE','SERVICE']

class Artifact(Contract):
    artifact_id: str
    action_id: str
    module: str
    scope: Scope = 'PUBLIC'
    owner_member_id: str | None = None
    request: dict[str,Any] = Field(default_factory=dict)
    response: dict[str,Any] = Field(default_factory=dict)
    dependencies: dict[str,str] = Field(default_factory=dict)
    revision: int = 0
    created_at: str | None = None
    input_stale: bool = False

class RecordView(Contract):
    schema_version: Literal['1.0'] = '1.0'
    record_id: str
    title: str = '새 목표 대화'
    revision: int = 0
    members: list[Member] = Field(default_factory=list)
    reference_date: date = Field(default_factory=date.today)
    timezone: str = 'Asia/Seoul'
    conversation: list[Message] = Field(default_factory=list)
    owner_member_id: str | None = None
    private_conversation: list[Message] = Field(default_factory=list)
    inputs: dict[str,dict[str,Any]] = Field(default_factory=dict)
    input_dependencies: dict[str,dict[str,str]] = Field(default_factory=dict)
    pending: dict[str,str] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    selected_module: str = 'A'
    execution_mode: Literal['LIVE','FIXTURE'] = 'LIVE'
    selected_owner_member_id: str | None = None
    internal: bool = False
    service_source_tokens: dict[str,str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def scoped(self):
        if self.service_source_tokens and not self.internal:raise ValueError('Internal lineage in public/member view')
        if self.private_conversation and not self.owner_member_id: raise ValueError('Private owner required')
        if any(m.role=='user' and m.speaker_id!=self.owner_member_id for m in self.private_conversation):raise ValueError('Other member conversation')
        for a in self.artifacts:
            if a.scope=='SERVICE' and not self.internal:raise ValueError('Service artifact in public/member view')
            if a.scope=='PRIVATE' and a.owner_member_id!=self.owner_member_id and not self.internal:raise ValueError('Other member artifact')
            if a.scope=='PUBLIC' and a.owner_member_id:raise ValueError('Public artifact has private owner')
        return self

class PreparedInput(Contract):
    module: str
    request: dict[str,Any] = Field(default_factory=dict)
    source_revision: int
    dependencies: dict[str,str] = Field(default_factory=dict)
    needed_inputs: list[str] = Field(default_factory=list)
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    ready: bool = False

class RecordStore(Protocol):
    """Authority is caller-owned. owner must come from SW authentication, not browser trust."""
    def load(self, record_id: str, *, owner_member_id: str | None = None, internal: bool = False) -> RecordView: ...
    def append_message(self, record_id: str, message: Message, *, scope: Scope, owner_member_id: str | None = None, expected_revision: int | None = None) -> int: ...
    def save_input(self, record_id: str, module: str, payload: dict, *, pending: str = '', expected_revision: int | None = None, metadata: dict | None = None, dependencies: dict | None = None) -> int: ...
    def save_artifact(self, record_id: str, artifact: Artifact, *, expected_revision: int | None = None) -> int: ...

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def artifact_key(module,scope='PUBLIC',owner=None):return 'artifact:'+module+':'+scope+':'+(owner or '')

def latest(view, modules, owner=None):
    modules={modules} if isinstance(modules,str) else set(modules)
    return next((a for a in reversed(view.artifacts) if a.module in modules and a.response.get('status')=='OK' and
        not (a.input_stale and a.module in {'C.confirm','E.contribution.confirm','E.review','C.review'}) and
        (not a.owner_member_id or a.owner_member_id==owner)),None)


def planning_dependencies(module, dependencies):
    """Approved conditions, not incidental chat, determine an executable plan."""
    if module=='D':return {k:v for k,v in dependencies.items() if k in {'confirmed_goal','members'} or k.startswith('input:D')}
    if module=='E.plan':return {k:v for k,v in dependencies.items() if k=='confirmed_goal' or k.startswith(('artifact:E.contribution.confirm:','input:E.plan:','private_user_messages:'))}
    if module in {'C.confirm','E.contribution.confirm'}:
        return {k:v for k,v in dependencies.items() if not k.startswith(('public_messages','public_user_messages'))}
    return dict(dependencies)


def reviewed_goal_state(view):
    draft=latest(view,{'C','C.review'});confirmed=latest(view,'C.confirm')
    draft_stale=bool(draft and (draft.input_stale or stale_reasons(view,draft.dependencies)))
    def decisions(value):
        return {k:{'value':v.get('value'),'review_status':v.get('review_status'),'conflicted':v.get('conflicted',False)} for k,v in (value or {}).get('fields',{}).items()}
    valid=bool(draft and confirmed and decisions(confirmed.request.get('draft'))==decisions(draft.response.get('data')))
    return {'draft':draft.response.get('data') if draft else None,'stale':draft_stale,
        'confirmed_goal':confirmed.response.get('data') if valid else None,'last_result':draft.response if draft else None}

def data(artifact):return artifact.response.get('data') or {} if artifact else {}

def goal_data(view):
    confirmed=latest(view,'C.confirm')
    if confirmed:
        draft=latest(view,{'C','C.review'})
        def values(raw):return {k:{'value':f.get('value'),'review_status':f.get('review_status'),'conflicted':f.get('conflicted',False)} for k,f in (raw or {}).get('fields',{}).items()}
        if draft and values(confirmed.request.get('draft'))!=values(data(draft)):return {},confirmed
        return data(confirmed),confirmed
    direct=view.inputs.get('D',{})
    if direct.get('confirmed'):return direct,None
    d=latest(view,'D')
    if d and d.request.get('confirmed'):return d.request,d
    return {},None

def input_key(module,payload):
    return 'input:'+module+(':'+str(payload.get('owner_member_id')) if module.startswith('E.') else '')

def source_tokens(view):
    tokens={'public_messages':fingerprint([m.model_dump(mode='json') for m in view.conversation]),
        'public_user_messages':fingerprint([m.model_dump(mode='json') for m in view.conversation if m.role=='user']),
        'members':fingerprint([m.model_dump(mode='json') for m in view.members])}
    if view.owner_member_id:
        tokens['private_messages:'+view.owner_member_id]=fingerprint([m.model_dump(mode='json') for m in view.private_conversation])
        tokens['private_user_messages:'+view.owner_member_id]=fingerprint([m.model_dump(mode='json') for m in view.private_conversation if m.role=='user'])
    for module,payload in view.inputs.items():tokens[input_key(module,payload)]=input_signature(module,payload)
    if view.internal:tokens.update(view.service_source_tokens)
    for a in view.artifacts:
        if a.response.get('status')=='OK':tokens[artifact_key(a.module,a.scope,a.owner_member_id)]=fingerprint({'request':a.request,'data':data(a)})
    goal,_=goal_data(view);tokens['confirmed_goal']=fingerprint(goal)
    tokens['public_context']=fingerprint(view.inputs.get('A',{}).get('context',{}))
    return tokens

def input_signature(module,payload):
    payload=dict(payload)
    if module=='C':payload.pop('previous',None)
    if module in {'A','E.coach'}:
        payload['conversation']=[m for m in payload.get('conversation',[]) if m.get('role')=='user']
        payload.pop('state',None);payload.pop('answer',None)
    return fingerprint(payload)

def stale_reasons(view, dependencies):
    tokens=source_tokens(view)
    return [key for key,version in dependencies.items() if tokens.get(key)!=version]

def prepare_module_input(module: str, record_view: RecordView, overrides: dict | None = None) -> PreparedInput:
    """Build only the requested input. It never executes an upstream module."""
    from .cli import registry
    view=record_view;module='E.coach' if module=='E' else module
    out=PreparedInput(module=module,source_revision=view.revision)
    if module not in registry():out.needed_inputs=['module: 지원하는 A~F 기능을 선택해주세요.'];return out
    tokens=source_tokens(view);deps={}
    def depend(key):deps[key]=tokens[key]
    def use(artifact):
        if artifact:
            deps.update(artifact.dependencies)
            key=artifact_key(artifact.module,artifact.scope,artifact.owner_member_id)
            if key in tokens:depend(key)
        return data(artifact)
    common={'members':[m.model_dump(mode='json') for m in view.members], 'reference_date':view.reference_date.isoformat(), 'timezone':view.timezone}
    goal,goal_artifact=goal_data(view)
    context={k:v for k,v in goal.items() if k in {'goal','start_date','end_date','completion_criteria','milestones','verification_method','privacy_scope'}}
    if view.owner_member_id:context['role']=goal.get('roles',{}).get(view.owner_member_id) or goal.get('role')
    if module in {'A','B'}:
        depend('public_user_messages' if module=='A' else 'public_messages');depend('members')
        payload={**common,'conversation':[m.model_dump(mode='json') for m in view.conversation]}
        if module=='A':
            payload['context']=context or view.inputs.get('A',{}).get('context',{})
            depend('confirmed_goal');depend('public_context')
    elif module=='C':
        a=latest(view,'B');analysis=use(a)
        payload={'facts':analysis.get('facts',{}),'conflicted_fields':analysis.get('conflicted_fields',[]),
            'members':common['members'],'reference_date':common['reference_date']}
        previous=latest(view,{'C','C.review'})
        if previous:payload['previous']=data(previous)
        depend('members')
    elif module=='D':
        payload=goal.copy();depend('confirmed_goal');use(goal_artifact)
    elif module.startswith('E.'):
        if not view.owner_member_id:out.needed_inputs=['owner_member_id: 본인을 선택해주세요.'];return out
        depend('confirmed_goal');use(goal_artifact);depend('private_user_messages:'+view.owner_member_id)
        if not context.get('goal'):
            # Independent E sessions also retain their own explicitly supplied public
            # context. They must not require a D/C record just to continue to E.draft.
            for candidate in dict.fromkeys([module,'E.coach','E.plan','E.draft']):
                saved=view.inputs.get(candidate,{})
                if saved.get('owner_member_id')==view.owner_member_id and saved.get('context',{}).get('goal'):
                    context=deepcopy(saved['context'])
                    key=input_key(candidate,saved)
                    if key in tokens:depend(key)
                    break

        state_artifact=next((a for a in reversed(view.artifacts) if a.module in {'E.coach','E.review'} and a.owner_member_id==view.owner_member_id and (data(a).get('state') or a.module=='E.review' and a.response.get('status')=='OK')),None)
        state_data=use(state_artifact) if module=='E.plan' else data(state_artifact)
        state=state_data.get('state',state_data)
        if module=='E.draft' and state_artifact:
            depend(artifact_key(state_artifact.module,state_artifact.scope,state_artifact.owner_member_id))
        if module in {'E.coach','E.draft'}:
            payload={'context':context,'owner_member_id':view.owner_member_id,
                'conversation':[m.model_dump(mode='json') for m in view.private_conversation],
                'reference_date':common['reference_date']}
            if state:payload['state']=state
        else:
            drafted=use(latest(view,'E.draft',view.owner_member_id))
            reviewed=view.inputs.get('E.plan',{})
            fields={**state.get('fields',{}),**drafted.get('profile_fields',{})}
            payload={'context':context,'owner_member_id':view.owner_member_id,'contribution':drafted.get('contribution',{'title':'','deliverable':'','completion_criteria':[],'confirmed':False}),
                'profile':{'current_level':fields.get('current_level',{}).get('value') or '',
                'weekly_available_minutes':fields.get('weekly_available_minutes',{}).get('value'),'confirmed':False}}
            if reviewed:
                for field in ('profile','contribution'):
                    if field in reviewed:payload[field]=deepcopy(reviewed[field])
                if reviewed.get('context')!=context:
                    payload['profile']['confirmed']=False
                    payload['contribution']['confirmed']=False
    else:
        group=latest(view,'D');member=latest(view,'E.plan',view.owner_member_id) if view.owner_member_id else None
        if member and not context.get('goal'):
            context=deepcopy(member.request.get('context',{}))
        if module=='F.revise':
            payload={'plan':use(member or group),'context':context,'feedback':''}
        else:
            payload={'context':context or None,'scope':'MEMBER' if member else 'GROUP'}
            if member:
                payload['member_plans']=[use(member)]
                minutes=member.request.get('profile',{}).get('weekly_available_minutes')
                if minutes:payload['weekly_budgets']={view.owner_member_id:minutes}
            elif group:payload['group_plan']=use(group)
    # Explicit SW overrides are first-class data and do not require prior execution.
    if overrides:
        payload.update(overrides)
        if module.startswith('E.') and payload.get('owner_member_id')!=view.owner_member_id:
            out.needed_inputs=['owner_member_id: 다른 사람의 개인 입력을 사용할 수 없습니다.'];return out
    out.request=payload;out.dependencies=deps
    out.stale_reasons=stale_reasons(view,deps);out.stale=bool(out.stale_reasons)
    if out.stale:out.needed_inputs.append('이전 기록 기준입니다. 변경된 근거를 검토하거나 최신 입력으로 수정해주세요.')
    try:
        typed=registry()[module][0].model_validate(payload)
        if module in {'A','B'} and not typed.conversation:out.needed_inputs.append('conversation: 대화를 입력해주세요.')
        if module=='C' and not any(f.value for f in typed.facts.values()) and not typed.previous:out.needed_inputs.append('facts.goal: 목표 설명이 필요합니다.')
        if module=='D':
            out.needed_inputs += [k for k in ['goal','start_date','end_date','completion_criteria','execution_method','verification_method','privacy_scope','members','confirmed'] if not getattr(typed,k)]
        if module in {'E.coach','E.draft','E.plan'} and not typed.context.goal:out.needed_inputs.append('context.goal: 공동 목표 설명이 필요합니다.')
        if module=='E.plan':
            if not typed.profile.current_level:out.needed_inputs.append('profile.current_level')
            if not typed.profile.weekly_available_minutes:out.needed_inputs.append('profile.weekly_available_minutes')
            if not typed.profile.confirmed:out.needed_inputs.append('profile.confirmed: 본인 확인 필요')
            if not typed.contribution.confirmed:out.needed_inputs.append('contribution.confirmed: 기여 목표 확인 필요')
            if not typed.contribution.completion_criteria:out.needed_inputs.append('contribution.completion_criteria')
        if module=='F' and not typed.group_plan and not typed.member_plans:out.needed_inputs.append('검사할 계획을 입력해주세요.')
    except ValidationError as exc:out.needed_inputs.extend('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors())
    out.ready=not out.needed_inputs
    return out

__all__=['Artifact','RecordView','RecordStore','PreparedInput','prepare_module_input','stale_reasons']
