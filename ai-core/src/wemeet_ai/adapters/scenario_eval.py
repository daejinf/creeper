"""Bounded, resumable LIVE evaluation; no answer keys are passed to providers."""
import hashlib
import json
import os
import threading
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4

from ..client import DeepSeekClient
from ..contracts import CoachRequest, MemberPlanRequest, Message
from ..scenarios import all_scenarios,get_scenario,DATA_VERSION
from ..scenario_inputs import entry_request
from ..references import select_references
from ..service import GoalAI
from .scenario_store import now
from .telemetry import PersistentTraceSink,provider_identity

ENGINE_VERSION='scenario-eval-v2-dialogue-grounded'

def engine_digest():
    root=Path(__file__).parents[1]
    return hashlib.sha256(b''.join((root/name).read_bytes() for name in ('service.py','personal.py','proactive.py','interactions.py','analysis.py','grounding.py','contracts.py','draft_validation.py','validation.py','scenario_inputs.py','references.py','adapters/scenario_eval.py'))).hexdigest()

def metric(results):
    return {'total_tokens':sum(r.meta.usage.total_tokens for r in results),'http_attempts':sum(r.meta.transport_attempts for r in results),'latency_ms':sum(r.meta.latency_ms for r in results)}

def execute_scenario(case,core):
    request=entry_request(case);results=[];checks={};payload=None
    if case.entry_module=='A':
        result=core.coach_reply(request);results.append(result)
        if result.status=='OK':checks={'natural_reply':bool(result.data.reply.strip()),'no_form_cards':not result.data.proposals,'no_auto_next_stage':result.data.next_action=='CONTINUE'}
    elif case.entry_module=='B':
        result=core.analyze_conversation(request);results.append(result)
        if result.status=='OK':
            f=result.data.fields;checks['frequency']=any(c.value.get('per_week')==case.expected['frequency_per_week'] for c in f.get('frequency',type('F',(),{'candidates':[]})()).candidates)
            if case.expected['privacy_scope'] is None:checks['missing_privacy_not_invented']=f.get('privacy') is None or f['privacy'].status=='MISSING'
            if case.expected['timeline_conflicted']:checks['conflicting_dates_preserved']=bool(f.get('timeline') and f['timeline'].status=='CONFLICTED' and len(f['timeline'].candidates)>=2)
    elif case.entry_module=='C':
        result=core.draft_goal_spec(request);results.append(result)
        if result.status=='OK':
            from ..grounding import source_catalog
            catalog=source_catalog(request.analysis)
            checks={'field_contracts':not any(f.validation_errors for f in result.data.fields.values()),'human_review_required':all(f.review_status=='PENDING' for f in result.data.fields.values()),'optional_metric_allowed':result.data.fields.get('metric') is not None,
                'no_ungrounded_values':all(f.value is None or any(o['value']==f.value for o in catalog.get(k,[])) for k,f in result.data.fields.items())}
    elif case.entry_module=='E':
        owner=case.profiles[0];sent_time=False
        for turn in range(6):
            result=core.coach_member_goal(request);results.append(result)
            if result.status!='OK':break
            request.state=result.data.state
            if request.state.current_question:
                field=request.state.current_question.target_field
                values={'current_level':owner.current_level,'weekly_available_minutes':owner.weekly_available_minutes,'preferred_times':owner.preferred_times,
                    'scope':case.expected['deliverable'],'contribution_candidate':case.expected['deliverable'],'current_progress':case.private_conversations[owner.member_id][2]['text']}
                text=(str(values[field]) if field in values else '이 항목은 아직 모르겠어요. 확인되지 않은 값으로 남겨주세요.')
                if field=='constraints':text='개인 사유는 설명하지 않고 건너뛸게요.'
                if field=='weekly_available_minutes':text=f'일주일에 {owner.weekly_available_minutes}분 가능해요.';sent_time=True
                question=request.state.current_question
                request.state.answered_question_ids.append(question.question_id);request.state.current_question=None
                request.conversation.append(Message(message_id=f'{case.case_id}-policy-{turn}',speaker_id=owner.member_id,role='user',text=text,created_at=case.group_input.conversation[0].created_at))
                # Policy marks explicit unknown/skip, rather than letting model infer false facts.
                if field not in values:
                    from ..contracts import DraftField
                    request.state.fields[field]=DraftField(value=None,evidence_refs=[request.conversation[-1].message_id]);request.state.answer_states[field]='SKIPPED' if field=='constraints' else 'UNKNOWN'
                request.continue_diagnosis=False
            elif request.state.awaiting_continue:request.continue_diagnosis=True
            else:break
        if result.status=='OK':
            state=result.data.state
            checks={'adaptive_dimensions':4<=len(state.diagnostic_dimensions)<=6,'question_budget':state.consecutive_questions<=2,'owner_scope':state.owner_member_id==owner.member_id}
            if sent_time:checks['time_matches_user']=bool(state.fields.get('weekly_available_minutes') and state.fields['weekly_available_minutes'].value==owner.weekly_available_minutes)
    else:
        group=core.generate_group_plan(request);results.append(group)
        if group.status=='OK':
            member=core.generate_member_plan(MemberPlanRequest(goal_spec=request,group_plan=group.data,member_profile=case.profiles[0]));results.append(member)
            if member.status=='OK':checks={'member_not_omitted':member.data.public_plan.member_id==case.profiles[0].member_id,'time_budget':all(w.estimated_minutes<=case.profiles[0].weekly_available_minutes for w in member.data.public_plan.weekly_targets),'task_ids':all(t.task_id for t in member.data.public_plan.first_week_todos)}
        result=results[-1]
    if case.entry_module!='E':
        public=[]
        for r in results:
            if r.status=='OK':public.append(r.data.public_plan.model_dump_json() if hasattr(r.data,'public_plan') else r.data.model_dump_json())
        checks['no_private_reason_in_public']=not any(secret in '\n'.join(public) for secret in case.expected['privacy_sentinels'])
    successful=bool(results) and all(r.status=='OK' for r in results)
    response={'label':'LIVE_RESULT','entry_module':case.entry_module,'prerequisite_source':'AUTHORED_TEST_FIXTURE' if case.entry_module in {'C','E','PLAN'} else 'SCENARIO_CONVERSATION','runs':[r.model_dump(mode='json',exclude={'traces'}) for r in results],
        'validation_events':[t for r in results for t in r.traces if t['event'].startswith(('validation.','plan.repair','json.repair'))]}
    return {'status':'DONE' if successful else 'FAILED','response':response,'checks':{'deterministic':checks,'passed':successful and bool(checks) and all(checks.values()),'semantic_quality':'NEEDS_HUMAN_REVIEW'},'metrics':metric(results)}

