"""Complete SW integration using public contracts only. ZERO HTTP, explicit fixtures.

Run: python sw_m0_m1_end_to_end.py --output demo-output
Human confirmation is simulated ONLY in this tutorial. Never auto-accept in a service.
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from wemeet_ai import GoalAI
from wemeet_ai.client import AIError
from wemeet_ai.cache import MemoryResultCache
from wemeet_ai.api import (
    AnalysisRequest, ConversationAnalysis, DraftField, GoalSetupRequest, GoalSetupResponse,
    GoalSetupState, GoalSpecDraft, GroupPlan, Member, Message, MemberCoachModel,
    MemberCoachState, MemberGoalDraft, MemberGoalRequest, MemberPlanDraft,
    MemberPlanRequest, MemberProfile, ValidationRequest, HandoffRequest, BaselineRef,
)
from wemeet_ai.baseline import BaselineRequest, ActivityReference, build_baseline, validate_activity_reference


NOW=datetime(2026,10,29,10,tzinfo=timezone(timedelta(hours=9)))
MEMBERS=[Member(member_id='design',nickname='민지'),Member(member_id='coding',nickname='도윤')]
PRIVATE={
    'design':{'level':'처음이에요','minutes':120,'constraint':'화요일 가족 돌봄 일정','role':'UI 시안'},
    'coding':{'level':'혼자 구현할 수 있어요','minutes':300,'constraint':'목요일 개인 병원 방문','role':'화면 구현'},
}


class AuthoredFixtureClient:
    """Provider injection, not a mock fallback. No requests/session/key access."""
    model='AUTHORED_FIXTURE_NOT_LIVE'
    base_url='offline://sw-acceptance'
    def __init__(self):self.calls=[];self.fail_group_once=True;self.failed_members=set()
    def complete_text(self,messages,options=None,*,stats):
        self.calls.append('coach');stats.actual_model=self.model
        return SimpleNamespace(content='11/8 개발 완료와 11/11 제출을 나누어 진행하세요. UI 시안을 먼저 공유한 뒤 화면을 연결하는 순서를 추천합니다.')
    def complete_json(self,messages,output_schema,options=None,*,stats):
        p=json.loads(messages[-1]['content']);task=output_schema.__name__;self.calls.append(task);stats.actual_model=self.model
        if output_schema is ConversationAnalysis:
            refs=[m['message_id'] for m in p['conversation'] if m['role']=='user']
            fields={name:{'status':'EXTRACTED','candidates':[{'candidate_id':name,'value':value,
                'original_text':p['conversation'][0]['text'],'evidence_refs':[refs[0]],'proposer_ids':['design']}]}
                for name,value in {'goal':{'title':'동아리 소개 웹사이트','expected_outcome':'소개와 가입 안내가 연결된 시연','execution_method':'UI 시안 공유 후 화면 구현 및 연결 점검'},
                    'timeline':{'start_date':'2026-10-29','end_date':'2026-11-11'},
                    'success':{'criteria':['소개와 가입 안내를 모바일 화면에서 시연한다.']}}.items()}
            value={'summary':'소개 웹사이트의 공동 일정과 담당을 정리했습니다.','fields':fields}
        elif output_schema is GoalSpecDraft:
            values={'goal':'동아리 소개 웹사이트','expected_outcome':'소개와 가입 안내가 연결된 시연',
                'completion_criteria':['소개와 가입 안내를 모바일 화면에서 시연한다.'],
                'start_date':'2026-10-29','end_date':'2026-11-11','execution_method':'UI 시안 공유 후 화면 구현 및 연결 점검',
                'verification_method':'스크린샷','privacy_scope':'OBJECTIVE_ONLY','checkins':['금요일 · 이미지 확인'],
                'roles':{'design':'디자인','coding':'프론트'},'frequency_per_week':None,'metric':None}
            value={'summary':'검토 전 추천 초안','fields':{k:{'value':v,'origin':'AI_SUGGESTED','reason':'작성된 튜토리얼 추천안'} for k,v in values.items()}}
        elif output_schema is GroupPlan:
            if self.fail_group_once:
                self.fail_group_once=False;raise AIError('TIMEOUT','작성된 장애 예제: 공동 계획 응답 지연',True)
            value={k:p[k] for k in ('spec_id','goal','expected_outcome','completion_criteria','start_date','end_date','frequency_per_week','metric')}
            value.update(milestones=[{'title':m['title'],'due_date':m['due_date'],'completion_criteria':m['title']+' 결과물 확인','evidence_refs':p['evidence_ids']} for m in p['milestones']],
                weekly_outline=[{'week':w['week'],'outcome':'UI 시안 공유' if w['week']==1 else '연결 시연 완성','tasks':['민지: UI 시안 전달','도윤: 화면 연결 및 점검']} for w in p['week_ranges']],evidence_refs=p['evidence_ids'])
        elif output_schema is MemberCoachModel:
            mid=p['owner_member_id'];known=p['state']['fields'];missing=next((f for f in ('current_level','weekly_available_minutes') if f not in known),None)
            question={'target_field':missing,'text':'이 작업의 경험은 어느 정도인가요?' if missing=='current_level' else '주간 가능 시간을 골라주세요.',
                'options':[{'label':PRIVATE[mid]['level'] if missing=='current_level' else str(PRIVATE[mid]['minutes']//60)+'시간',
                    'value':PRIVATE[mid]['level'] if missing=='current_level' else PRIVATE[mid]['minutes'],'action':'VALUE'}]} if missing and p['allow_question'] else None
            value={'reply':'담당 결과물을 공동 시연에 맞춰 나누겠습니다. 확인한 조건으로 범위를 조절할 수 있습니다.',
                'kind':'CLARIFICATION' if question else 'SUMMARY','question':question,
                'extracted_fields':{'constraints':{'value':[PRIVATE[mid]['constraint']],'origin':'EXTRACTED','evidence_refs':[p['conversation'][0]['message_id']]}},
                'diagnostic_dimensions':[{'field_id':f,'label':label,'reason':'이 역할의 작업 범위와 난이도를 조절합니다.'} for f,label in [('current_level','경험'),('weekly_available_minutes','시간'),('scope','담당 결과물'),('current_progress','현재 진척')]]}
        elif output_schema is MemberGoalDraft:
            mid=p['owner_member_id'];value={'owner_member_id':mid,'group_plan_id':p['group_plan']['plan_id'],
                'contribution':{'owner_member_id':mid,'group_plan_id':p['group_plan']['plan_id'],'title':PRIVATE[mid]['role']+' 완성',
                    'deliverable':PRIVATE[mid]['role']+' 결과물','completion_criteria':['소개와 가입 안내가 연결된다.'],
                    'public_rationale':'공동 소개 웹사이트 시연에 기여합니다.','evidence_refs':p['goal_spec']['evidence_ids']},'profile_fields':p['state']['fields']}
        elif output_schema is MemberPlanDraft:
            mid=p['member_profile']['member_id'];self.calls[-1]+=':'+mid
            if mid=='coding' and mid not in self.failed_members:
                self.failed_members.add(mid);raise AIError('TIMEOUT','작성된 장애 예제: 도윤 개인 계획만 실패',True)
            minutes=25 if mid=='design' else 60
            value={'public_plan':{'group_plan_id':p['group_plan']['plan_id'],'member_id':mid,
                'weekly_targets':[{'week':i,'outcome':PRIVATE[mid]['role']+' 단계 완성','estimated_minutes':minutes*2} for i in range(1,p['number_of_weeks']+1)],
                'first_week_todos':[{'scheduled_date':day,'action':PRIVATE[mid]['role']+' '+str(i+1)+'단계',
                    'amount_or_deliverable':PRIVATE[mid]['role']+' 검토본','estimated_minutes':minutes,'difficulty':'EASY' if mid=='design' else 'MEDIUM',
                    'completion_criteria':'모바일 화면에서 소개와 가입 안내를 확인한다.','evidence_refs':p['evidence_ids']} for i,day in enumerate(p['first_week_allowed_dates'][:2])],
                'adaptation_rules':['지연되면 남은 작업을 우선순위에 따라 다음 버전에서 조정한다.'],'public_rationale':'담당 결과물을 공동 시연에 연결합니다.','evidence_refs':p['evidence_ids']},
                'private_notes':['본인에게만 전달하는 작업 조절 안내입니다.']}
        else:raise AssertionError('Unexpected schema '+task)
        return SimpleNamespace(parsed_output=output_schema.model_validate(value))


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value.model_dump(mode='json') if hasattr(value,'model_dump') else value,ensure_ascii=False,indent=2),encoding='utf-8')


def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    fixture=AuthoredFixtureClient();cache=MemoryResultCache();ai=GoalAI(fixture,cache_backend=cache,cache_scope='tutorial-group',execution_mode='REPLAY')
    records=[];status_examples={};member_results={};baselines=[];intakes={};profiles={};private_chats={}
    def invoke(name,request,fn,owner=None, sensitive=False):
        result=fn(request);records.append({'name':name,'owner':owner,'meta':result.meta.model_dump(mode='json'),'status':result.status})
        folder=output/'private'/'_service' if sensitive else output/'private'/owner if owner else output/'group'
        save(folder/(name+'.json'),{'request':request.model_dump(mode='json'),'response':result.model_dump(mode='json',exclude={'traces'})})
        return result
    def ok(result):
        if result.status!='OK':raise RuntimeError(result.error.model_dump_json() if result.error else str(result.questions))
        return result.data
    public=[Message(message_id='g1',speaker_id='design',role='user',created_at=NOW,
        text='10/29부터 동아리 소개 웹사이트를 만들자. 소개와 가입 안내를 모바일에서 시연하자. 11/4 중간 목표, 11/8 개발 완료, 11/11 최종 제출이야.'),
        Message(message_id='g2',speaker_id='coding',role='user',created_at=NOW,text='민지는 디자인, 도윤은 프론트 담당이야. 금요일 스크린샷으로 확인하고 완료 여부와 달성률만 공개하자.')]
    coached=ok(invoke('01-coach',GoalSetupRequest(action='COACH',conversation=public,members=MEMBERS,reference_date=NOW.date()),ai.advance_goal_setup))
    reviewed=ok(invoke('02-review',GoalSetupRequest(action='REVIEW',state=coached.state),ai.advance_goal_setup))
    blocked=invoke('03-needs-review',GoalSetupRequest(action='CONFIRM',state=reviewed.state,actor_member_id='design'),ai.advance_goal_setup)
    assert blocked.status=='NEEDS_INPUT';status_examples['NEEDS_INPUT']='group/03-needs-review.json'
    # Tutorial-only simulated human click. SW must obtain an actual user choice here.
    accepted=ok(invoke('04-simulated-accept',GoalSetupRequest(action='RESPOND',state=reviewed.state,actor_member_id='design',
        response=GoalSetupResponse(operation='ACCEPT',fields=list(reviewed.state.draft.fields))),ai.advance_goal_setup))
    confirmed=ok(invoke('05-confirm',GoalSetupRequest(action='CONFIRM',state=accepted.state,actor_member_id='design'),ai.advance_goal_setup))
    save(output/'group'/'saved-state.json',confirmed.state)
    restored=GoalSetupState.model_validate_json((output/'group'/'saved-state.json').read_text(encoding='utf-8'))
    duplicate=ok(invoke('06-duplicate-confirm',GoalSetupRequest(action='CONFIRM',state=restored,actor_member_id='design'),ai.advance_goal_setup))
    assert duplicate.group_spec.spec_id==confirmed.group_spec.spec_id
    spec=duplicate.group_spec
    failed=invoke('07-group-timeout',spec,ai.generate_group_plan);assert failed.status=='FAILED';status_examples['FAILED']='group/07-group-timeout.json'
    # Recover from the stored result: do not repeat COACH/REVIEW or lose accepted values.
    group=ok(invoke('08-group-resumed',spec,ai.generate_group_plan))
    for member in MEMBERS:
        mid=member.member_id;member_ai=GoalAI(fixture,cache_backend=cache,cache_scope='tutorial-private-'+mid,execution_mode='REPLAY')
        chat=[Message(message_id=mid+'-private',speaker_id=mid,role='user',created_at=NOW,text='내 담당 결과물을 나누고 싶어요. '+PRIVATE[mid]['constraint']+'이 있어요.')]
        state=MemberCoachState();coached_member=None
        for index in range(3):
            request=MemberGoalRequest(goal_spec=spec,group_plan=group,owner_member_id=mid,conversation=chat,state=state,
                diagnostic_mode='ADAPTIVE',reference_date=NOW.date(),answer=answer if index else None)
            coached_member=ok(invoke('09-intake-'+str(index),request,member_ai.coach_member_goal,mid));state=coached_member.state
            q=state.current_question
            if not q:break
            choice=q.options[0];message=Message(message_id=mid+'-answer-'+str(index),speaker_id=mid,role='user',created_at=NOW,text=choice.label);chat.append(message)
            from wemeet_ai.contracts import ButtonAnswer
            answer=ButtonAnswer(question_id=q.question_id,option_ids=[choice.option_id],message_id=message.message_id)
        assert not coached_member.missing_fields
        draft=ok(invoke('10-member-draft',MemberGoalRequest(goal_spec=spec,group_plan=group,owner_member_id=mid,conversation=chat,state=state,
            diagnostic_mode='ADAPTIVE',reference_date=NOW.date()),member_ai.draft_member_goal,mid))
        # Tutorial-only review of private conditions and contribution goal.
        draft.contribution.review_status='ACCEPTED'
        for field in draft.profile_fields.values():field.review_status='ACCEPTED'
        profile=MemberProfile(member_id=mid,current_level=draft.profile_fields['current_level'].value,
            weekly_available_minutes=draft.profile_fields['weekly_available_minutes'].value,preferred_times=[],constraints=draft.profile_fields['constraints'].value)
        profiles[mid]=profile;intakes[mid]=draft;private_chats[mid]=chat
        save(output/'private'/mid/'reviewed-intake.json',draft)
        request=MemberPlanRequest(goal_spec=spec,group_plan=group,member_profile=profile,contribution_goal=draft.contribution)
        member_results[mid]=invoke('11-member-plan',request,member_ai.generate_member_plan,mid)
    save(output/'private'/'member-results.json',{k:r.model_dump(mode='json',exclude={'traces'}) for k,r in member_results.items()})
    # Resume only failed members after loading the stored success/failure envelope.
    from wemeet_ai.contracts import Result
    restored_results={k:Result[MemberPlanDraft].model_validate(v) for k,v in json.loads((output/'private'/'member-results.json').read_text(encoding='utf-8')).items()}
    successful_ids={k:r.data.public_plan.plan_id for k,r in restored_results.items() if r.status=='OK'}
    retries=[k for k,r in restored_results.items() if r.status!='OK']
    for mid in retries:
        request=MemberPlanRequest(goal_spec=spec,group_plan=group,member_profile=profiles[mid],contribution_goal=intakes[mid].contribution)
        restored_results[mid]=invoke('12-member-resumed',request,GoalAI(fixture,execution_mode='REPLAY').generate_member_plan,mid)
    plans=[ok(r) for r in restored_results.values()]
    assert all(restored_results[k].data.public_plan.plan_id==v for k,v in successful_ids.items())
    validation=ok(invoke('13-validate',ValidationRequest(goal_spec=spec,group_plan=group,member_plans=plans,member_profiles=list(profiles.values())),ai.validate_plan,sensitive=True));assert validation.valid
    for member in MEMBERS:
        mid=member.member_id
        baseline=build_baseline(BaselineRequest(scope='PERSONAL',confirmed_by=mid,owner_member_id=mid,goal_spec=spec,group_plan=group,
            member_plans=plans,member_profiles=list(profiles.values())))
        baselines.append(baseline);task=baseline['public_payload']['tasks'][0]
        activity=ActivityReference(plan_version_id=baseline['plan_version_id'],task_id=task['task_id'],recorded_at=NOW,text='공동 목표에 맞춘 담당 작업 기록')
        assert validate_activity_reference(baseline,activity)
        save(output/'group'/('m2-'+mid+'.json'),{'baseline':{k:v for k,v in baseline.items() if k!='private_payloads'},'activity':activity.model_dump(mode='json')})
        save(output/'private'/mid/'baseline-private.json',baseline['private_payloads'])
    refs=[BaselineRef(baseline_id=b['baseline_id'],scope=b['scope'],owner_member_id=b['owner_member_id'],plan_version_id=b['plan_version_id'],group_plan_version_id=b['group_plan_version_id'],task_ids=[t['task_id'] for t in b['public_payload']['tasks']],confirmed_by=b['confirmed_by'],confirmed_at=b['confirmed_at']) for b in baselines]
    handoff=ok(invoke('14-handoff',HandoffRequest(baseline_refs=refs,goal_spec=spec,group_plan=group,member_plans=plans,member_profiles=list(profiles.values()),
        personal_drafts=intakes,personal_conversations=private_chats,reviewed_draft=confirmed.state.draft,source_fingerprint='tutorial-v1',current_source_fingerprint='tutorial-v1'),ai.build_handoff,sensitive=True))
    assert handoff.group_file['integration_ready'] and len(handoff.group_file['evaluation_baselines'])==len(MEMBERS)
    save(output/'group-public.json',handoff.group_file)
    for mid,payload in handoff.private_files.items():save(output/'private'/mid/'handoff-private.json',payload)
    public_text=json.dumps(handoff.group_file,ensure_ascii=False)+' '.join(p.read_text(encoding='utf-8') for p in (output/'group').glob('*.json'))
    assert all(row['constraint'] not in public_text for row in PRIVATE.values())
    report={'source':'AUTHORED_FIXTURE / REPLAY','actual_http_requests':0,'actual_tokens':0,'human_confirmation':'SIMULATED_TUTORIAL_ONLY',
        'validation_passed':validation.valid,'m2_reference_valid':True,'state_restored':True,'duplicate_confirmation_stable':True,
        'retry_member_ids':retries,'successful_member_plan_preserved':True,'public_private_separation':True,
        'fixture_calls':len(fixture.calls),'status_examples':status_examples,'steps':len(records),'service_activation':False}
    save(output/'run-manifest.json',records);save(output/'report.json',report);print(json.dumps(report,ensure_ascii=True));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='demo-output');run(parser.parse_args().output)
