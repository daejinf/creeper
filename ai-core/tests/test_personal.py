import json

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from test_core import Response, client, fake_key, group_fixture, member_fixture
from test_missing_flow import prepared
from wemeet_ai import GoalAI
from wemeet_ai.contracts import (
    AnswerOption, ButtonAnswer, ContributionGoal, DraftField, HandoffRequest, MemberCoachModel,
    MemberCoachState, MemberGoalDraft, MemberGoalRequest, MemberPlanRequest, Message, PersonalQuestion,
)
from wemeet_ai.tracing import MemoryTraceSink


def request():
    spec, _, _ = prepared()
    owner = spec.members[0].member_id
    return MemberGoalRequest(goal_spec=spec, group_plan=group_fixture(spec), owner_member_id=owner,
        reference_date="2026-10-28", conversation=[Message(message_id="p1", speaker_id=owner, role="user", text="처음이고 주말에 두 시간 가능해요.", created_at="2026-10-28T10:00:00+09:00")])


def model(question=True, fields=None):
    extracted = {} if question else {"current_level": DraftField(value="초보", evidence_refs=["p1"]), "weekly_available_minutes": DraftField(value=120, evidence_refs=["p1"])}
    extracted.update(fields or {})
    return MemberCoachModel(reply="담당 결과물의 작은 동작부터 구현하는 기여 초안을 제안합니다.",
        question=PersonalQuestion(target_field="weekly_available_minutes", text="주당 얼마나 진행할 수 있나요?", options=[AnswerOption(label="두 시간", value=120)]) if question else None,
        extracted_fields=extracted)


def answer_request(action="VALUE", value=120, field="weekly_available_minutes"):
    req=request()
    req.state=MemberCoachState(owner_member_id=req.owner_member_id, group_plan_id=req.group_plan.plan_id,
        current_question=PersonalQuestion(question_id="q1",target_field=field,text="확인해주세요.",options=[AnswerOption(option_id="o1",label="선택 답변",action=action,value=value)]))
    req.answer=ButtonAnswer(question_id="q1",option_ids=["o1"],message_id="p1")
    return req


def contribution(req):
    return ContributionGoal(owner_member_id=req.owner_member_id,group_plan_id=req.group_plan.plan_id,title="담당 기능 데모",deliverable="담당 기능과 검증 결과",completion_criteria=["실행 가능한 동작 시연"],public_rationale="공동 데모 완성에 기여",evidence_refs=req.goal_spec.evidence_ids)


def test_ai_question_options_get_code_ids_and_unknown_control():
    result=GoalAI(client(Response(model().model_dump_json())),trace_sink=MemoryTraceSink()).coach_member_goal(request())
    assert result.status=="OK",result.model_dump_json()
    q=result.data.state.current_question
    assert q.question_id and all(o.option_id for o in q.options)
    assert any(o.action=="UNKNOWN" for o in q.options)


def test_button_and_free_text_have_same_minutes_and_explicit_values_win():
    req=answer_request()
    result=GoalAI(client(Response(model(False,{"weekly_available_minutes":DraftField(value=180,evidence_refs=["p1"])}).model_dump_json()))).coach_member_goal(req)
    assert result.status=="OK" and result.data.state.fields["weekly_available_minutes"].value==120
    free=GoalAI(client(Response(model(False,{"weekly_available_minutes":DraftField(value=120,evidence_refs=["p1"])}).model_dump_json()))).coach_member_goal(request())
    assert free.data.state.fields["weekly_available_minutes"].value==120


@pytest.mark.parametrize("action,value,field,expected",[("UNKNOWN",None,"weekly_available_minutes","UNKNOWN"),("SKIP",None,"constraints","SKIPPED"),("VALUE",[],"constraints","KNOWN")])
def test_unknown_skip_and_no_constraints_remain_distinct(action,value,field,expected):
    req=answer_request(action,value,field)
    result=GoalAI(client(Response(model(False).model_dump_json()))).coach_member_goal(req)
    assert result.status=="OK", result.model_dump_json()
    assert result.data.state.answer_states[field]==expected
    assert result.data.state.fields[field].value==value


def test_duplicate_button_is_noop_without_new_provider_call():
    req=answer_request();req.state.answered_question_ids=["q1"]
    c=client();result=GoalAI(c).coach_member_goal(req)
    assert result.status=="OK" and not c.session.calls


@pytest.mark.parametrize("change",["question","option","invalid-value","multiple"])
def test_invalid_selections_fail_before_provider(change):
    req=answer_request()
    if change=="question":req.answer.question_id="old-question"
    if change=="option":req.answer.option_ids=["not-an-option"]
    if change=="invalid-value":req.state.current_question.options[0].value=-20
    if change=="multiple":req.answer.option_ids=["o1","o1"]
    c=client();result=GoalAI(c).coach_member_goal(req)
    assert result.status=="FAILED" and not c.session.calls


def test_two_question_budget_repairs_into_proposal_and_does_not_repeat_answer():
    req=request();req.state=MemberCoachState(group_plan_id=req.group_plan.plan_id,consecutive_questions=2)
    c=client(Response(model().model_dump_json()),Response(model(False).model_dump_json()))
    result=GoalAI(c).coach_member_goal(req)
    assert result.status=="OK" and result.data.state.current_question is None
    assert result.meta.plan_repair_attempts==1


