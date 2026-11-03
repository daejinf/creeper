import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest
import requests

from wemeet_ai import GoalAI
from wemeet_ai.analysis import local_analysis, merge_analysis
from wemeet_ai.client import AIError, DeepSeekClient, RunStats
from wemeet_ai.contracts import (
    AnalysisField, CoachReply, CoachRequest, ConversationAnalysis, GroupPlan,
    MemberPlanDraft, MemberPlanRequest, Meta, PlanBundle, Result, RevisionRequest,
    ValidationRequest,
)
from wemeet_ai.examples import example_input, regression_conversation
from wemeet_ai.records import JsonlRecorder
from wemeet_ai.validation import validate_plan


class Response:
    def __init__(self, content="답변", *, status=200, finish="stop", envelope=None):
        self.status_code = status
        self.envelope = envelope or {"id": "provider-123", "model": "deepseek-flash", "choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    def json(self): return self.envelope


class Session:
    def __init__(self, *responses): self.responses, self.calls = list(responses), []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response


@pytest.fixture(autouse=True)
def fake_key(monkeypatch): monkeypatch.setenv("DEEPSEEK_API_KEY", "test-not-a-real-key")


def client(*responses):
    return DeepSeekClient(Session(*responses), sleeper=lambda _: None)


def group_fixture(spec):
    return GroupPlan(plan_id="group-1", spec_id=spec.spec_id, **{k: getattr(spec, k) for k in ["goal", "expected_outcome", "completion_criteria", "start_date", "end_date", "frequency_per_week", "metric"]}, milestones=[{"title": "시연", "due_date": spec.end_date, "completion_criteria": "확인된 결과물 완성", "evidence_refs": spec.evidence_ids}], weekly_outline=[{"week": i, "outcome": "기능 구현과 시연", "tasks": ["결과물을 실행하여 검증"]} for i in range(1, 3)], evidence_refs=spec.evidence_ids)


def member_fixture(spec, group, mid):
    return MemberPlanDraft(public_plan={"plan_id": "member-" + mid, "group_plan_id": group.plan_id, "member_id": mid, "weekly_targets": [{"week": i, "outcome": "개인 담당 결과물", "estimated_minutes": 60} for i in range(1, 3)], "first_week_todos": [{"scheduled_date": spec.start_date + timedelta(days=i), "action": "담당 기능을 구현하고 테스트", "amount_or_deliverable": "검증 가능한 동작 하나", "estimated_minutes": 30, "difficulty": "EASY", "completion_criteria": "테스트 결과 확인", "evidence_refs": spec.evidence_ids} for i in [0, 2]], "adaptation_rules": ["미완료 시 다음 주 범위를 다시 협의"], "public_rationale": "공동 시연 결과물에 필요한 기능을 분담합니다.", "evidence_refs": spec.evidence_ids}, private_notes=["당사자만 확인하는 조언"])


def test_no_web_or_database_import_in_core():
    root = Path(__file__).parents[1] / "src/wemeet_ai"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom): assert (node.module or "").split(".")[0] not in {"fastapi", "sqlalchemy", "app"}
            if isinstance(node, ast.Import): assert not {x.name.split(".")[0] for x in node.names} & {"fastapi", "sqlalchemy", "app"}


