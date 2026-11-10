"""Four operational sessions using public core APIs and the optional record adapter.

--live is explicit. Initial attempts used the prior remainder; after user authorization,
all four sessions and their rechecks share a separate bounded HTTP ledger.
Auth/transport recovery is explicitly injected, never represented as provider traffic.
"""
import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluations.evaluate import Budget, digest, write
from wemeet_ai.cli import execute
from wemeet_ai.continuity import Artifact, prepare_module_input
from wemeet_ai.m0 import Runtime, TimelyClient, MemoryResultCache, MemoryTraceSink
from wemeet_ai.shared.contracts import Message, Member, ReviewField, Evidence
from wemeet_ai.m1.c_goal_draft import GoalDraft, review_field, confirm_goal
from tools.bench.store import SQLiteRecordStore, RevisionConflict

OUT = ROOT / 'runtime' / 'operations-4'
MEMBERS = [{'member_id':'design','nickname':'서연'}, {'member_id':'dev','nickname':'도윤'}, {'member_id':'pm','nickname':'민재'}]
GOAL = {'goal':'동아리 행사 신청 웹의 신청·관리 시연본 완성', 'start_date':'2026-11-05','end_date':'2026-11-22',
 'completion_criteria':['참가자가 신청하고 운영자가 신청 내역을 확인할 수 있음','모바일에서도 신청 완료까지 시연 가능'],
 'execution_method':'역할별 제작 후 공유 저장소에서 통합', 'verification_method':'매주 일요일 시연 영상과 짧은 설명',
 'privacy_scope':'OBJECTIVE_ONLY','roles':{'design':'신청 화면과 관리자 목록 UI','dev':'신청 API·저장·조회','pm':'시연 시나리오·발표자료'},
 'milestones':[{'title':'핵심 화면·API 통합','due_date':'2026-11-15'}, {'title':'개발 완료','due_date':'2026-11-19'}, {'title':'최종 시연 제출','due_date':'2026-11-22'}],
 'members':MEMBERS, 'confirmed':True, 'evidence':[{'source':'SW_CONFIRMED','source_id':'OPERATOR_SIMULATED_REVIEW'}]}

def msg(mid, speaker, text, role='user'):
    return {'message_id':mid,'speaker_id':speaker,'role':role,'text':text,'created_at':'2026-11-04T18:00:00+09:00'}

def group_messages():
    lines=[('pm','이번 과제는 동아리 행사 신청 웹을 만들어 최종 시연을 제출하는 거야.'),
     ('design','참가자가 신청하고 운영자가 신청 내역을 볼 수 있어야 해. 모바일로도 신청 완료까지 시연하자.'),
     ('dev','2026년 11월 5일 시작해서 11월 22일 최종 제출하자. 개발은 11월 19일까지 끝내자.'),
     ('pm','11월 15일에는 핵심 화면과 API를 통합해서 중간 점검하자. 개발 완료 후 발표자료를 정리할게.'),
     ('design','나는 신청 화면과 관리자 목록 UI 맡을게. 처음부터 모든 페이지를 만드는 건 범위가 커.'),
     ('dev','나는 신청 API랑 저장, 조회를 맡을게. 기능은 이 세 가지부터 연결하자.'),
     ('pm','역할별로 만든 뒤 공유 저장소에서 통합하자. 일요일마다 시연 영상과 짧은 설명으로 인증하면 어때?'),
     ('design','좋아. 그룹에는 완료 여부랑 달성률만 공개하고 개인 사유는 공개하지 말자.'),
     ('dev','동의해. 구현이 막히면 도움 요청을 공유하되 사유 공개는 본인이 선택하자.'),
     ('pm','이 범위로 실행 순서와 통합 전에 준비할 것을 구체적으로 조언해줘. 없는 시간을 임의로 정하지는 마.')]
    return [msg('g'+str(i),s,t) for i,(s,t) in enumerate(lines,1)]