def test_plan_request_cannot_ask_question():
    req=request();req.conversation[0].text="실행 계획을 만들어줘"
    result=GoalAI(client(Response(model(False).model_dump_json()))).coach_member_goal(req)
    assert result.status=="OK" and result.data.state.current_question is None


def test_private_state_and_messages_cannot_cross_members():
    req=request();req.state.owner_member_id="someone-else"
    c=client();result=GoalAI(c).coach_member_goal(req)
    assert result.error.code=="PRIVATE_SCOPE" and not c.session.calls
    payload=request().model_dump(mode="json");payload["conversation"][0]["speaker_id"]="another-member"
    with pytest.raises(ValidationError):MemberGoalRequest.model_validate(payload)


def test_legacy_spec_other_private_profiles_never_enter_provider_payload():
    from wemeet_ai.examples import example_input
    spec=GoalAI(client()).prepare_goal_spec(example_input()).data
    spec.member_profiles[1].constraints=["다른 구성원의 비밀 조건"]
    req=request().model_copy(update={"goal_spec":spec,"group_plan":group_fixture(spec)})
    c=client(Response(model(False).model_dump_json()));result=GoalAI(c).coach_member_goal(req)
    assert result.status=="OK"
    assert "다른 구성원의 비밀 조건" not in json.dumps(c.session.calls,ensure_ascii=False)


def test_personal_draft_and_reviewed_contribution_feed_plan_and_handoff():
    req=request();profile_fields={"current_level":DraftField(value="초보",evidence_refs=["p1"]),"weekly_available_minutes":DraftField(value=120,evidence_refs=["p1"])}
    draft=MemberGoalDraft(owner_member_id=req.owner_member_id,group_plan_id=req.group_plan.plan_id,contribution=contribution(req),profile_fields=profile_fields)
    ai=GoalAI(client(Response(draft.model_dump_json())))
    result=ai.draft_member_goal(req)
    assert result.status=="OK" and not result.data.missing_fields
    _,profiles,_=prepared();profile=profiles[0].model_copy(update={"current_level":"초보","weekly_available_minutes":120})
    group=req.group_plan;member=member_fixture(req.goal_spec,group,profile.member_id)
    c=client(Response(member.model_dump_json()));goal=result.data.contribution
    pending=GoalAI(c).generate_member_plan(MemberPlanRequest(goal_spec=req.goal_spec,group_plan=group,member_profile=profile,contribution_goal=goal))
    assert pending.status=="FAILED" and not c.session.calls
    goal.review_status="ACCEPTED"
    generated=GoalAI(c).generate_member_plan(MemberPlanRequest(goal_spec=req.goal_spec,group_plan=group,member_profile=profile,contribution_goal=goal))
    assert generated.status=="OK",generated.model_dump_json()
    assert generated.data.contribution_goal.title==goal.title
    assert "confirmed_contribution_goal" in json.dumps(c.session.calls)
    package=GoalAI(client(),trace_sink=MemoryTraceSink()).build_handoff(HandoffRequest(goal_spec=req.goal_spec,group_plan=group,member_profiles=[profile],member_plans=[generated.data],personal_drafts={profile.member_id:result.data},personal_conversations={profile.member_id:req.conversation}))
    assert package.data.group_file["group_payload"]["contribution_goals"][0]["title"]==goal.title
    assert req.conversation[0].text not in json.dumps(package.data.group_file,ensure_ascii=False)
    assert package.data.private_files[profile.member_id]["private_payload"]["conversation"]
    assert req.conversation[0].text not in json.dumps(package.traces,ensure_ascii=False)


def test_group_change_preserves_personal_facts_and_resets_old_question():
    req=answer_request();req.answer=None;req.state.group_plan_id="old-plan"
    req.state.fields["current_level"]=DraftField(value="초보",evidence_refs=["p1"])
    result=GoalAI(client(Response(model(False).model_dump_json()))).coach_member_goal(req)
    assert result.status=="OK" and result.data.state.fields["current_level"].value=="초보"
    assert result.data.state.group_plan_id==req.group_plan.plan_id


def test_personal_apis_and_cli_schema_are_available():
    from wemeet_ai.adapters.web import app
    with TestClient(app) as web:
        paths=web.get('/openapi.json').json()['paths']
        assert '/ai/v1/member-goal/coach' in paths and '/ai/v1/member-goal/draft' in paths


def test_missing_essential_conditions_take_priority_over_contribution_question():
    req=request()
    req.conversation[0].text="공동 계획에서 내 역할을 같이 정해보자."
    wrong=model()
    wrong.question=PersonalQuestion(target_field="contribution_candidate", text="어떤 역할을 맡고 싶나요?", options=[AnswerOption(label="입력 화면",value="입력 화면 구현")])
    correct=model()
    c=client(Response(wrong.model_dump_json()),Response(correct.model_dump_json()))
    result=GoalAI(c).coach_member_goal(req)
    assert result.status=="OK",result.model_dump_json()
    assert result.data.state.current_question.target_field=="weekly_available_minutes"
    assert result.meta.plan_repair_attempts==1


def test_unknown_required_field_is_not_immediately_asked_again():
    req=answer_request("UNKNOWN",None)
    wrong=model()
    c=client(Response(wrong.model_dump_json()),Response(model(False).model_dump_json()))
    result=GoalAI(c).coach_member_goal(req)
    assert result.status=="OK",result.model_dump_json()
    assert result.data.state.current_question is None
    assert result.data.state.answer_states["weekly_available_minutes"]=="UNKNOWN"
    assert result.data.missing_fields==["weekly_available_minutes"]
