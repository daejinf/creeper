"""Independent, zero-network developer acceptance audit. Never modifies product code.

The agreed safety gate blocks the planned LIVE phase when critical checks fail.
Findings are observations, not product patches or assertions that fake HTTP is LIVE.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import requests
from pydantic import ValidationError
from wemeet_ai import GoalAI, __version__
from wemeet_ai import contracts
from wemeet_ai.api import *
from wemeet_ai.client import RunStats
from wemeet_ai.analysis import merge_analysis
from wemeet_ai.grounding import constrain_grounded, constrain_assisted
from wemeet_ai.draft_validation import normalize_goal_draft
from wemeet_ai.scenarios import all_scenarios, audit_scenarios, DOMAINS, DATA_VERSION
from wemeet_ai.validation import validate_plan as check_plan

ROOT=Path(__file__).resolve().parents[1]


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def product_digest():
    files=sorted((ROOT/'src').rglob('*.py'))
    return hashlib.sha256(b''.join(str(p.relative_to(ROOT)).encode()+p.read_bytes() for p in files)).hexdigest()


def source(deadline='2026-11-15'):
    return AnalysisRequest(members=[Member(member_id='m1',nickname='민지')],reference_date=date(2026,10,29),conversation=[
        Message(message_id='u1',speaker_id='m1',role='user',created_at='2026-10-29T12:00:00+09:00',
            text=f'공동 소개 페이지를 2026-11-01부터 {deadline}까지 만들자. 소개와 가입 안내를 시연하고 사진으로 인증하자.')])


def analysis(request,deadline='2026-11-15'):
    m=request.conversation[-1]
    fields={name:contracts.AnalysisField(status='EXTRACTED',candidates=[contracts.Candidate(candidate_id=name,
        value=value,original_text=m.text,evidence_refs=[m.message_id],proposer_ids=['m1'])])
        for name,value in {'goal':{'title':'공동 소개 페이지','expected_outcome':'소개·가입 안내 시연','execution_method':'화면 제작 후 연결 점검'},
            'timeline':{'start_date':'2026-11-01','end_date':deadline},'success':{'criteria':['소개와 가입 안내가 시연된다.']}}.items()}
    return ConversationAnalysis(summary='공동 소개 페이지',fields=fields)


def draft(deadline='2026-11-15',accepted=False):
    values={'goal':'공동 소개 페이지','expected_outcome':'소개·가입 안내 시연','execution_method':'화면 제작 후 연결 점검',
        'completion_criteria':['소개와 가입 안내가 시연된다.'],'start_date':'2026-11-01','end_date':deadline,
        'verification_method':'사진','privacy_scope':'OBJECTIVE_ONLY'}
    return GoalSpecDraft(summary='작성된 평가 fixture',fields={k:DraftField(value=v,origin='AI_SUGGESTED',
        review_status='ACCEPTED' if accepted else 'PENDING',reason='평가용 기준값') for k,v in values.items()})


class StructuredFixture:
    model='INJECTED_FIXTURE_NOT_LIVE';base_url='offline://developer-audit'
    def __init__(self,*outputs):self.outputs=list(outputs);self.calls=0
    def complete_json(self,messages,output_schema,options=None,*,stats):
        self.calls+=1;value=self.outputs.pop(0)
        if isinstance(value,Exception):raise value
        stats.actual_model=self.model
        return SimpleNamespace(parsed_output=output_schema.model_validate(value.model_dump()))


class FakeResponse:
    def __init__(self,content='답변',status=200,finish='stop',usage=None):
        self.status_code=status;self.content=content;self.finish=finish;self.usage=usage
    def json(self):
        return {'id':'fixture-provider-id','model':'FIXTURE','choices':[{'message':{'content':self.content},'finish_reason':self.finish}],
            'usage':self.usage if self.usage is not None else {'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}


class FakeSession:
    def __init__(self,*responses):self.responses=list(responses);self.calls=0;self.guard=threading.Lock()
    def post(self,*args,**kwargs):
        with self.guard:
            self.calls+=1;value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value


@contextmanager
def fake_credential():
    old=os.environ.get('DEEPSEEK_API_KEY');os.environ['DEEPSEEK_API_KEY']='developer-audit-fixture-token'
    try:yield
    finally:
        if old is None:os.environ.pop('DEEPSEEK_API_KEY',None)
        else:os.environ['DEEPSEEK_API_KEY']=old


def local_checks(output):
    checks=[]
    def record(name,passed,actual,expected,priority='P2',critical=False):
        entry={'id':name,'passed':bool(passed),'expected':expected,'actual':actual,'priority':priority,'critical':critical,'source':'INJECTED_FIXTURE / CODE','new_network_requests':0}
        checks.append(entry);save(output/'evidence'/(name+'.json'),entry)
    # Exercise the PUBLIC facade, not just a private field normalizer.
    original=source();old_analysis=merge_analysis(original,analysis(original));previous=draft(accepted=True)
    state=GoalSetupState(**original.model_dump(),analysis=old_analysis,draft=previous)
    changed=source('2026-11-22');model=analysis(changed,'2026-11-22')
    fixture=StructuredFixture(model,draft('2026-11-22'));ai=GoalAI(fixture,execution_mode='REPLAY')
    review=ai.advance_goal_setup(GoalSetupRequest(action='REVIEW',state=state,conversation=changed.conversation))
    assert review.status=='OK',review.model_dump()
    confirmed=ai.advance_goal_setup(GoalSetupRequest(action='CONFIRM',state=review.data.state,actor_member_id='m1'))
    actual={'new_dialogue_deadline':'2026-11-22','returned_deadline':review.data.state.draft.fields['end_date'].value,
        'review_status':review.data.state.draft.fields['end_date'].review_status,'can_generate_group_plan':review.data.can_generate_group_plan,
        'needed_inputs':review.data.needed_inputs,'confirm_status':confirmed.status,
        'confirmed_deadline':str(confirmed.data.group_spec.end_date) if confirmed.data.group_spec else None}
    record('M1-STATE-001',not review.data.can_generate_group_plan and confirmed.status=='NEEDS_INPUT',actual,
        '새 대화와 이전 확인값 충돌은 재검토 대상이며 이전 승인으로 확정하지 않는다.','P0',True)
    # An accepted-card alternative must not make a paid call just to return the old value.
    fixture=StructuredFixture(draft('2026-11-22'));out=GoalAI(fixture,execution_mode='REPLAY').advance_goal_setup(
        GoalSetupRequest(action='RESPOND',state=state,actor_member_id='m1',response=GoalSetupResponse(operation='ALTERNATIVE',fields=['end_date'])))
    after=out.data.state.draft.fields['end_date'] if out.data else None
    record('M1-ALTERNATIVE-002',fixture.calls==0 or bool(after and after.value!=previous.fields['end_date'].value),
        {'status':out.status,'injected_model_calls':fixture.calls,'old_value':previous.fields['end_date'].value,
         'returned_value':after.value if after else None,'returned_review_status':after.review_status if after else None},
        '수락 항목의 다른 제안은 사전에 재검토 필요로 안내하거나 새 미확인 제안을 반환한다.','P2')
    # A failed C must keep the successful B and only resume C.
    f=StructuredFixture(analysis(original),AIError('TIMEOUT','injected C timeout',True),draft())
    ai=GoalAI(f,execution_mode='REPLAY');first=ai.advance_goal_setup(GoalSetupRequest(action='REVIEW',**original.model_dump()))
    before=f.calls;resumed=ai.advance_goal_setup(GoalSetupRequest(action='REVIEW',state=first.data.state))
    record('M1-RECOVERY-003',first.status=='FAILED' and resumed.status=='OK' and f.calls-before==1,
        {'first_status':first.status,'analysis_preserved':first.data.state.analysis is not None,'resume_status':resumed.status,'resume_fixture_calls':f.calls-before},
        'B 성공 C 실패 시 B를 보존하고 C만 재실행한다.','P1')
    # Full public-output privacy rule: semantically equivalent private reason is still private.
    group_record=json.loads((ROOT/'runs/v101-e2e-release/group/08-group-resumed.json').read_text(encoding='utf-8'))
    person_record=json.loads((ROOT/'runs/v101-e2e-release/private/design/11-member-plan.json').read_text(encoding='utf-8'))
    spec=GroupGoalSpec.model_validate(group_record['request']);group=GroupPlan.model_validate(group_record['response']['data'])
    member=MemberPlanDraft.model_validate(person_record['response']['data']);profile=MemberProfile.model_validate(person_record['request']['member_profile'])
    profile.constraints=['매주 화요일 가족 돌봄으로 참여 불가']
    member.public_plan.public_rationale='화요일마다 가족을 돌보느라 시간을 낼 수 없어 분량을 줄였습니다.'
    privacy=check_plan(spec,group,[member],require_all_members=False,member_profiles=[profile])
    record('M1-PRIVACY-004',not privacy.valid,
        {'validator_valid':privacy.valid,'issues':[i.model_dump() for i in privacy.issues],
         'private_input':profile.constraints,'injected_public_rationale':member.public_plan.public_rationale,
         'exact_substring_present':profile.constraints[0] in member.public_plan.model_dump_json()},
        '같은 비공개 사유를 바꿔 쓴 공개 결과도 검토/반려되어야 한다. 실제 사용자 노출 사건이 아닌 합성 결과 주입 검사.','P1',True)
    # Shared GoalAI instance: deterministic barrier forces metadata overlap.
    barrier=threading.Barrier(2)
    class BarrierClient:
        model='INJECTED_FIXTURE_NOT_LIVE';base_url='offline://concurrency'
        def complete_text(self,messages,options=None,*,stats):
            barrier.wait(timeout=10);stats.actual_model=self.model
            return SimpleNamespace(content='공동 결과물을 작게 나누어 시작하는 방법을 추천합니다.')
    ai=GoalAI(BarrierClient(),reference_provider=lambda task,payload:[{'case_id':payload['conversation'][0]['message_id'],'style':'평가용 참고'}],execution_mode='REPLAY')
    def concurrent(index):
        request=source();request.conversation[0].message_id='reference-'+str(index)
        result=ai.coach_reply(contracts.CoachRequest(**request.model_dump(),coaching_mode='DIALOGUE'))
        return {'expected_reference':request.conversation[0].message_id,'actual_references':result.meta.reference_case_ids,'status':result.status}
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(concurrent,[1,2]))
    record('M0-CONCURRENCY-005',all(r['actual_references']==[r['expected_reference']] for r in results),results,
        '동일 GoalAI를 동시 사용할 때 실행별 참고 사례 메타데이터가 섞이지 않거나 공유 불가 계약을 명시한다.','P1')
    # Independent retry/error matrix through M0, never real transport.
    with fake_credential():
        matrix=[('401',[FakeResponse(status=401)],'AUTH_FAILED',1),('403',[FakeResponse(status=403)],'AUTH_FAILED',1),
            ('429-recover',[FakeResponse(status=429),FakeResponse()],None,2),('500-recover',[FakeResponse(status=500),FakeResponse()],None,2),
            ('timeout-recover',[requests.Timeout(),FakeResponse()],None,2),('empty',[FakeResponse('')],'EMPTY_RESPONSE',1),
            ('truncated',[FakeResponse(finish='length')],'TRUNCATED_OUTPUT',1),('bad-usage',[FakeResponse(usage={'total_tokens':-1})],'INVALID_JSON',1)]
        for name,responses,error,count in matrix:
            session=FakeSession(*responses);client=DeepSeekClient(session,sleeper=lambda _:None);code=None
            try:client.complete_text([{'role':'user','content':'fixture'}])
            except AIError as exc:code=exc.code
            record('M0-TRANSPORT-'+name,code==error and session.calls==count,{'error':code,'injected_transport_attempts':session.calls}, {'error':error,'attempts':count},'P1')
        # Repair usage: the direct public client result and explicit aggregate stats differ.
        good=draft().model_dump_json();session=FakeSession(FakeResponse('{bad'),FakeResponse(good));stats=RunStats()
        completion=DeepSeekClient(session,sleeper=lambda _:None).complete_json([{'role':'user','content':'fixture'}],GoalSpecDraft,stats=stats)
        record('M0-DIRECT-METRICS-006',completion.transport_attempts==stats.transport_attempts and completion.usage.total_tokens==stats.usage.total_tokens,
            {'completion_attempts':completion.transport_attempts,'aggregate_attempts':stats.transport_attempts,
             'completion_tokens':completion.usage.total_tokens,'aggregate_tokens':stats.usage.total_tokens},
            '직접 client 결과가 단일 응답인지 전체 JSON 보정 실행인지 계약을 명확히 하고 실제 사용량을 빠짐없이 제공한다.','P2')
        # Same-key concurrent invocations with separate request instances coalesce.
        session=FakeSession(FakeResponse('공동 작업을 작은 결과물로 나누세요.'));cache=MemoryResultCache();transport=DeepSeekClient(session,sleeper=lambda _:None)
        def cached(_):return GoalAI(transport,cache_backend=cache,cache_scope='same-request',execution_mode='REPLAY').coach_reply(contracts.CoachRequest(**source().model_dump(),coaching_mode='DIALOGUE'))
        with ThreadPoolExecutor(4) as pool:cached_results=list(pool.map(cached,range(4)))
        record('M0-CACHE-007',session.calls==1 and sum(r.meta.cache_hit for r in cached_results)==3,
            {'injected_transport_attempts':session.calls,'cache_hits':sum(r.meta.cache_hit for r in cached_results)},'같은 범위·입력 동시4건은 가짜 전송1회와 캐시3건이어야 한다.','P1')
        # Returned data, detailed trace and cached payload must never contain the configured secret.
        secret=os.environ['DEEPSEEK_API_KEY'];sink=MemoryTraceSink();session=FakeSession(FakeResponse('응답 '+secret));cache=MemoryResultCache()
        result=GoalAI(DeepSeekClient(session,sleeper=lambda _:None),trace_sink=sink,cache_backend=cache,cache_scope='secrets',execution_mode='REPLAY').coach_reply(contracts.CoachRequest(**source().model_dump(),coaching_mode='DIALOGUE'))
        serialized=json.dumps([result.model_dump(mode='json'),sink.events,cache.values],ensure_ascii=False,default=str)
        record('M0-SECRET-008',secret not in serialized,{'configured_secret_in_outputs':secret in serialized},'구성 키가 응답·추적·캐시에서 제거되어야 한다.','P0',True)
    return checks


def freeze_manifest(output):
    frozen=output/'live-manifest.json'
    if frozen.exists():
        saved=json.loads(frozen.read_text(encoding='utf-8'))
        if saved['source_sha256']!=product_digest():raise RuntimeError('제품 코드가 달라졌습니다. 새 평가 폴더를 사용하세요.')
        return saved
    cases=list(all_scenarios());by_domain={d:sorted([c for c in cases if c.domain==d],key=lambda c:c.case_id) for d in DOMAINS};rows=[];inputs=[]
    modules=['A','B','C','E','FLOW']
    for index,(domain,pool) in enumerate(by_domain.items()):
        pairs=[1,2,3,4,9];thirds=[7,6,8,5,10]
        remaining=[[1,4,8],[2,5,7],[3,6,9],[4,7,10],[5,8,10]]
        numbers=([pairs[index],pairs[index],thirds[index]] if index<5 else remaining[index-5])
        selected=[]
        for j,number in enumerate(numbers):
            kind='VARIANT' if j==1 else 'BASE'
            selected.append(next(c for c in pool if c.family_id.endswith('-'+str(number).zfill(2)) and c.kind==kind))
        base,variant,third=selected
        for j,case in enumerate([base,variant,third]):
            module=(modules[index] if j<2 else modules[(index+1)%5]) if index<5 else modules[(index-5+j)%5]
            model_input={'group_input':case.group_input.model_dump(mode='json'),'private_conversations':case.private_conversations,
                'member_profiles': [p.model_dump(mode='json') for p in case.profiles]}
            # This envelope is NOT sent wholesale to an LLM. It describes separately scoped module inputs.
            envelope={'evaluation_id':f'{domain}-{j+1}','module':module,'scoped_source_inputs':model_input,
                'prior_stage_fixture':{'label':'AUTHORED_NOT_LIVE','confirmed_conditions':case.confirmed_fixture.model_dump(mode='json')},
                'judge_only':{'expected':case.expected,'forbidden':case.expected.get('forbidden',[]),'comparison':case.comparison}}
            digest=hashlib.sha256(json.dumps(envelope,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            rows.append({'evaluation_id':envelope['evaluation_id'],'source_case_id':case.case_id,'original_entry_module':case.entry_module,'module':module,
                'domain':domain,'input_sha256':digest,'pair_id':base.family_id if index<5 and j<2 else None,'status':'NOT_RUN_LOCAL_GATE',
                'scores':{name:None for name in ['facts','usefulness','question_quality','feasibility','privacy']}})
            inputs.append(envelope)
    assert Counter(r['module'] for r in rows)==Counter({m:6 for m in modules}) and len(rows)==30
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'package_version':__version__,'data_version':DATA_VERSION,'source_sha256':product_digest(),
        'live_policy':{'max_actual_http':120,'batch_size':5,'concurrency':1,'cache':False,'reference_injection':False,
            'count_retries_and_repairs':True,'stop_before_transport_if_local_critical':True},'selection_revision':2,'coverage_tags':sorted({t for r in rows for t in next(c for c in cases if c.case_id==r['source_case_id']).tags}),'cases':rows}
    save(output/'live-manifest.json',manifest);save(output/'live-inputs-and-judge-expectations.json',inputs)
    return manifest


def saved_recheck(output):
    records=[];sources=[]
    for path in sorted((ROOT/'runs').glob('*.sqlite3')):
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        before=[]
        if 'eval_results' in tables:
            for rid,case_id,status,response,metrics in db.execute('SELECT id,case_id,status,response,metrics FROM eval_results ORDER BY id'):
                if json.loads(metrics).get('http_attempts',0)<=0:continue
                before.append((rid,response));item={'db':path.name,'record_id':rid,'case_id':case_id,'original_status':status,'source':'STORED_LIVE_PARSED','runs':[]}
                try:
                    obj=json.loads(response);case=next(c for c in all_scenarios() if c.case_id==case_id);stored_group=None;stored_spec=None
                    for run in obj.get('runs',[]):
                        task=run.get('meta',{}).get('task');data=run.get('data');result={'task':task,'original_status':run.get('status')}
                        if not data:result['recheck']='NO_PARSED_BODY'
                        else:
                            types={'coach':'CoachReply','coach-proactive':'CoachReply','coach-dialogue':'CoachReply','analyze':'ConversationAnalysis',
                                'draft':'GoalSpecDraft','member-goal-coach':'MemberCoachReply','member-goal-draft':'MemberGoalDraft','group-plan':'GroupPlan','member-plan':'MemberPlanDraft','prepare':'GoalSpec'}
                            schema=getattr(contracts,types.get(task,''),None)
                            if schema is None:result['recheck']='NO_COMPATIBLE_SCHEMA_MAPPING'
                            else:
                                try:
                                    parsed=schema.model_validate(data);result['recheck']='SCHEMA_COMPATIBLE'
                                    if task=='analyze':
                                        merged=merge_analysis(case.group_input,parsed);result['recheck']='ANALYSIS_REPLAY_VALID';result['privacy_status']=merged.fields['privacy'].status
                                    elif task=='draft':
                                        normalized=normalize_goal_draft(parsed,case.group_input.members)
                                        result['field_errors']={k:f.validation_errors for k,f in normalized.fields.items() if f.validation_errors}
                                        result['recheck']='DRAFT_FIELD_CHECK_FAILED' if result['field_errors'] else 'DRAFT_FIELD_CHECK_VALID'
                                    elif task=='group-plan':
                                        from wemeet_ai.scenario_inputs import authored_spec
                                        stored_spec=authored_spec(case);stored_spec.spec_id=parsed.spec_id;stored_group=parsed
                                        checked=check_plan(stored_spec,stored_group,[],require_all_members=False)
                                        result['recheck']='GROUP_PLAN_CODE_VALID' if checked.valid else 'GROUP_PLAN_CODE_FAILED'
                                        result['issue_codes']=[i.code for i in checked.issues];result['prerequisite']='AUTHORED_SAME_CASE_WITH_STORED_SPEC_ID'
                                    elif task=='member-plan' and stored_group is not None:
                                        checked=check_plan(stored_spec,stored_group,[parsed],require_all_members=False)
                                        result['recheck']='MEMBER_PLAN_CODE_VALID' if checked.valid else 'MEMBER_PLAN_CODE_FAILED'
                                        result['issue_codes']=[i.code for i in checked.issues];result['prerequisite']='STORED_GROUP_PLAN_AND_AUTHORED_MEMBER_PROFILE'
                                except ValidationError:result['recheck']='SCHEMA_INCOMPATIBLE'
                                except Exception as exc:result['recheck']='REPLAY_CHECK_FAILED';result['error_type']=type(exc).__name__
                        item['runs'].append(result)
                except Exception as exc:item['record_error']=type(exc).__name__
                records.append(item)
        if 'ai_result_cache' in tables:
            for key,payload in db.execute('SELECT key,payload FROM ai_result_cache ORDER BY key'):
                before.append((key,payload));obj=json.loads(payload);run=obj.get('result',{});meta=run.get('meta',{})
                if meta.get('execution_source')!='LIVE' and meta.get('source_execution_source')!='LIVE':continue
                schema=getattr(contracts,obj.get('data_type',''),None);item={'db':path.name,'record_id':hashlib.sha256(key.encode()).hexdigest()[:16],
                    'source':'STORED_LIVE_CACHE_PARSED','runs':[{'task':meta.get('task'),'original_status':run.get('status')}]}
                try:
                    if schema is None:item['runs'][0]['recheck']='NO_COMPATIBLE_SCHEMA_MAPPING'
                    elif run.get('data') is None:item['runs'][0]['recheck']='NO_PARSED_BODY'
                    else:schema.model_validate(run['data']);item['runs'][0]['recheck']='SCHEMA_COMPATIBLE_INPUT_NOT_RECONSTRUCTED'
                except ValidationError:item['runs'][0]['recheck']='SCHEMA_INCOMPATIBLE'
                records.append(item)
        # Compare only the original immutable evaluation/cache rows, not new user activity.
        changed=[]
        for identity,body in before:
            table,column=('eval_results','response') if 'eval_results' in tables and db.execute('SELECT 1 FROM eval_results WHERE id=?',(identity,)).fetchone() else ('ai_result_cache','payload')
            row=db.execute(f'SELECT {column} FROM {table} WHERE '+('id' if table=='eval_results' else 'key')+'=?',(identity,)).fetchone()
            if row is None or row[0]!=body:changed.append(hashlib.sha256(identity.encode()).hexdigest()[:16])
        sources.append({'db':path.name,'read_only':True,'original_rows_checked':len(before),'changed_original_rows':changed,
            'run_meta_rows':db.execute('SELECT count(*) FROM runs').fetchone()[0] if 'runs' in tables else 0,
            'raw_provider_body_available_in_db':False})
        db.close()
    counts=Counter(r['recheck'] for item in records for r in item['runs'] if 'recheck' in r)
    report={'source':'READ_ONLY_STORED_OUTPUT_RECHECK','new_http_requests':0,'new_tokens':0,'records':len(records),'run_classification':dict(counts),
        'sources':sources,'items':records,'limitation':'스키마 호환은 의미 품질 통과가 아닙니다. 입력이 복원되지 않은 캐시와 원문 없는 실패를 의미 재평가 성공으로 집계하지 않습니다. 같은 LIVE 실행의 DB/캐시 표현은 별도 저장 행이며 고유 시나리오 수가 아닙니다.'}
    save(output/'stored-recheck.json',report);return report


def run(output):
    output=Path(output);before=product_digest();cases=list(all_scenarios())
    manifest=freeze_manifest(output);audit=audit_scenarios(cases);save(output/'data-audit.json',audit)
    checks=local_checks(output);save(output/'local-checks.json',checks)
    stored=saved_recheck(output)
    blockers=[r['id'] for r in checks if not r['passed'] and r['critical']]
    assert product_digest()==before,'Product source changed during evaluation'
    report={'evaluation_version':'developer-acceptance-v2','package_version':__version__,'product_source_sha256':before,'product_code_unchanged':True,
        'local_checks':{'executed':len(checks),'passed':sum(r['passed'] for r in checks),'failed':sum(not r['passed'] for r in checks)},
        'data_scenarios':len(cases),'stored_records':stored['records'],'critical_blockers':blockers,
        'live':{'planned':len(manifest['cases']),'executed':0,'http_attempts':0,'tokens':0,'status':'BLOCKED_LOCAL_CRITICAL' if blockers else 'READY_NOT_STARTED'},
        'semantic_mean_score':None,'human_reviews':0,'verdict':'HOLD' if blockers else 'PENDING_LIVE','findings':[r for r in checks if not r['passed']]}
    import xml.etree.ElementTree as ET
    baseline=ROOT/'runs/developer-evaluation-baseline.xml'
    if baseline.exists():
        suites=list(ET.parse(baseline).getroot().iter('testsuite'))
        report['baseline_tests']={k:sum(int(s.get(k,'0')) for s in suites) for k in ['tests','failures','errors','skipped']}
    sw=output/'sw-mock/report.json'
    if sw.exists():
        observed=json.loads(sw.read_text(encoding='utf-8'))
        report['sw_simulation']={k:observed[k] for k in ['source','actual_http_requests','validation_passed','state_restored','duplicate_confirmation_stable','retry_member_ids','successful_member_plan_preserved','public_private_separation']}
        report['sw_simulation']['reviewer']='AGENT_MOCK_NOT_SW_TEAM'
        report['sw_simulation']['m2_output_excluded_from_evaluation']=True
    report['stored_classification']=stored['run_classification']
    report['evaluator_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save(output/'summary.json',report);print(json.dumps({k:v for k,v in report.items() if k!='findings'},ensure_ascii=True));return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(ROOT/'runs/developer-acceptance-v2'));run(p.parse_args().output)
