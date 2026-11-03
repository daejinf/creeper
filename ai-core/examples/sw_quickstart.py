"""Executable SW integration tutorial. Default: scripted fixtures, ZERO HTTP, not AI quality evidence."""
import argparse
import json
from datetime import timedelta,datetime,timezone
from pathlib import Path
from types import SimpleNamespace

from wemeet_ai import GoalAI
from wemeet_ai.cache import MemoryResultCache
from wemeet_ai.contracts import *
from wemeet_ai.scenarios import get_scenario
from wemeet_ai.scenario_inputs import authored_spec,authored_group
from wemeet_ai.analysis import local_analysis
from wemeet_ai.baseline import BaselineRequest,ActivityReference,build_baseline,validate_activity_reference


class OfflineFixtureClient:
    """Injected contract fixtures: never contacts a provider or reads credentials."""
    model='SCRIPTED_FIXTURE_NOT_LIVE';base_url='offline://fixtures'
    def __init__(self):self.outputs={};self.fixture_requests=0
    def complete_text(self,messages,options=None,*,stats):
        self.fixture_requests+=1;stats.actual_model=self.model
        return SimpleNamespace(content='현재는 담당 결과물을 작게 나누고 검증하는 순서를 추천합니다. 날짜와 공개 범위는 대화에서 결정하고 B에서 정리할 수 있습니다.')
    def complete_json(self,messages,output_schema,options=None,*,stats):
        self.fixture_requests+=1;stats.actual_model=self.model
        value=self.outputs[output_schema.__name__]
        if callable(value):value=value()
        return SimpleNamespace(parsed_output=output_schema.model_validate(value))