def test_missing_key_never_uses_fallback(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    c = client()
    with pytest.raises(AIError, match="not configured"): c.complete_text([])
    assert c.session.calls == []


@pytest.mark.parametrize("responses,code,count", [
    ([Response(status=401)], "AUTH_FAILED", 1),
    ([Response(status=403)], "AUTH_FAILED", 1),
    ([Response(status=400)], "INVALID_REQUEST", 1),
    ([Response(status=429), Response(status=429)], "RATE_LIMITED", 2),
    ([Response(status=503), Response(status=503)], "UPSTREAM_ERROR", 2),
    ([requests.Timeout(), requests.Timeout()], "TIMEOUT", 2),
    ([requests.ConnectionError(), requests.ConnectionError()], "NETWORK_ERROR", 2),
    ([Response(content="")], "EMPTY_RESPONSE", 1),
    ([Response(finish="length")], "TRUNCATED_OUTPUT", 1),
])
def test_transport_errors(responses, code, count):
    c = client(*responses)
    with pytest.raises(AIError) as exc: c.complete_text([])
    assert exc.value.code == code and len(c.session.calls) == count


def test_successful_retry_and_real_http_contract():
    c = client(requests.Timeout(), Response())
    result = c.complete_text([{"role": "user", "content": "목표"}])
    assert result.transport_attempts == 2 and result.usage.total_tokens == 15
    url, kwargs = c.session.calls[-1]
    assert url.endswith("/chat/completions")
    assert kwargs["timeout"] == (10, 60) and kwargs["json"]["messages"][0]["content"] == "목표"


def test_json_repair_is_separate_from_transport_retry():
    c = client(Response("broken"), Response('{"reply":"수정된 답변"}'))
    stats = RunStats()
    result = c.complete_json([], CoachReply, stats=stats)
    assert result.parsed_output.reply == "수정된 답변"
    assert stats.repair_attempts == 1 and stats.transport_attempts == 2
    assert stats.usage.total_tokens == 30
    assert c.session.calls[-1][1]["json"]["response_format"] == {"type": "json_object"}


def test_persistent_invalid_json_fails():
    with pytest.raises(AIError) as exc: client(Response("bad"), Response("bad")).complete_json([], CoachReply)
    assert exc.value.code == "INVALID_JSON"


@pytest.mark.parametrize("body", [{"choices": "bad"}, {"choices": [{"finish_reason": "stop", "message": "bad"}]}, {"usage": {"total_tokens": "bad"}}])
def test_malformed_provider_envelope_is_typed_error(body):
    with pytest.raises(AIError) as exc: client(Response(envelope=body)).complete_text([])
    assert exc.value.code == "INVALID_JSON"


def test_schema_repair_failure_uses_safe_error():
    with pytest.raises(AIError) as exc: client(Response('{}'), Response('{}')).complete_json([], CoachReply)
    assert exc.value.code == "SCHEMA_INVALID"


def test_coach_preserves_history_and_masks_contact():
    request = CoachRequest(**regression_conversation().model_dump(), scope="GROUP")
    request.conversation[0].text += " 연락은 someone@example.com"
    c = client(Response("이미 말한 기간 후보를 정리했어요. 어떤 결과물을 완성하고 싶나요?"))
    result = GoalAI(c).coach_reply(request)
    assert result.status == "OK"
    messages = c.session.calls[0][1]["json"]["messages"]
    assert len(messages) == len(request.conversation) + 1
    assert "someone@example.com" not in json.dumps(messages)


def test_private_coaching_rejects_other_members():
    with pytest.raises(ValueError): CoachRequest(**regression_conversation().model_dump(), scope="PRIVATE", owner_member_id="j")


def test_actual_user_conversation_regression():
    analysis = local_analysis(regression_conversation())
    assert analysis.fields["goal"].status == "PARTIAL"
    assert analysis.fields["timeline"].status == "CONFLICTED"
    assert len(analysis.fields["timeline"].candidates) == 3
    assert {"duration_days": 14} in [c.value for c in analysis.fields["timeline"].candidates]
    assert {"duration_days": 21} in [c.value for c in analysis.fields["timeline"].candidates]
    assert analysis.fields["frequency"].candidates[0].value == {"per_week": 3}
    assert analysis.fields["verification"].candidates[0].value == {"method": "이미지 첨부"}
    assert analysis.fields["checkin"].status == "MISSING"
    assert analysis.fields["privacy"].status == "MISSING"
    assert analysis.fields["frequency"].support_refs[0].member_id == "j"
    assert len(analysis.fields["verification"].support_refs) == 2
    assert all(c.inferred for c in analysis.fields["timeline"].candidates if "start_date" in c.value)


def test_unknown_evidence_rejected():
    request = regression_conversation()
    model = local_analysis(request)
    model.fields["goal"].candidates[0].evidence_refs = ["invented-id"]
    with pytest.raises(AIError): merge_analysis(request, model)


def test_prepare_collects_all_missing_values_without_ai_calls():
    c = client()
    request = example_input()
    request.confirmed_decisions.confirmed = False
    request.confirmed_decisions.start_date = "3주"
    request.confirmed_decisions.goal = None
    request.member_profiles = []
    result = GoalAI(c).prepare_goal_spec(request)
    assert result.status == "NEEDS_INPUT" and len(result.questions) >= 4
    assert c.session.calls == [] and result.meta.model is None


def test_non_numeric_project_never_receives_invented_metric():
    result = GoalAI(client()).prepare_goal_spec(example_input())
    assert result.status == "OK" and result.data.metric is None
    assert result.data.evidence_ids[-1].startswith("example") or any(x.startswith("confirmation:") for x in result.data.evidence_ids)


def test_group_prompt_contains_no_private_profiles():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    fixture = group_fixture(spec)
    c = client(Response(fixture.model_dump_json()))
    result = GoalAI(c).generate_group_plan(spec)
    assert result.status == "OK"
    prompt = json.dumps(c.session.calls[0][1]["json"], ensure_ascii=False)
    assert "화요일에는 30분까지만 가능" not in prompt
    assert "weekly_available_minutes" not in c.session.calls[0][1]["json"]["messages"][-1]["content"]


def test_semantic_plan_errors_are_repaired_once_and_counted_separately():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    valid = group_fixture(spec)
    invalid = valid.model_copy(deep=True)
    invalid.start_date += timedelta(days=1)
    c = client(Response(invalid.model_dump_json()), Response(valid.model_dump_json()))
    result = GoalAI(c).generate_group_plan(spec)
    assert result.status == "OK" and result.meta.plan_repair_attempts == 1
    assert result.meta.repair_attempts == 0 and result.meta.transport_attempts == 2
    assert "validation_errors" in c.session.calls[1][1]["json"]["messages"][-1]["content"]


def test_confirmed_values_cannot_be_silently_changed_after_repair():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    invalid = group_fixture(spec)
    invalid.goal = "unrelated goal"
    result = GoalAI(client(Response(invalid.model_dump_json()), Response(invalid.model_dump_json()))).generate_group_plan(spec)
    assert result.status == "FAILED" and result.error.code == "PLAN_INVALID" and result.data is None


def test_member_prompt_contains_only_owner_profile():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    spec.member_profiles[1].constraints = ["other-person-private-condition"]
    group = group_fixture(spec)
    fixture = member_fixture(spec, group, spec.member_profiles[0].member_id)
    c = client(Response(fixture.model_dump_json()))
    result = GoalAI(c).generate_member_plan(MemberPlanRequest(goal_spec=spec, group_plan=group, member_profile=spec.member_profiles[0]))
    assert result.status == "OK"
    prompt = json.dumps(c.session.calls[0][1]["json"], ensure_ascii=False)
    assert "other-person-private-condition" not in prompt
    assert "private_notes" not in result.data.public_plan.model_dump_json()


def test_validator_reports_time_date_privacy_coverage_and_unknown_refs():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    group = group_fixture(spec)
    draft = member_fixture(spec, group, spec.member_profiles[0].member_id)
    draft.public_plan.first_week_todos[0].estimated_minutes = 999
    draft.public_plan.first_week_todos[0].scheduled_date = spec.end_date + timedelta(days=1)
    draft.public_plan.first_week_todos[0].evidence_refs = ["unknown"]
    draft.public_plan.public_rationale = spec.member_profiles[0].constraints[0]
    draft.public_plan.first_week_todos.append(draft.public_plan.first_week_todos[0].model_copy(deep=True))
    codes = {i.code for i in validate_plan(spec, group, [draft]).issues}
    assert {"TIME_BUDGET_EXCEEDED", "INVALID_FIRST_WEEK_DATE", "PRIVATE_CONTENT_EXPOSED", "MEMBER_COVERAGE", "INVALID_EVIDENCE", "DUPLICATE_TASK"} <= codes


def test_success_and_failed_member_are_preserved_for_resume():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    group = group_fixture(spec)
    one = member_fixture(spec, group, spec.member_profiles[0].member_id)
    two = member_fixture(spec, group, spec.member_profiles[1].member_id)
    existing = PlanBundle(goal_spec=spec, group_plan=group, member_results={one.public_plan.member_id: Result(status="OK", data=one, meta=Meta(run_id="old-run", task="member-plan", prompt_version="v1"))}, retry_member_ids=[two.public_plan.member_id], validation=validate_plan(spec, group, [one]))
    c = client(Response(two.model_dump_json()))
    resumed = GoalAI(c).generate_bundle(spec, existing)
    assert resumed.validation.valid and not resumed.retry_member_ids
    assert resumed.member_results[one.public_plan.member_id].meta.run_id == "old-run"
    assert len(c.session.calls) == 1


def test_revision_is_immutable_and_increments_version():
    spec = GoalAI(client()).prepare_goal_spec(example_input()).data
    group = group_fixture(spec)
    old = member_fixture(spec, group, spec.member_profiles[0].member_id)
    before = old.model_dump_json()
    c = client(Response(old.model_dump_json()))
    revised = GoalAI(c).revise_plan(RevisionRequest(confirmed_spec=spec, group_plan=group, member_plan=old, feedback="행동 설명을 더 구체적으로"))
    assert revised.status == "OK" and old.model_dump_json() == before
    assert revised.data.public_plan.previous_plan_id == old.public_plan.plan_id
    assert revised.data.public_plan.revision == 2


def test_logs_omit_private_inputs_and_keys(tmp_path):
    path = tmp_path / "runs.jsonl"
    recorder = JsonlRecorder(path)
    result = GoalAI(client(Response("어떤 목표인가요?")), recorder).coach_reply(CoachRequest(**regression_conversation().model_dump()))
    text = path.read_text(encoding="utf-8")
    assert result.meta.run_id in text
    assert "오늘은 위밋" not in text and "test-not-a-real-key" not in text


def test_log_failure_does_not_destroy_result():
    class BrokenRecorder:
        def record(self, *args, **kwargs): raise OSError("disk full")
    result = GoalAI(client(), BrokenRecorder()).prepare_goal_spec(example_input())
    assert result.status == "OK" and result.warnings


def test_web_is_thin_typed_adapter():
    from fastapi.testclient import TestClient
    from wemeet_ai.adapters.web import app
    with TestClient(app) as web:
        assert web.get("/").status_code == 200
        response = web.post("/ai/v1/goal-spec", json=example_input().model_dump(mode="json"))
        assert response.status_code == 200 and response.json()["status"] == "OK"
        assert {"/ai/v1/goal-spec/draft", "/ai/v1/intake/analyze", "/ai/v1/workflows/goal-draft", "/ai/v1/handoff"} <= set(web.get("/openapi.json").json()["paths"])
        assert web.get("/ai/v1/examples/invalid").status_code == 404
