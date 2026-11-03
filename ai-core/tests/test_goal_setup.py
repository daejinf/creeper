"""Actual reported regression and stateless SW integration, with injected HTTP only."""
import pytest
from test_core import client, Response, fake_key
from test_interactions import draft_fixture
from wemeet_ai import GoalAI
from wemeet_ai.analysis import local_analysis, merge_analysis
from wemeet_ai.contracts import *
from wemeet_ai.examples import design_conversation


def setup_input():
    return design_conversation()


def analysis_of(request):
    return merge_analysis(request, ConversationAnalysis(summary='디자인 일정',fields={}))


def fixture_for(request):
    draft=draft_fixture()
    draft.fields['goal']=DraftField(value='위밋 프로젝트 데모',origin='AI_SUGGESTED')
    draft.fields['start_date'].value='2026-10-28';draft.fields['end_date'].value='2026-11-15'
    draft.fields['milestones'].value=[];draft.fields['roles'].value={}
    draft.fields['metric'].value=None;draft.fields['frequency_per_week'].value=None
    return draft


def reviewed_flow():
    request=setup_input();model=analysis_of(request);draft=fixture_for(request)
    c=client(Response(model.model_dump_json()),Response(draft.model_dump_json()))
    ai=GoalAI(c)
    result=ai.advance_goal_setup(GoalSetupRequest(action='REVIEW',**request.model_dump()))
    assert result.status=='OK',result.model_dump()
    return ai,c,result


def test_reported_design_dates_are_project_and_checkpoint_not_conflict():
    analysis=analysis_of(setup_input())
    assert analysis.fields['timeline'].status=='EXTRACTED'
    assert analysis.fields['timeline'].candidates[0].value=={'start_date':'2026-10-28','end_date':'2026-11-15'}
    milestones={c.value['title']:c.value['due_date'] for c in analysis.fields['milestones'].candidates}
    assert milestones['개발 완료']=='2026-11-10' and milestones['중간 목표']=='2026-11-03'
    assert analysis.fields['success'].candidates and len(analysis.fields['roles'].candidates)==4


def test_review_proposes_missing_required_values_and_confirm_needs_acceptance():
    ai,c,result=reviewed_flow();state=result.data.state
    assert state.draft.fields['execution_method'].origin=='AI_SUGGESTED'
    assert state.draft.fields['execution_method'].evidence_refs==[]
    assert state.draft.fields['start_date'].value=='2026-10-28'
    assert all(f.review_status=='PENDING' for f in state.draft.fields.values())
    before=len(c.session.calls)
    blocked=ai.advance_goal_setup(GoalSetupRequest(action='CONFIRM',state=state,actor_member_id='j'))
    assert blocked.status=='NEEDS_INPUT' and blocked.data.group_spec is None
    assert len(c.session.calls)==before


def test_sw_only_action_state_to_review_accept_confirm_no_extra_http():
    ai,c,result=reviewed_flow();state=result.data.state
    accepted=ai.advance_goal_setup(GoalSetupRequest(action='RESPOND',state=state,actor_member_id='j',
        response=GoalSetupResponse(operation='ACCEPT',fields=list(state.draft.fields))))
    assert accepted.status=='OK' and accepted.data.can_generate_group_plan
    confirmed=ai.advance_goal_setup(GoalSetupRequest(action='CONFIRM',state=accepted.data.state,actor_member_id='j'))
    assert confirmed.status=='OK' and confirmed.data.group_spec
    duplicate=ai.advance_goal_setup(GoalSetupRequest(action='CONFIRM',state=confirmed.data.state,actor_member_id='j'))
    assert duplicate.data.group_spec.spec_id==confirmed.data.group_spec.spec_id
    assert len(c.session.calls)==2
    assert confirmed.data.state.review_history[-1]['actor_member_id']=='j'
    # Same REVIEW does not invoke B/C again or reset acceptance.
    repeated=ai.advance_goal_setup(GoalSetupRequest(action='REVIEW',state=confirmed.data.state))
    assert repeated.status=='OK' and repeated.data.state.draft.fields['goal'].review_status=='ACCEPTED'
    assert len(c.session.calls)==2


def test_invalid_inline_date_keeps_previous_state_and_submitted_value():
    ai,c,result=reviewed_flow();state=result.data.state;old=state.draft.fields['start_date'].value
    out=ai.advance_goal_setup(GoalSetupRequest(action='RESPOND',state=state,actor_member_id='j',
        response=GoalSetupResponse(operation='EDIT',values={'start_date':'내일쯤'})))
    assert out.status=='NEEDS_INPUT' and out.data.state.draft.fields['start_date'].value==old
    assert out.data.needed_inputs[0]['submitted_value']=='내일쯤'
    assert len(c.session.calls)==2


def test_inline_answer_can_edit_without_returning_to_A():
    ai,c,result=reviewed_flow()
    out=ai.advance_goal_setup(GoalSetupRequest(action='RESPOND',state=result.data.state,actor_member_id='j',
        response=GoalSetupResponse(operation='ANSWER',fields=['execution_method'],text='UI 시안 전달 후 프론트와 API를 연결한다.')))
    assert out.status=='OK' and out.data.state.draft.fields['execution_method'].review_status=='EDITED'
    assert out.data.state.draft.fields['execution_method'].value.startswith('UI 시안')
    assert len(c.session.calls)==2


def test_changed_conversation_does_not_confirm_stale_draft():
    ai,c,result=reviewed_flow();messages=list(result.data.state.conversation)
    messages.append(Message(message_id='change',speaker_id='j',role='user',text='제출일을 바꿀게요.',created_at=messages[-1].created_at))
    out=ai.advance_goal_setup(GoalSetupRequest(action='CONFIRM',state=result.data.state,conversation=messages,actor_member_id='j'))
    assert out.status=='NEEDS_INPUT' and out.data.allowed_actions==['REVIEW']
    assert len(c.session.calls)==2