class Operator:
    def __init__(self):
        OUT.mkdir(parents=True,exist_ok=True)
        self.store=SQLiteRecordStore(OUT/'records.sqlite3')
        budget_file=ROOT/'runtime/operations-4/live-budget.json'
        if not budget_file.exists():write(budget_file,{'http':9,'limit':40,'earlier_attempts_included':9,'authorization':'user allowed necessary LIVE use for total four operations'})
        self.budget=Budget(budget_file)
        self.cache=MemoryResultCache()
        self.manifest={'source_sha256':digest(),'exact_sessions':[1,2,3,4],
          'previous_http':json.loads(self.budget.path.read_text())['http'],'total_http_limit':40,
          'reviewer':'AGENT_NOT_HUMAN','synthetic_members':True,'human_approval':'SIMULATED_OPERATOR_ONLY',
          'live_call_plan':{'1':['A','B','C','D'],'2':['E.coach','E.coach','E.plan'],'3':['C'],'4':['B']}}

    def run(self, session, step, module, payload, rid, *, dependencies=None, live=True):
        path=OUT/f'session-{session}'/(step+'.json')
        if path.exists():return json.loads(path.read_text(encoding='utf-8'))['response']
        self.store.save_input(rid,module,payload)  # user messages BEFORE AI
        trace=MemoryTraceSink()
        runtime=Runtime(TimelyClient(budget=self.budget),cache=self.cache,scope_id=rid,trace_sink=trace,debug=True)
        old_d=ROOT/'runtime/operations-4/session-1/04-group-plan.json'
        reuse=None
        if module=='D' and session==1 and old_d.exists():
            from wemeet_ai.shared.contracts import ConfirmedGoal
            saved=json.loads(old_d.read_text(encoding='utf-8'))
            normalized=lambda value:ConfirmedGoal.model_validate(value).model_dump(mode='json',exclude={'evidence'})
            if saved['response']['status']=='OK' and normalized(saved['request'])==normalized(payload):
                reuse=deepcopy(saved['response']);m=reuse['meta'];m['source_run_id']=m['run_id'];m['run_id']=str(uuid4());m['cached_usage']=m['usage'];m['usage']={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0};m['transport_attempts']=m['repair_attempts']=0;m['provider_request_ids']=[];m['execution_source']='CACHE';m['cache_hit']=True;m['latency_ms']=0
                reuse['warnings'].append('SAVED_SUCCESS_REUSE: 같은 확인 조건의 기존 공동 계획을 보존했습니다.')
        response=reuse or (execute(module,payload,runtime=runtime).model_dump(mode='json') if live else execute(module,payload).model_dump(mode='json'))
        scope,owner=self.store.scope_for(module,payload)
        record={'session':session,'step':step,'module':module,'request':payload,'response':response,
          'source_sha256':digest(),'executed_at':datetime.now(timezone.utc).isoformat()}
        # Preserve paid output before attempting the adapter transaction.
        write(path,record);write(path.with_name(step+'-private-trace.json'),{'events':trace.events})
        self.store.save_artifact(rid,Artifact(artifact_id=str(uuid4()),action_id=f'op-{session}-{step}-'+response['meta']['run_id'],module=module,
          scope=scope,owner_member_id=owner or None,request=payload,response=response,dependencies=dependencies or {}))
        print(f'Session {session} {step}: {response["status"]} HTTP={response["meta"]["transport_attempts"]}',flush=True)
        return response

    def session1(self):
        folder=OUT/'session-1';folder.mkdir(exist_ok=True)
        pointer=folder/'record-id.json'
        if pointer.exists():rid=json.loads(pointer.read_text())['record_id']
        else:
            record=self.store.create('운영1 · 행사 신청 웹',members=MEMBERS,reference_date='2026-11-04');rid=record.record_id
            write(pointer,{'record_id':rid})
        inp={'conversation':group_messages(),'members':MEMBERS,'reference_date':'2026-11-04','timezone':'Asia/Seoul','context':{}}
        a=self.run(1,'01-coach','A',inp,rid)
        if a['status']=='OK':self.store.append_message(rid,Message.model_validate(msg('ai-operation-1',None,a['data']['reply'],'assistant')),scope='PUBLIC')
        b_input=prepare_module_input('B',self.store.load(rid))
        b=self.run(1,'02-analysis','B',b_input.request,rid,dependencies=b_input.dependencies)
        c=None;confirmed=None
        if b['status']=='OK':
            prep=prepare_module_input('C',self.store.load(rid))
            c=self.run(1,'03-draft','C',prep.request,rid,dependencies=prep.dependencies)
        if c and c['status']=='OK':
            reviewed=GoalDraft.model_validate(c['data']);actions=[]
            # Not blind acceptance: explicitly compare/edit against scripted human decisions.
            for key,value in GOAL.items():
                if key in {'members','confirmed','evidence'}:continue
                old=reviewed.fields.get(key)
                out=review_field(reviewed,key,value=value,edit=not old or old.value!=value,actor_id='OPERATOR_SIMULATED_REVIEW')
                actions.append({'field':key,'before':old.value if old else None,'after':value,'result':out.status})
                reviewed=out.data
            confirmed=confirm_goal(reviewed,members=[Member.model_validate(m) for m in MEMBERS])
            write(folder/'review-actions.json',{'label':'SIMULATED_HUMAN_REVIEW','actions':actions,'draft':reviewed.model_dump(mode='json'),'confirmation':confirmed.model_dump(mode='json')})
            self.store.save_artifact(rid,Artifact(artifact_id=str(uuid4()),action_id='confirm-op1',module='C.confirm',request={'draft':reviewed.model_dump(mode='json')},response=confirmed.model_dump(mode='json'),dependencies=prep.dependencies))
        # If upstream fails, D still independently receives the scripted SW-confirmed conditions.
        goal=confirmed.data.model_dump(mode='json') if confirmed and confirmed.status=='OK' else GOAL
        d=self.run(1,'04-group-plan','D',goal,rid)
        f=self.run(1,'05-code-validation','F',{'scope':'GROUP','group_plan':d['data'],'context':{k:v for k,v in goal.items() if k in {'goal','start_date','end_date','completion_criteria','milestones','verification_method','privacy_scope'}}},rid,live=False) if d['status']=='OK' else None
        write(folder/'summary.json',{'record_id':rid,'A':a['status'],'B':b['status'],'C':c['status'] if c else 'NOT_RUN','D':d['status'],'F_valid':f['data']['valid'] if f and f.get('data') else None,'connected_flow':bool(c and c['status']=='OK' and confirmed and confirmed.status=='OK')})

    def session2(self):
        folder=OUT/'session-2';folder.mkdir(exist_ok=True)
        rid=self.store.create('운영2 · 독립 개인 코칭',members=MEMBERS,reference_date='2026-11-04').record_id
        context={k:v for k,v in GOAL.items() if k in {'goal','start_date','end_date','completion_criteria','milestones','verification_method','privacy_scope'}}
        context['role']=GOAL['roles']['design']
        inp={'context':context,'owner_member_id':'design','reference_date':'2026-11-04','conversation':[msg('p1','design','피그마로 개인 화면은 몇 번 그려봤지만 실제 개발팀과 일하는 건 처음이야. 주당 120분 가능해. 신청 폼과 관리자 목록 두 화면을 맡고 싶어. 신청 폼 와이어프레임은 절반 했고 목록은 아직 시작 전이야. 화요일 저녁은 수업이 있어. PRIVATE-OP2-CANARY는 개인 기록에만 남겨줘.')]}
        first=self.run(2,'01-private-coach','E.coach',inp,rid)
        follow=None
        if (first.get('data')or{}).get('state'):
            inp['state']=first['data']['state']
            inp['conversation'].append(msg('p-ai',None,first['data']['reply'],'assistant'))
            question=first['data']['state'].get('question')
            if question and question.get('options'):
                # A real operator chooses UNKNOWN rather than accepting an inappropriate default.
                option=next((o for o in question['options'] if o['action']=='UNKNOWN'),question['options'][0])
                inp['conversation'].append(msg('p2','design',option['label']))
                inp['answer']={'question_id':question['question_id'],'option_id':option['option_id'],'message_id':'p2'}
            else:inp['conversation'].append(msg('p2','design','더 확인할 게 있다면 작업 범위가 실제로 달라지는 것만 알려줘. 지금 정보로 우선순위와 도움 요청도 구체적으로 정리해줘.'))
            follow=self.run(2,'02-button-followup','E.coach',inp,rid)
        plan_input={'context':context,'owner_member_id':'design',
          'contribution':{'title':'신청·관리 화면 UI','deliverable':'신청 폼과 관리자 목록 2개 UI 및 개발 전달용 상태 설명','completion_criteria':['모바일 신청 완료 화면까지 흐름이 연결됨','목록의 빈 상태·로딩·오류 상태가 설명됨'],'confirmed':True},
          'profile':{'current_level':'피그마 일부 경험, 협업 초보','weekly_available_minutes':120,'confirmed':True,'constraints':['화요일 저녁 수업','PRIVATE-OP2-CANARY'],'private_notes':['개인 원문을 그룹에 내보내지 않음']}}
        plan=self.run(2,'03-member-plan','E.plan',plan_input,rid)
        public=self.store.load(rid).model_dump_json();other=self.store.load(rid,owner_member_id='dev').model_dump_json()
        f=self.run(2,'04-code-validation','F',{'scope':'MEMBER','member_plans':[plan['data']],'context':context,'weekly_budgets':{'design':120}},rid,live=False) if plan['status']=='OK' else None
        write(folder/'summary.json',{'record_id':rid,'D_not_run':True,'coach':first['status'],'followup':follow['status'] if follow else 'NOT_RUN','plan':plan['status'],'F_valid':f['data']['valid'] if f and f.get('data') else None,'public_has_canary':'PRIVATE-OP2-CANARY'in public,'other_has_canary':'PRIVATE-OP2-CANARY'in other,'plan_has_canary':'PRIVATE-OP2-CANARY'in json.dumps(plan),'confirmation':'SCRIPTED_OPERATOR_NOT_REAL_USER'})

    def session3(self):
        folder=OUT/'session-3';folder.mkdir(exist_ok=True)
        review_file=OUT/'session-1/review-actions.json'
        if review_file.exists():previous=json.loads(review_file.read_text(encoding='utf-8'))['draft']
        else:
            # Session 1 C failed. These are explicitly SW-authored reviewed conditions,
            # not a successful AI draft manufactured by the evaluator.
            previous=GoalDraft(fields={k:ReviewField(value=v,origin='EXTRACTED',review_status='ACCEPTED',evidence=[Evidence(source='SW_CONFIRMED',source_id='SCRIPTED_OPERATOR')]) for k,v in GOAL.items() if k not in {'members','confirmed','evidence'}}).model_dump(mode='json')
        rid=json.loads((OUT/'session-1/summary.json').read_text())['record_id']
        from wemeet_ai.continuity import source_tokens
        before_view=self.store.load(rid)
        tokens=source_tokens(before_view)
        self.store.save_artifact(rid,Artifact(artifact_id=str(uuid4()),action_id='SW-authored-confirm-for-change-test',module='C.confirm',request={'draft':previous},response={'status':'OK','data':GOAL},dependencies={'public_messages':tokens['public_messages']}))
        change=msg('deadline-change','pm','최종 제출이 2026년 11월 29일로 미뤄졌어. 개발 완료 11월 19일과 중간 점검 11월 15일은 유지하자.')
        self.store.append_message(rid,Message.model_validate(change),scope='PUBLIC')
        stale=prepare_module_input('D',self.store.load(rid))
        payload={'previous':previous,'members':MEMBERS,'reference_date':'2026-11-04','facts':{
          'end_date':{'value':'2026-11-29','origin':'EXTRACTED','evidence':[{'source':'USER_MESSAGE','source_id':'deadline-change'}]}}}
        result=self.run(3,'01-changed-deadline','C',payload,rid)
        checked=confirm_goal(GoalDraft.model_validate(result['data']),members=[Member.model_validate(m) for m in MEMBERS]) if result['status']=='OK' else None
        final=None;updated_plan=None
        if checked and checked.status=='NEEDS_INPUT' and 'end_date'in checked.needed_inputs:
            updated=review_field(GoalDraft.model_validate(result['data']),'end_date',value='2026-11-29',edit=True,actor_id='SIMULATED_DATE_CONFIRMATION')
            final=confirm_goal(updated.data,members=[Member.model_validate(m) for m in MEMBERS])
            if final.status=='OK':
                from wemeet_ai.continuity import source_tokens
                self.store.save_artifact(rid,Artifact(artifact_id=str(uuid4()),action_id='user-confirmed-new-date',module='C.confirm',request={'draft':updated.data.model_dump(mode='json')},response=final.model_dump(mode='json'),dependencies={'public_messages':source_tokens(self.store.load(rid))['public_messages']}))
                updated_plan=self.run(3,'02-revised-group-plan','D',final.data.model_dump(mode='json'),rid)
        write(folder/'confirmed-change.json',{'confirmation':final.model_dump(mode='json') if final else None,'new_plan':updated_plan,'original_plan_retained':any(a.module=='D'and a.response.get('status')=='OK' for a in self.store.load(rid).artifacts)})
        write(folder/'summary.json',{'record_id':rid,'previous_D_is_stale':stale.stale,'previous_D_ready':stale.ready,'C':result['status'],'end_field':result['data']['fields'].get('end_date') if result['status']=='OK' else None,'confirm_status':checked.status if checked else None,'needed_inputs':checked.needed_inputs if checked else [],'code_only_date_edit':review_field(GoalDraft.model_validate(previous),'end_date',value='2026-11-29',edit=True,actor_id='SIMULATED_MANUAL_EDIT').model_dump(mode='json')})

    def session4(self):
        folder=OUT/'session-4';folder.mkdir(exist_ok=True)
        rid=self.store.create('운영4 · 독립 공부 분석·실패·복원',members=MEMBERS,reference_date='2026-11-04').record_id
        inp={'members':MEMBERS,'reference_date':'2026-11-04','conversation':[
          msg('s1','design','2026년 11월 5일부터 12월 3일까지 SQLD 시험을 준비하자. 매주 두 번 문제를 풀자.'),
          msg('s2','dev','나는 세 번이 좋을 것 같은데. 두 번과 세 번 중에서는 아직 결론을 못 냈어.'),
          msg('s3','pm','오답 이유를 직접 설명할 수 있는 것을 완료 기준으로 하자.'),
          msg('s4','design','문제 풀이 캡처와 오답 이유를 남기고 그룹에는 완료 여부만 공개하자.'),
          msg('s5','dev','인증과 공개 범위는 좋아. 주당 횟수는 두 번과 세 번 후보를 모두 남겨줘.')]}
        result=self.run(4,'01-independent-analysis','B',inp,rid)
        runtime=Runtime(TimelyClient(budget=self.budget),cache=self.cache,scope_id=rid)
        class AuthFailure:
            status_code=401
            def json(self):return {'error':{'message':'synthetic auth failure'}}
        class NoNetwork:
            calls=0
            def post(self,*a,**kw):self.calls+=1;return AuthFailure()
        transport=NoNetwork();failed_input=deepcopy(inp)
        failure_message=msg('s6','pm','연결이 안 되더라도 이 대화는 남겨줘.')
        failed_input['conversation'].append(failure_message)
        self.store.save_input(rid,'A',failed_input)
        failed=execute('A',failed_input,runtime=Runtime(TimelyClient(session=transport)))
        self.store.save_artifact(rid,Artifact(artifact_id=str(uuid4()),action_id='injected-auth-failure',module='A',request=failed_input,response=failed.model_dump(mode='json')))
        restored=SQLiteRecordStore(OUT/'records.sqlite3').load(rid)
        revision=restored.revision
        duplicate=self.store.append_message(rid,Message.model_validate(failure_message),scope='PUBLIC')
        changed=deepcopy(failure_message);changed['text']='같은 ID를 변경하면 안 됨'
        conflict=False
        try:self.store.append_message(rid,Message.model_validate(changed),scope='PUBLIC')
        except RevisionConflict:conflict=True
        preparation=prepare_module_input('B',restored)
        before=json.loads(self.budget.path.read_text())['http']
        cached=execute('B',inp,runtime=runtime).model_dump(mode='json') if result['status']=='OK' else None
        after=json.loads(self.budget.path.read_text())['http']
        write(folder/'summary.json',{'record_id':rid,'B':result['status'],'B_source':'LIVE_NEW_STUDY_CASE','failure_mode':'INJECTED_401_NOT_REAL_PROVIDER','failure_code':failed.error.code if failed.error else None,'injected_transport_calls':transport.calls,'real_HTTP_for_injected_failure':0,'messages_preserved':len(restored.conversation)==len(inp['conversation'])+1,'previous_B_success_preserved':any(a.module=='B'and a.response['status']=='OK' for a in restored.artifacts),'duplicate_no_revision_change':revision==duplicate,'changed_id_conflict':conflict,'prepare_ready':preparation.ready,'cache_source':cached['meta']['execution_source'] if cached else None,'cache_new_http':after-before})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--session',type=int,choices=[1,2,3,4],required=True);parser.add_argument('--live',action='store_true');parser.add_argument('--recheck',action='store_true');args=parser.parse_args()
    global OUT
    if args.recheck:OUT=ROOT/'runtime/operations-4/recheck'
    if not args.live:print('Explicit --live required; no HTTP performed.');return
    if (OUT/f'session-{args.session}'/'summary.json').exists():print('Completed session preserved. No calls.');return
    op=Operator()
    if not (OUT/'manifest.json').exists():write(OUT/'manifest.json',op.manifest)
    getattr(op,'session'+str(args.session))()

if __name__=='__main__':main()
