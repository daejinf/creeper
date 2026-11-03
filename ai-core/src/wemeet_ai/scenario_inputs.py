"""Authored prerequisites are test inputs, not successful LIVE outputs or approvals."""
from datetime import timedelta
from .contracts import AnalysisField, Candidate, ConversationAnalysis, CoachRequest, DraftRequest, GroupPlan, MemberGoalRequest
from .service import GoalAI


def authored_analysis(case):
    fields={};all_refs=[m.message_id for m in case.group_input.conversation]
    targets={'goal':({'title':case.expected['goal']},case.expected['goal']),
        'success':({'criteria':case.expected['criterion']},case.expected['criterion']),
        'frequency':({'per_week':case.expected['frequency_per_week']},'주 '+str(case.expected['frequency_per_week'])+'회'),
        'privacy':({'scope':case.expected['privacy_scope']},'공개 범위'),
        'verification':({'method':case.expected['verification_method']},case.expected['verification_method'] or 'UNMENTIONED')}
    if case.expected['end_date']:targets['timeline']=({'start_date':case.confirmed_fixture.start_date,'end_date':case.expected['end_date']},case.expected['end_date'])
    for key,(value,text) in targets.items():
        messages=[m for m in case.group_input.conversation if text in m.text]
        if messages and all(v is not None for v in value.values()):
            fields[key]=AnalysisField(status='EXTRACTED',candidates=[Candidate(candidate_id=case.case_id+'-'+key,value=value,original_text=messages[0].text,evidence_refs=[messages[0].message_id],proposer_ids=[messages[0].speaker_id])])
        else:fields[key]=AnalysisField(status='MISSING',questions=['추가 확인 필요'])
    if case.expected['timeline_conflicted']:fields['timeline'].status='CONFLICTED'
    return ConversationAnalysis(summary='시나리오 작성 기준 입력 · 실제 AI 분석 결과 아님',fields=fields,members=case.group_input.members,evidence_ids=all_refs)


def authored_spec(case):
    from .contracts import PrepareRequest
    request=PrepareRequest(analysis=authored_analysis(case),confirmed_decisions=case.confirmed_fixture,member_profiles=case.profiles)
    result=GoalAI().prepare_goal_spec(request)
    if result.status!='OK':raise ValueError(result.model_dump_json())
    return result.data


def authored_group(case,spec):
    count=(spec.end_date-spec.start_date).days//7+1
    return GroupPlan(plan_id='authored-'+case.case_id,spec_id=spec.spec_id,**{k:getattr(spec,k) for k in ('goal','expected_outcome','completion_criteria','start_date','end_date','frequency_per_week','metric')},
        milestones=[{'title':m['title'],'due_date':m['due_date'],'completion_criteria':case.expected['criterion'],'evidence_refs':spec.evidence_ids} for m in spec.milestones],
        weekly_outline=[{'week':i,'outcome':case.expected['deliverable'],'tasks':case.expected['workflow'].split(' → ')} for i in range(1,count+1)],evidence_refs=spec.evidence_ids)


def entry_request(case):
    if case.entry_module=='A':return CoachRequest(**case.group_input.model_dump(),coaching_mode='DIALOGUE')
    if case.entry_module=='B':return case.group_input
    if case.entry_module=='C':return DraftRequest(analysis=authored_analysis(case),reference_date=case.group_input.reference_date,timezone=case.group_input.timezone)
    spec=authored_spec(case)
    if case.entry_module=='PLAN':return spec
    owner=case.profiles[0].member_id
    return MemberGoalRequest(diagnostic_mode='ADAPTIVE',goal_spec=spec,group_plan=authored_group(case,spec),owner_member_id=owner,
        conversation=case.private_conversations[owner][:1],reference_date=case.group_input.reference_date)
