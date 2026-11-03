import json

import pytest

from test_core import client, Response, group_fixture, member_fixture
from wemeet_ai import GoalAI
from wemeet_ai.analysis import local_analysis, merge_analysis
from wemeet_ai.contracts import CoachRequest, CoachState, DraftField, DraftRequest, GoalSpecDraft, HandoffRequest, PrivateIntakeAnalysis, SharedContext, WorkflowRequest
from wemeet_ai.examples import design_conversation, example_input, regression_conversation
from wemeet_ai.tracing import MemoryTraceSink


@pytest.fixture(autouse=True)
def key(monkeypatch): monkeypatch.setenv("DEEPSEEK_API_KEY", "test-not-a-real-key")


def private_request():
    group = design_conversation()
    return CoachRequest(members=group.members, reference_date=group.reference_date,
        conversation=[{"message_id":"private-1","speaker_id":"j","role":"user","text":"UI와 PPT 둘 다야. 직접 계획을 만들어줘", "created_at":"2026-10-28T10:00:00+09:00"}],
        scope="PRIVATE", owner_member_id="j",
        shared_context=SharedContext(goal="위밋 데모", dates={"development":"2026-11-10","submission":"2026-11-15"}, roles={"j":"UI와 PPT 디자인","s":"DB"}))


def draft_fixture():
    example = example_input()
    return GoalSpecDraft(summary="AI 초안", fields={k:DraftField(value=v, origin="AI_SUGGESTED", reason="제안") for k,v in example.confirmed_decisions.model_dump(mode="json").items() if k!="confirmed"})


def test_plan_request_proposes_without_another_question():
    request = private_request()
    request.conversation[0].text = "화면 UI랑 PPT 싹 다야. 너가 직접 계획을 만들어줘."
    c = client(Response("가정: 메인을 우선 시안으로 제안합니다. 11/3까지 메인·서브 시안, 11/10 이후 PPT를 정리합니다."))
    result = GoalAI(c).coach_reply(request)
    assert result.status == "OK" and result.data.kind == "PLAN_PROPOSAL"
    assert result.data.question is None and result.data.next_action == "REVIEW_GOAL_DRAFT"
    prompt = c.session.calls[0][1]["json"]["messages"][0]["content"]
    assert "2026-11-10" in prompt and "UI와 PPT" in prompt
    assert '"s": "DB"' not in prompt


def test_question_budget_repairs_once_then_returns_advice():
    request = private_request()
    request.conversation[0].text = "백지 디자인이야"
    request.coach_state = CoachState(consecutive_questions=2,last_question_topic="현재 상태는?",answered_topics=["UI인가요?"])
    c = client(Response("그렇군요. 메인에 들어갈 것은 무엇인가요?"), Response("가정으로 메인 구성 3개를 제안하고 시안부터 만들겠습니다."))
    result = GoalAI(c).coach_reply(request)
    assert result.status == "OK" and result.data.question is None
    assert result.data.updated_coach_state.consecutive_questions == 0
    assert "현재 상태는?" in result.data.updated_coach_state.answered_topics
    assert len(c.session.calls) == 2


def test_repeated_policy_failure_is_not_appended_as_success():
    request = private_request()
    request.conversation[0].text = "계획을 만들어줘"
    result = GoalAI(client(Response("마지막으로 하나만 확인할게요. 어떤 화면인가요?"),Response("어떤 화면인가요?"))).coach_reply(request)
    assert result.status == "FAILED" and result.error.code == "COACH_POLICY"


def test_milestone_dates_are_not_treated_as_one_conflict():
    request = design_conversation()
    analysis = local_analysis(request)
    values = analysis.fields["milestones"].candidates
    assert any(c.value["due_date"]=="2026-11-10" for c in values)
    assert any(c.value["due_date"]=="2026-11-15" for c in values)
    assert not any(c.value["due_date"]=="2026-10-28" for c in values)
    assert analysis.fields["milestones"].status != "CONFLICTED"
    assert any(c.value.get("member_id")=="j" for c in analysis.fields["roles"].candidates)


def test_pasted_ai_proposal_is_not_human_confirmation():
    request=regression_conversation()
    request.conversation[0].text="AI: 프로젝트 목표는 문제 20개를 푸는 것으로 제안해요."
    analysis=local_analysis(request)
    assert analysis.fields["goal"].candidates[0].origin=="ASSISTANT_PROPOSAL"


def test_ai_draft_preserves_reviewed_edits_and_never_auto_confirms():
    analysis=local_analysis(design_conversation())
    previous=draft_fixture()
    previous.fields["goal"].value="사람이 직접 수정한 목표"
    previous.fields["goal"].review_status="EDITED"
    result=GoalAI(client(Response(draft_fixture().model_dump_json()))).draft_goal_spec(DraftRequest(analysis=analysis,previous_draft=previous))
    assert result.status=="OK"
    assert result.data.fields["goal"].value=="사람이 직접 수정한 목표"
    assert result.data.fields["verification_method"].review_status=="PENDING"
    request=example_input();request.reviewed_draft=result.data
    assert GoalAI(client()).prepare_goal_spec(request).status=="NEEDS_INPUT"