def demo(output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    case=get_scenario('software-05-b');full=authored_spec(case)
    client=OfflineFixtureClient();cache=MemoryResultCache()
    ai=GoalAI(client,cache_backend=cache,cache_scope='offline-tutorial-group',execution_mode='REPLAY')
    steps=[]
    def run(name,request,fn):
        result=fn(request)
        record={'step':name,'request':request.model_dump(mode='json'),'response':result.model_dump(mode='json',exclude={'traces'})}
        steps.append(record)
        (output_dir/(name+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        if result.status=='NEEDS_INPUT':raise RuntimeError('Collect missing inputs: '+', '.join(result.questions))
        if result.status=='FAILED':raise RuntimeError(result.error.code+': '+result.error.message)
        return result

    # A: SW supplies group conversation, stores pending recommendations, then records human choice.
    refs=full.evidence_ids
    client.outputs['ProactiveCoachOutput']={'reply':'작은 결과물부터 검증하는 실행안을 제안합니다.','observations':['담당 결과물과 기간이 대화에 있습니다.'],
        'proposals':[{'title':'핵심 결과물 검증','rationale':'범위와 완료 여부를 확인하기 쉽습니다.','field_updates':{'completion_criteria':full.completion_criteria},'evidence_refs':[case.group_input.conversation[0].message_id]}]}
    run('A-coach',CoachRequest(**case.group_input.model_dump(),coaching_mode='DIALOGUE'),ai.coach_reply)
    # B: no implicit approval; analysis remains candidates, missing items do not block C.
    client.outputs['ConversationAnalysis']=local_analysis(case.group_input).model_dump(mode='json')
    analysis=run('B-analysis',case.group_input,ai.analyze_conversation).data
    # C: these are authored fixtures. A real UI must collect explicit card acceptance.
    client.outputs['GoalSpecDraft']={'draft_id':'offline-draft','summary':'작성된 튜토리얼 기준안·AI 미실행','fields':{k:{'value':v,'origin':'AI_SUGGESTED','reason':'작성된 테스트 기준안','evidence_refs':[case.group_input.conversation[0].message_id],'review_status':'PENDING'} for k,v in case.confirmed_fixture.model_dump(mode='json').items() if k!='confirmed'}}
    draft=run('C-draft',DraftRequest(analysis=analysis,reference_date=case.group_input.reference_date),ai.draft_goal_spec).data
    # Simulated user confirmation ONLY for this local tutorial; never writes human-review statistics.
    for field in draft.fields.values():field.review_status='ACCEPTED'
    prepared=run('C-reviewed-spec',GroupPrepareRequest(analysis=analysis,confirmed_decisions=case.confirmed_fixture),ai.prepare_group_goal_spec)
    spec=prepared.data
    client.outputs['GroupPlan']=authored_group(case,spec).model_dump(mode='json')
    group=run('D-group',spec,ai.generate_group_plan).data
    # E: only this member's private conversation/profile is passed, public spec has NO profiles.
    profile=case.profiles[0];mid=profile.member_id;conversation=[Message.model_validate(m) for m in case.private_conversations[mid][:2]]
    client.outputs['MemberCoachModel']={'reply':'현재 조건을 바탕으로 담당 결과물을 나눌 수 있습니다.','kind':'SUMMARY','question':None,
        'extracted_fields':{'current_level':{'value':profile.current_level,'evidence_refs':[conversation[0].message_id]},'weekly_available_minutes':{'value':profile.weekly_available_minutes,'evidence_refs':[conversation[1].message_id]}},
        'diagnostic_dimensions':[{'field_id':key,'label':label,'reason':'담당 결과물과 작업 예산을 확인합니다.'} for key,label in [('current_level','경험'),('weekly_available_minutes','시간'),('current_progress','진척'),('scope','담당 범위')]]}
    run('E-personal-coach',MemberGoalRequest(goal_spec=spec,group_plan=group,owner_member_id=mid,conversation=conversation,diagnostic_mode='ADAPTIVE'),ai.coach_member_goal)
    contribution=ContributionGoal(owner_member_id=mid,group_plan_id=group.plan_id,title='내 담당 결과물',deliverable=case.expected['deliverable'],completion_criteria=spec.completion_criteria,public_rationale='공동 결과물 검증에 기여합니다.',evidence_refs=refs,review_status='ACCEPTED')
    days=spec.frequency_per_week or 2;minutes=max(1,min(20,profile.weekly_available_minutes//days));weeks=(spec.end_date-spec.start_date).days//7+1
    client.outputs['MemberPlanDraft']={'public_plan':{'group_plan_id':group.plan_id,'member_id':mid,
        'weekly_targets':[{'week':week,'outcome':contribution.deliverable,'estimated_minutes':minutes*days} for week in range(1,weeks+1)],
        'first_week_todos':[{'scheduled_date':(spec.start_date+timedelta(days=i)).isoformat(),'action':f'담당 결과물 {i+1}단계 확인','amount_or_deliverable':contribution.deliverable,'estimated_minutes':minutes,'difficulty':'EASY','completion_criteria':spec.completion_criteria[0],'evidence_refs':refs} for i in range(days)],
        'adaptation_rules':['미완료 시 남은 범위를 다시 확인하고 새 버전으로 조정'],'public_rationale':'공동 결과물을 분담합니다.','evidence_refs':refs},'private_notes':['이 문장은 본인에게만 전달합니다.']}
    member_request=MemberPlanRequest(goal_spec=spec,group_plan=group,member_profile=profile,contribution_goal=contribution)
    member=run('E-personal-plan',member_request,ai.generate_member_plan).data
    cached=run('E-cache-reuse',member_request,ai.generate_member_plan)
    assert cached.meta.cache_hit and cached.meta.usage.total_tokens==0
    checked=run('F-validate',ValidationRequest(goal_spec=spec,group_plan=group,member_plans=[member],member_profiles=[profile],require_all_members=False),ai.validate_plan)
    assert checked.data.valid
    baseline=build_baseline(BaselineRequest(scope='PERSONAL',confirmed_by=mid,owner_member_id=mid,goal_spec=spec,group_plan=group,member_plans=[member],member_profiles=[profile],require_all_members=False))
    task=baseline['public_payload']['tasks'][0]
    activity=ActivityReference(plan_version_id=baseline['plan_version_id'],task_id=task['task_id'],recorded_at=datetime.now(timezone.utc),text='담당 결과물 작업 기록')
    assert validate_activity_reference(baseline,activity)
    public={k:v for k,v in baseline.items() if k!='private_payloads'}
    (output_dir/'group-public.json').write_text(json.dumps({'goal_spec':spec.model_dump(mode='json'),'group_plan':group.model_dump(mode='json'),'personal_tasks':member.public_plan.model_dump(mode='json')},ensure_ascii=False,indent=2),encoding='utf-8')
    (output_dir/'personal-private.json').write_text(json.dumps(baseline['private_payloads'],ensure_ascii=False,indent=2),encoding='utf-8')
    (output_dir/'m2-reference.json').write_text(json.dumps({'baseline':public,'activity':activity.model_dump(mode='json')},ensure_ascii=False,indent=2),encoding='utf-8')
    report={'source':'SCRIPTED_FIXTURE / REPLAY - not LIVE quality evidence','actual_http_requests':0,'actual_tokens':0,'fixture_requests':client.fixture_requests,'cache_hit':cached.meta.cache_hit,'validation_passed':checked.data.valid,'m2_reference_valid':True,'integration_ready':False,'notice':'다른 구성원 계획·실제 사용자 승인·운영 권한은 이 튜토리얼에서 검증하지 않았습니다.','steps':len(steps)}
    (output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='runs/sw-quickstart');args=parser.parse_args();demo(args.output)