def test_review_failure_retains_successful_analysis_for_resume():
    request=setup_input();model=analysis_of(request)
    c=client(Response(model.model_dump_json()),Response('',status=401))
    result=GoalAI(c).advance_goal_setup(GoalSetupRequest(action='REVIEW',**request.model_dump()))
    assert result.status=='FAILED' and result.data.state.analysis and result.data.state.draft is None


def test_assisted_without_a_meaningful_goal_does_not_create_project():
    request=setup_input();request.conversation=request.conversation[:1];request.conversation[0].text='안녕'
    model=analysis_of(request);draft=fixture_for(request)
    out=GoalAI(client(Response(draft.model_dump_json()))).draft_goal_spec(DraftRequest(analysis=model,draft_mode='ASSISTED'))
    assert out.status=='OK' and out.data.fields['goal'].value is None


def test_coach_facade_duplicate_input_is_code_only_and_does_not_append_twice():
    request=setup_input();c=client(Response('메인 화면부터 시안을 만들고 API 연결 범위를 함께 정해보세요.'))
    ai=GoalAI(c);first=ai.advance_goal_setup(GoalSetupRequest(action='COACH',**request.model_dump()))
    second=ai.advance_goal_setup(GoalSetupRequest(action='COACH',state=first.data.state))
    assert second.status=='OK' and second.data.reply==first.data.reply
    assert len(second.data.state.conversation)==len(first.data.state.conversation) and len(c.session.calls)==1


def test_natural_date_answer_is_interpreted_but_needs_human_review():
    ai,c,result=reviewed_flow()
    c.session.responses.append(Response(DraftField(value='2026-11-01',origin='AI_SUGGESTED').model_dump_json()))
    out=ai.advance_goal_setup(GoalSetupRequest(action='RESPOND',state=result.data.state,actor_member_id='j',
        response=GoalSetupResponse(operation='ANSWER',fields=['start_date'],text='다음 주 일요일부터')))
    assert out.status=='OK',out.model_dump()
    assert out.data.state.draft.fields['start_date'].value=='2026-11-01'
    assert out.data.state.draft.fields['start_date'].review_status=='PENDING' and len(c.session.calls)==3


def test_description_alias_and_goal_with_trailing_question_still_meaningful():
    from wemeet_ai.grounding import source_catalog
    request=setup_input();request.conversation=request.conversation[:1];m=request.conversation[0]
    m.text='동아리 소개 홈페이지를 함께 만들고 싶어. 어떻게 진행할까?'
    analysis=merge_analysis(request,ConversationAnalysis(summary='홈페이지',fields={'goal':AnalysisField(status='PARTIAL',candidates=[
        Candidate(candidate_id='model',value={'description':'동아리 소개 홈페이지 제작'},original_text=m.text,evidence_refs=[m.message_id],proposer_ids=['j'])])}))
    assert any(o['value']=='동아리 소개 홈페이지 제작' for o in source_catalog(analysis)['goal'])


def test_paraphrased_coaching_note_dropped_without_discarding_human_facts():
    request=setup_input();request.conversation.append(Message(message_id='ai',role='assistant',text='UI 시안을 먼저 공유하는 방법을 추천합니다.',created_at=request.conversation[0].created_at))
    model=ConversationAnalysis(summary='분석',fields={},coaching_notes=[CoachingNote(assistant_message_id='ai',suggestion_text='시안 공유 우선',disposition='SUPPORTED')])
    merged=merge_analysis(request,model)
    assert merged.fields['timeline'].status=='EXTRACTED'
    assert merged.coaching_notes[0].suggestion_text==request.conversation[-1].text and merged.coaching_notes[0].disposition=='UNDECIDED'


def test_goal_setup_http_thin_wrapper(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from wemeet_ai.adapters.web import app,service
    ai,c,result=reviewed_flow();app.dependency_overrides[service]=lambda:ai
    try:
        response=TestClient(app).post('/ai/v1/goal-setup',json=GoalSetupRequest(action='CONFIRM',state=result.data.state,actor_member_id='j').model_dump(mode='json'))
        assert response.status_code==200 and response.json()['status']=='NEEDS_INPUT'
        assert response.json()['data']['needed_inputs'] and len(c.session.calls)==2
    finally:app.dependency_overrides.pop(service,None)


def test_calendar_flags_wrong_weekday_without_inventing_a_date():
    from datetime import date
    from wemeet_ai.calendar_context import calendar_context,weekday_errors
    facts=calendar_context(date(2026,11,1),'11/7 중간 목표, 11/14 개발 완료')
    assert facts['2026-11-07']=='토요일'
    errors=list(weekday_errors({'criterion':'11/7 금요일 인증'},date(2026,11,1)))
    assert len(errors)==1 and '토요일' in errors[0][1]


def test_reviewed_goal_can_seed_assisted_completion_after_inline_answer():
    request=setup_input();request.conversation=request.conversation[:1];request.conversation[0].text='안녕'
    analysis=analysis_of(request);previous=GoalSpecDraft(summary='직접 답변',fields={'goal':DraftField(value='동아리 소개 홈페이지',origin='EXTRACTED',review_status='EDITED')})
    output=fixture_for(request)
    result=GoalAI(client(Response(output.model_dump_json()))).draft_goal_spec(DraftRequest(analysis=analysis,previous_draft=previous,draft_mode='ASSISTED'))
    assert result.status=='OK' and result.data.fields['goal'].value=='동아리 소개 홈페이지'
    assert result.data.fields['execution_method'].value and result.data.fields['execution_method'].origin=='AI_SUGGESTED'
