"""MISSING is analysis state, not a dependency on private profiles."""
import json

from fastapi.testclient import TestClient

from test_core import client, Response, group_fixture, member_fixture, fake_key
from test_interactions import draft_fixture
from wemeet_ai import GoalAI
from wemeet_ai.analysis import local_analysis
from wemeet_ai.contracts import (
    DraftField, DraftRequest, GoalSpecDraft, GroupPrepareRequest, HandoffRequest,
    MemberPlanRequest, ValidationRequest, RevisionRequest,
)
from wemeet_ai.examples import example_input
from wemeet_ai.tracing import MemoryTraceSink


def prepared():
    old = example_input()
    for field in old.analysis.fields.values():
        field.status = "MISSING"
        field.candidates = []
    request = GroupPrepareRequest(analysis=old.analysis, confirmed_decisions=old.confirmed_decisions)
    result = GoalAI(client()).prepare_group_goal_spec(request)
    assert result.status == "OK", result.model_dump_json()
    return result.data, old.member_profiles, request


def test_missing_and_zero_profiles_allow_confirmed_group_spec():
    spec, _, _ = prepared()
    assert spec.members and "member_profiles" not in spec.model_dump()


def test_group_generation_and_group_validation_need_no_private_profiles():
    spec, _, _ = prepared()
    group = group_fixture(spec)
    ai = GoalAI(client(Response(group.model_dump_json())))
    generated = ai.generate_group_plan(spec)
    assert generated.status == "OK", generated.model_dump_json()
    report = ai.validate_plan(ValidationRequest(goal_spec=spec, group_plan=generated.data, require_all_members=False))
    assert report.data.valid


def test_individual_plan_uses_only_selected_confirmed_profile():
    spec, profiles, _ = prepared()
    profiles[1].constraints = ["다른 사람의 민감한 개인 조건"]
    group = group_fixture(spec)
    draft = member_fixture(spec, group, profiles[0].member_id)
    c = client(Response(draft.model_dump_json()))
    result = GoalAI(c).generate_member_plan(MemberPlanRequest(goal_spec=spec, group_plan=group, member_profile=profiles[0]))
    assert result.status == "OK", result.model_dump_json()
    sent = json.dumps(c.session.calls[0][1]["json"], ensure_ascii=False)
    assert profiles[1].constraints[0] not in sent


def test_unknown_person_cannot_generate_individual_plan():
    spec, profiles, _ = prepared()
    profile = profiles[0].model_copy(update={"member_id": "outsider"})
    c = client()
    result = GoalAI(c).generate_member_plan(MemberPlanRequest(goal_spec=spec, group_plan=group_fixture(spec), member_profile=profile))
    assert result.status == "FAILED" and not c.session.calls


def test_group_only_handoff_is_diagnostic_then_all_members_becomes_ready():
    spec, profiles, _ = prepared()
    group = group_fixture(spec)
    ai = GoalAI(client())
    diagnostic = ai.build_handoff(HandoffRequest(goal_spec=spec, group_plan=group))
    assert not diagnostic.data.group_file["integration_ready"]
    assert {i["code"] for i in diagnostic.data.group_file["validation"]["issues"]} >= {"PROFILE_COVERAGE", "MEMBER_COVERAGE"}
    complete = ai.build_handoff(HandoffRequest(goal_spec=spec, group_plan=group, member_profiles=profiles,
        member_plans=[member_fixture(spec, group, p.member_id) for p in profiles]))
    assert complete.data.group_file["integration_ready"]
    assert "member_profiles" not in json.dumps(complete.data.group_file)


def test_pending_required_suggestions_block_but_optional_missing_does_not():
    _, _, request = prepared()
    request.reviewed_draft = GoalSpecDraft(summary="검토", fields={
        "metric": DraftField(), "checkins": DraftField(value=[]), "roles": DraftField(value={}),
        "privacy_scope": DraftField(value="OBJECTIVE_ONLY", origin="AI_SUGGESTED"),
    })
    ai = GoalAI(client())
    assert ai.prepare_group_goal_spec(request).status == "NEEDS_INPUT"
    request.reviewed_draft.fields["privacy_scope"].value = request.confirmed_decisions.privacy_scope
    request.reviewed_draft.fields["privacy_scope"].review_status = "ACCEPTED"
    assert ai.prepare_group_goal_spec(request).status == "OK"


def test_targeted_supplement_preserves_other_and_reviewed_cards_and_traces():
    previous = draft_fixture()
    previous.fields["goal"].review_status = "EDITED"
    previous.fields["start_date"] = DraftField()
    previous.fields["verification_method"].value = "이전 인증 후보 유지"
    response = draft_fixture()
    response.fields["start_date"] = DraftField(value="2026-11-01", origin="AI_SUGGESTED", reason="기준일 이후 시작을 제안")
    c = client(Response(response.model_dump_json()))
    ai = GoalAI(c, trace_sink=MemoryTraceSink())
    result = ai.draft_goal_spec(DraftRequest(analysis=example_input().analysis, previous_draft=previous,
        reference_date="2026-10-28", target_fields=["start_date"]))
    assert result.status == "OK", result.model_dump_json()
    assert result.data.fields["start_date"].value == "2026-11-01"
    assert result.data.fields["start_date"].review_status == "PENDING"
    assert result.data.fields["goal"] == previous.fields["goal"]
    assert result.data.fields["verification_method"] == previous.fields["verification_method"]
    assert "target_fields" in json.dumps(result.traces)
    assert len(c.session.calls) == 1  # No re-analysis call


def test_group_api_and_legacy_api_coexist():
    from wemeet_ai.adapters.web import app
    _, _, request = prepared()
    with TestClient(app) as web:
        response = web.post("/ai/v1/group-goal-spec", json=request.model_dump(mode="json"))
        assert response.status_code == 200 and response.json()["status"] == "OK"
        paths = web.get("/openapi.json").json()["paths"]
        assert "/ai/v1/goal-spec" in paths and "/ai/v1/group-goal-spec" in paths


def test_cli_prepares_group_without_private_profiles(tmp_path):
    from wemeet_ai.cli import main
    _, _, request = prepared()
    source, output = tmp_path / "input.json", tmp_path / "spec.json"
    source.write_text(request.model_dump_json(), encoding="utf-8")
    assert main(["prepare-group", "--input", str(source), "--output", str(output)]) == 0
    spec = json.loads(output.read_text(encoding="utf-8"))["data"]
    assert "members" in spec and "member_profiles" not in spec


def test_undecided_goal_remains_actionable_input_not_generic_confirmed_goal():
    request = example_input()
    request.confirmed_decisions.goal = None
    request.confirmed_decisions.expected_outcome = None
    group_request = GroupPrepareRequest(analysis=request.analysis, confirmed_decisions=request.confirmed_decisions)
    c = client()
    result = GoalAI(c).prepare_group_goal_spec(group_request)
    assert result.status == "NEEDS_INPUT"
    assert any("goal:" in q for q in result.questions)
    assert not c.session.calls


def test_group_revision_without_personal_profile_is_supported():
    spec, _, _ = prepared()
    group = group_fixture(spec)
    result = GoalAI(client(Response(group.model_dump_json()))).revise_plan(
        RevisionRequest(confirmed_spec=spec, group_plan=group, feedback="시연 확인 단계를 명확히 해주세요."))
    assert result.status == "OK", result.model_dump_json()
    assert result.data.revision == 2 and result.data.previous_plan_id == group.plan_id
