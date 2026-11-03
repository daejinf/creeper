"""Stateless SW-facing orchestration. Persist returned state in the caller's repository."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from .client import AIError
from .contracts import (AnalysisRequest, CoachRequest, ConfirmedDecisions, DraftField,
    DraftRequest, GoalSetupOutput, GoalSetupState, GroupPrepareRequest, Meta, Result)
from .draft_validation import normalize_goal_draft, normalize_decision_field
from .grounding import source_catalog
from .tracing import active, emit

LABELS = dict(goal='공동 목표', expected_outcome='기대 결과', completion_criteria='완료 기준',
    start_date='시작일', end_date='최종 마감일', execution_method='수행 방법',
    verification_method='인증 방법', privacy_scope='공개 범위', custom_privacy_fields='공개 항목',
    frequency_per_week='주간 횟수', checkins='확인할 일정', milestones='주요 일정', roles='담당 역할', metric='수치 목표')
REQUIRED = {'goal','expected_outcome','completion_criteria','start_date','end_date',
    'execution_method','verification_method','privacy_scope'}


def advance(core, request):
    def operation(stats):
        from .service import GoalAI
        state = request.state.model_copy(deep=True) if request.state else GoalSetupState()
        for key in ('conversation','members','reference_date','timezone'):
            value=getattr(request,key)
            if value is not None:setattr(state,key,value)
        output=GoalSetupOutput(state=state)
        if not state.conversation or not state.members:
            output.needed_inputs=[{'field':'conversation','label':'공동 목표','reason':'함께 하고 싶은 일을 먼저 알려주세요.'}]
            output.allowed_actions=['COACH','REVIEW']
            return Result(status='NEEDS_INPUT',data=output,questions=['함께 하고 싶은 목표와 구성원을 알려주세요.'],meta=Meta(run_id='',task='',prompt_version=''))
        source=AnalysisRequest(conversation=state.conversation,members=state.members,reference_date=state.reference_date,timezone=state.timezone)
        fingerprint=hashlib.sha256(source.model_dump_json().encode()).hexdigest()
        stale=bool(state.source_fingerprint and state.source_fingerprint!=fingerprint)
        if stale:state.group_spec=None
        _,trace_id,parent,*_=active.get()
        child=GoalAI(core.client,core.recorder,trace_sink=core.trace_sink,trace_id=trace_id,parent_run_id=parent,
            prompt_variant=core.prompt_variant,reference_provider=core.reference_provider,cache_backend=core.cache_backend,
            cache_scope=core.cache_scope,force_refresh=core.force_refresh,execution_mode=core.execution_mode)
        def run(result):
            output.runs.append(result.meta)
            for name in ('prompt_tokens','completion_tokens','total_tokens'):
                setattr(stats.usage,name,getattr(stats.usage,name)+getattr(result.meta.usage,name))
            stats.transport_attempts+=result.meta.transport_attempts
            stats.repair_attempts+=result.meta.repair_attempts
            stats.actual_model=result.meta.model or stats.actual_model
            stats.request_ids.extend(result.meta.provider_request_ids)
            core.used_reference_ids.update(result.meta.reference_case_ids)
            return result
        failure=None
        if request.action=='COACH':
            coach_key=hashlib.sha256(json.dumps([m.model_dump(mode='json') for m in state.conversation if m.role=='user'],ensure_ascii=False,sort_keys=True).encode()+str(state.members).encode()+str(state.reference_date).encode()+state.timezone.encode()).hexdigest()
            if state.last_coach_input==coach_key and not core.force_refresh:
                output.reply=state.last_coach_reply;output.allowed_actions=['COACH','REVIEW']
                return output
            result=run(child.coach_reply(CoachRequest(**source.model_dump(),coaching_mode='DIALOGUE',coach_state=state.coach_state)))
            if result.status=='OK':
                from .contracts import Message
                output.reply=result.data.reply;state.coach_state=result.data.updated_coach_state;state.last_coach_input=coach_key;state.last_coach_reply=output.reply
                state.conversation.append(Message(message_id=str(uuid4()),role='assistant',text=output.reply,created_at=datetime.now(timezone.utc)))
            else:failure=result
        elif request.action=='REVIEW':
            needs_draft=state.draft is None or stale or state.source_fingerprint!=fingerprint or state.draft_source_fingerprint!=fingerprint or core.force_refresh
            if state.analysis is None or stale or state.source_fingerprint!=fingerprint:
                result=run(child.analyze_conversation(source))
                if result.status=='OK':
                    state.analysis=result.data;state.source_fingerprint=fingerprint
                else:failure=result
            if not failure and needs_draft:
                result=run(child.draft_goal_spec(DraftRequest(analysis=state.analysis,previous_draft=state.draft,
                    draft_mode='ASSISTED',reference_date=state.reference_date,timezone=state.timezone)))
                if result.status=='OK':state.draft=result.data;state.draft_source_fingerprint=fingerprint;state.group_spec=None
                else:failure=result
        elif stale or state.draft_source_fingerprint and state.draft_source_fingerprint!=fingerprint:
            output.needed_inputs=[{'field':'conversation','label':'변경된 대화','reason':'대화가 변경됐습니다. 검토 초안을 새 대화 기준으로 정리해주세요.'}]
            output.allowed_actions=['REVIEW']
            return Result(status='NEEDS_INPUT',data=output,questions=['최신 대화 기준으로 검토해주세요.'],meta=Meta(run_id='',task='',prompt_version=''))
        elif request.action=='RESPOND':
            response=request.response
            if not state.draft or not state.analysis or not response:raise AIError('SCHEMA_INVALID','검토 초안과 사용자 응답이 필요합니다.')
            if request.actor_member_id not in {m.member_id for m in state.members}:raise AIError('SCHEMA_INVALID','응답한 구성원을 지정해주세요.')
            keys=response.fields or list(response.values)
            if not keys or set(keys)-set(LABELS):raise AIError('SCHEMA_INVALID','응답할 검토 항목을 지정해주세요.')
            if response.operation=='ALTERNATIVE':
                result=run(child.draft_goal_spec(DraftRequest(analysis=state.analysis,previous_draft=state.draft,
                    draft_mode='SUGGEST',target_fields=keys,reference_date=state.reference_date,timezone=state.timezone)))
                if result.status=='OK':state.draft=result.data
                else:failure=result
            else:
                candidate=state.draft.model_copy(deep=True)
                for key in keys:
                    old=candidate.fields.setdefault(key,DraftField())
                    if response.operation=='ACCEPT':
                        if old.value not in (None,[],{}):old.review_status='ACCEPTED' if old.review_status!='EDITED' else 'EDITED'
                    else:
                        value=response.values.get(key,response.text if len(keys)==1 else None)
                        if response.operation=='ANSWER' and key in {'completion_criteria','checkins','custom_privacy_fields'} and isinstance(value,str):value=[v.strip() for v in value.splitlines() if v.strip()]
                        inferred_answer=False
                        if response.operation=='ANSWER' and key in {'start_date','end_date','frequency_per_week','privacy_scope'} and isinstance(value,str):
                            try:normalize_decision_field(key,value,[m.member_id for m in state.members])
                            except ValueError:
                                payload={'field':key,'text':value,'reference_date':state.reference_date.isoformat(),'timezone':state.timezone}
                                def interpret(answer_stats):
                                    return child._json('setup-answer',payload,DraftField,
                                        '사용자의 답변에서 지정 field 하나의 값만 정규화하세요. 날짜는 기준일의 상대 날짜를 ISO YYYY-MM-DD로, 공개 범위는 OBJECTIVE_ONLY/PRIVATE/CUSTOM으로, 주간 횟수는 정수로 표현합니다. 의미가 모호하거나 해석 불가능하면 value=null로 두고 필요한 설명을 reason에 적으세요. 다른 사실을 만들거나 빈칸을 추측하지 마세요. review_status=PENDING, origin=AI_SUGGESTED, evidence_refs=[]입니다.',answer_stats)
                                interpreted=run(child._run('setup-answer',payload,interpret))
                                if interpreted.status!='OK':
                                    return Result(status=interpreted.status,data=output,error=interpreted.error,questions=interpreted.questions,meta=Meta(run_id='',task='',prompt_version=''))
                                value=interpreted.data.value;inferred_answer=True
                        try:value=normalize_decision_field(key,value,[m.member_id for m in state.members])
                        except ValueError as exc:
                            output.needed_inputs=[{'field':key,'label':LABELS[key],'reason':str(exc),'submitted_value':value}]
                            output.allowed_actions=['RESPOND']
                            return Result(status='NEEDS_INPUT',data=output,questions=[str(exc)],meta=Meta(run_id='',task='',prompt_version=''))
                        candidate.fields[key]=DraftField(value=value,origin=('AI_SUGGESTED' if inferred_answer else 'EXTRACTED') if value is not None else 'UNRESOLVED',
                            review_status='PENDING' if inferred_answer else 'EDITED',reason='사용자 답변을 AI가 해석한 값입니다. 맞는지 확인해주세요.' if inferred_answer else '사용자가 이 검토 화면에서 직접 입력·확인한 내용입니다.')
                state.draft=normalize_goal_draft(candidate,state.members)
            if not failure:
                state.group_spec=None
                state.review_history.append({'operation':response.operation,'fields':keys,'actor_member_id':request.actor_member_id,
                    'timestamp':datetime.now(timezone.utc).isoformat(),'values':{k:state.draft.fields[k].value for k in keys}})
                emit('setup.user_response',state.review_history[-1])
        if state.draft and state.analysis:
            state.draft=normalize_goal_draft(state.draft,state.members)
            catalog=source_catalog(state.analysis)
            for key,field in state.draft.fields.items():
                required=key in REQUIRED or key=='custom_privacy_fields' and state.draft.fields.get('privacy_scope',DraftField()).value=='CUSTOM'
                missing=field.value in (None,[],{})
                actions=['ANSWER','ALTERNATIVE'] if missing else ['ACCEPT','EDIT','ALTERNATIVE']
                output.review_items.append({'field':key,'label':LABELS.get(key,key),'required':required,**field.model_dump(mode='json'),
                    'candidates':catalog.get(key,[]),'actions':actions})
                if field.validation_errors or required and missing or not missing and field.review_status=='PENDING':
                    output.needed_inputs.append({'field':key,'label':LABELS.get(key,key),'reason':' / '.join(field.validation_errors) if field.validation_errors else '이 항목을 알려주세요.' if missing else '추천 내용이 맞는지 확인해주세요.','actions':actions})
            if not output.needed_inputs:
                decisions={k:f.value for k,f in state.draft.fields.items() if f.value is not None}
                prepared=run(child.prepare_group_goal_spec(GroupPrepareRequest(analysis=state.analysis,
                    reviewed_draft=state.draft,confirmed_decisions=ConfirmedDecisions(confirmed=True,**decisions))))
                output.can_generate_group_plan=prepared.status=='OK'
                if request.action=='CONFIRM':
                    if request.actor_member_id not in {m.member_id for m in state.members}:raise AIError('SCHEMA_INVALID','확인한 구성원을 지정해주세요.')
                    if prepared.status=='OK':
                        if state.group_spec is None:
                            state.group_spec=prepared.data
                            state.review_history.append({'operation':'CONFIRM','actor_member_id':request.actor_member_id,'spec_id':state.group_spec.spec_id,'timestamp':datetime.now(timezone.utc).isoformat()})
                            emit('setup.confirmed',state.review_history[-1])
                        output.group_spec=state.group_spec
                if prepared.status!='OK':output.needed_inputs=[{'field':q.split(':')[0],'reason':q} for q in prepared.questions]
            if output.needed_inputs:output.allowed_actions=['RESPOND','REVIEW']
            else:output.allowed_actions=['CONFIRM','RESPOND','COACH']
        else:
            output.allowed_actions=['COACH','REVIEW']
            if request.action=='CONFIRM':output.needed_inputs=[{'field':'draft','label':'목표 검토','reason':'먼저 목표 검토 초안을 작성해주세요.'}]
        state.revision+=1
        if failure:return Result(status=failure.status,data=output,error=failure.error,questions=failure.questions,meta=Meta(run_id='',task='',prompt_version=''))
        status='NEEDS_INPUT' if request.action=='CONFIRM' and output.needed_inputs else 'OK'
        return Result(status=status,data=output,questions=[i.get('label',LABELS.get(i['field'],i['field']))+' · '+i['reason'] for i in output.needed_inputs] if status=='NEEDS_INPUT' else [],meta=Meta(run_id='',task='',prompt_version=''))
    return core._run('goal-setup',request,operation,uses_ai=False)