class EvaluationManager:
    def __init__(self,store,client=None):self.store=store;self.client=client or DeepSeekClient();self.lock=threading.Lock();self.threads={}
    def create(self,case_ids=None,configurations=None):
        cases=[get_scenario(c) for c in case_ids] if case_ids else list(all_scenarios());configs=configurations or ['baseline','improved']
        if not cases or not set(configs)<={'baseline','improved'} or len(configs)!=len(set(configs)):raise ValueError('평가할 사례와 설정을 확인해주세요.')
        approved=[k for k,v in self.store.reviews().items() if v['status']=='APPROVED' and get_scenario(k).split=='REFERENCE']
        spec={'data_version':DATA_VERSION,'engine_version':ENGINE_VERSION,'engine_digest':engine_digest(),'configurations':configs,'case_ids':[c.case_id for c in cases],'reference_pool':approved,'batch_size':5,'parallelism':1,'provider':provider_identity(self.client)}
        items=[]
        for case in cases:
            for config in configs:
                identity={'case':case.model_dump(mode='json'),'config':config,'engine':spec['engine_digest'],'provider':spec['provider'],'references':approved if config=='improved' else []}
                items.append({'case_id':case.case_id,'configuration':config,'fingerprint':hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()})
        return self.store.create_job(spec,items)
    def run_batch(self,job_id):
        completed=0
        job=self.store.job(job_id);pool=[get_scenario(k) for k in job['spec']['reference_pool']]
        if job['spec']['engine_digest']!=engine_digest():self.store.set_job(job_id,'PAUSED','평가 코드가 바뀌었습니다. 새 작업을 만들어 비교하세요.');return
        with self.store.connect() as db:
            # Recover interrupted attempts only after the lease has expired.
            db.execute("UPDATE eval_items SET status='PENDING' WHERE job_id=? AND status='RUNNING' AND lease_until<?",(job_id,now()))
            pending=db.execute("SELECT * FROM eval_items WHERE job_id=? AND status='PENDING' ORDER BY ordinal",(job_id,)).fetchall()
        case_ids=list(dict.fromkeys(r['case_id'] for r in pending))[:5]
        for item in pending:
            if item['case_id'] not in case_ids:continue
            if self.store.job(job_id,False)['status']!='RUNNING':return
            with self.store.connect() as db:
                cached=db.execute("SELECT id FROM eval_results WHERE fingerprint=? AND status='DONE' ORDER BY created_at DESC LIMIT 1",(item['fingerprint'],)).fetchone()
                if cached:
                    db.execute("UPDATE eval_items SET status='DONE',result_id=?,cached=1 WHERE id=?",(cached[0],item['id']));continue
                lease=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat()
                claim=db.execute("UPDATE eval_items SET status='RUNNING',lease_until=?,attempts=attempts+1 WHERE id=? AND status='PENDING'",(lease,item['id'])).rowcount
            if not claim:continue
            case=get_scenario(item['case_id']);configuration=item['configuration']
            reference_provider=(lambda task,payload:select_references(pool,task,payload,case.family_id)) if configuration=='improved' else None
            core=GoalAI(self.client,trace_sink=PersistentTraceSink(self.store),prompt_variant=configuration,reference_provider=reference_provider)
            try:outcome=execute_scenario(case,core)
            except Exception as exc:
                calls=list(core.trace_sink.calls.values())
                outcome={'status':'FAILED','response':{'label':'EXECUTION_EXCEPTION','error':type(exc).__name__,'message':str(exc)[:500]},'checks':{'passed':False,'semantic_quality':'NEEDS_HUMAN_REVIEW'},'metrics':{'total_tokens':sum(c.get('usage',{}).get('total_tokens',0) for c in calls),'http_attempts':len(calls),'latency_ms':0,'metrics_incomplete':True}}
            result_id=str(uuid4())
            with self.store.connect() as db:
                db.execute('INSERT INTO eval_results VALUES(?,?,?,?,?,?,?,?,?)',(result_id,item['fingerprint'],case.case_id,configuration,outcome['status'],json.dumps(outcome['response'],ensure_ascii=False),json.dumps(outcome['checks'],ensure_ascii=False),json.dumps(outcome['metrics']),now()))
                db.execute('UPDATE eval_items SET status=?,result_id=?,lease_until=NULL WHERE id=?',(outcome['status'],result_id,item['id']))
            completed+=1
            runs=outcome['response'].get('runs',[])
            if any((r.get('error') or {}).get('code') in {'AUTH_FAILED','RATE_LIMITED'} for r in runs):self.store.set_job(job_id,'PAUSED','인증·사용 한도를 확인한 뒤 재개하세요.');return
        current=self.store.job(job_id,False)
        if not current['counts']['PENDING'] and not current['counts']['RUNNING']:self.store.set_job(job_id,'COMPLETED','전체 조합 실행 종료. 품질 실패·사람 검토 수는 별도 확인하세요.')
    def start(self,key,automatic=False,retry_failed=False):
        if os.getenv('WEMEET_OFFLINE')=='1':raise ValueError('LIVE_CALLS_PAUSED: 추가 LIVE 호출 보류 중입니다.')
        with self.lock:
            if any(t.is_alive() for t in self.threads.values()):raise ValueError('다른 평가 배치가 실행 중입니다. 동시 실행은 1개입니다.')
            self.store.job(key)
            if retry_failed:
                with self.store.connect() as db:db.execute("UPDATE eval_items SET status='PENDING' WHERE job_id=? AND status='FAILED'",(key,))
            self.store.set_job(key,'RUNNING','전체 순차 실행' if automatic else '5개 사례 배치 실행')
            def work():
                try:
                    while self.store.job(key,False)['status']=='RUNNING':
                        self.run_batch(key)
                        if not automatic:
                            if self.store.job(key,False)['status']=='RUNNING':self.store.set_job(key,'PAUSED','5개 배치 완료 · 다음 배치 또는 전체 순차 실행을 선택하세요.')
                            break
                        job=self.store.job(key,False)
                        if job['counts']['RUNNING'] and not job['counts']['PENDING']:self.store.set_job(key,'PAUSED','이전 실행 복구 대기 · lease 만료 후 재개하세요.');break
                except Exception as exc:self.store.set_job(key,'PAUSED','실행 오류: '+type(exc).__name__)
            thread=threading.Thread(target=work,daemon=True,name='scenario-evaluation');self.threads[key]=thread;thread.start()
        return self.store.job(key,False)
    def pause(self,key):self.store.set_job(key,'PAUSED','사용자가 일시정지했습니다. 진행 중 호출은 결과를 저장한 뒤 멈춥니다.');return self.store.job(key,False)