def test_private_analysis_contains_only_owners_input():
    request=private_request();request.conversation[0].text="처음 시작하고 일주일에 60분 가능해요. 비공개 제약 문장이에요."
    payload=PrivateIntakeAnalysis(owner_member_id="j",fields={"weekly_available_minutes":DraftField(value=60,origin="EXTRACTED",evidence_refs=["private-1"]),"constraints":DraftField(value=["비공개 제약 문장이에요."],origin="EXTRACTED",evidence_refs=["private-1"])})
    c=client(Response(payload.model_dump_json()))
    result=GoalAI(c).analyze_private_intake(request)
    assert result.status=="OK" and result.data.fields["current_level"].value is None
    prompt=c.session.calls[0][1]["json"]["messages"][-1]["content"]
    assert "UI와 PPT" not in prompt and '"s"' not in prompt


def test_trace_contains_actual_provider_request_and_response_but_no_key():
    sink=MemoryTraceSink();request=private_request();request.conversation[0].text="계획을 만들어줘"
    result=GoalAI(client(Response("메인 시안을 먼저 작성하고 PPT는 개발 후 초안을 만들겠습니다.")),trace_sink=sink).coach_reply(request)
    events={event["event"] for event in result.traces}
    assert {"provider.request","provider.response","input.prepared","output.parsed","run.completed"}<=events
    assert all(e["trace_id"]==result.meta.trace_id for e in result.traces)
    serialized=json.dumps(result.traces,ensure_ascii=False)
    assert "test-not-a-real-key" not in serialized and "Authorization" not in serialized
    assert "provider-123" in serialized


def test_workflow_links_analysis_and_draft_runs():
    request=WorkflowRequest(**design_conversation().model_dump())
    analysis=local_analysis(design_conversation())
    result=GoalAI(client(Response(analysis.model_dump_json()),Response(draft_fixture().model_dump_json())),trace_sink=MemoryTraceSink()).build_goal_draft(request)
    assert result.status=="OK"
    assert result.data.analysis_result.meta.parent_run_id==result.meta.run_id
    assert result.data.draft_result.meta.trace_id==result.meta.trace_id
    assert result.meta.usage.total_tokens==30


def test_handoff_separates_public_and_owner_private_payloads():
    spec=GoalAI(client()).prepare_goal_spec(example_input()).data
    group=group_fixture(spec)
    plans=[member_fixture(spec,group,p.member_id) for p in spec.member_profiles]
    result=GoalAI(client(),trace_sink=MemoryTraceSink()).build_handoff(HandoffRequest(goal_spec=spec,group_plan=group,member_plans=plans,source_fingerprint="r1",current_source_fingerprint="r1"))
    assert result.status=="OK" and result.data.group_file["integration_ready"]
    assert "member_profiles" not in json.dumps(result.data.group_file)
    secret=spec.member_profiles[0].constraints[0]
    assert secret not in json.dumps(result.data.group_file,ensure_ascii=False)
    assert secret in json.dumps(result.data.private_files[spec.member_profiles[0].member_id],ensure_ascii=False)
    assert secret not in json.dumps(result.traces,ensure_ascii=False)


def test_stale_or_incomplete_handoff_is_diagnostic_only():
    result=GoalAI(client()).build_handoff(HandoffRequest(source_fingerprint="r1",current_source_fingerprint="r2"))
    assert result.status=="OK" and not result.data.group_file["integration_ready"]
    assert {i["code"] for i in result.data.group_file["validation"]["issues"]}=={"STALE_INPUT","NOT_READY"}


def test_handoff_rejects_reviewed_value_different_from_spec():
    spec=GoalAI(client()).prepare_goal_spec(example_input()).data
    group=group_fixture(spec)
    reviewed=GoalSpecDraft(summary="변경된 카드",fields={"goal":DraftField(value="명세와 다른 목표",origin="AI_SUGGESTED",review_status="EDITED")})
    result=GoalAI(client()).build_handoff(HandoffRequest(goal_spec=spec,group_plan=group,reviewed_draft=reviewed))
    assert not result.data.group_file["integration_ready"]
    assert any(i["code"]=="REVIEW_MISMATCH" for i in result.data.group_file["validation"]["issues"])


def test_invalid_confirmed_milestone_is_caught_before_provider_call():
    request=example_input()
    request.confirmed_decisions.milestones=[{"title":"개발 완료","due_date":"2028-12-01"}]
    c=client()
    result=GoalAI(c).prepare_goal_spec(request)
    assert result.status=="NEEDS_INPUT" and any("milestones" in q for q in result.questions)
    assert not c.session.calls


def test_reported_long_interview_history_stops_asking_and_remembers_answers():
    from wemeet_ai.examples import design_coaching_regression
    c=client(Response("메인 UI 시안을 먼저 만들고 같은 스타일로 서브 화면을 확장합니다. 개발 완료 뒤 PPT에 시연 화면을 반영하는 초안을 제안합니다."))
    result=GoalAI(c).coach_reply(design_coaching_regression())
    assert result.status=="OK" and result.data.question is None
    assert result.data.kind=="PLAN_PROPOSAL"
    assert len(result.data.updated_coach_state.answered_topics)>=5
