from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api import approve_plan, revise_plan
from app.config import get_settings
from app.evaluation import load_cases, run_evaluation
from app.planner import PlanningService
from app.providers import MockPlanningProvider
from app.schemas import ApprovalRequest, GoalPlanningRequest, PlanStatus, RevisionRequest


def sample_request():
    return {
        "group_id": "test-group",
        "members": [
            {"member_id": "a", "skill_level": "BEGINNER", "weekly_available_minutes": 120, "constraints": []},
            {"member_id": "b", "skill_level": "ADVANCED", "weekly_available_minutes": 300, "constraints": []},
        ],
        "kickoff_messages": [
            {"member_id": "a", "text": "4주간 파이썬을 주 3회 공부해요. mail@example.com", "created_at": "2026-11-01T09:00:00+09:00"},
            {"member_id": "b", "text": "문제 풀이로 인증해요.", "created_at": "2026-11-01T09:01:00+09:00"},
        ],
        "known_constraints": {
            "start_date": "2026-11-02",
            "end_date": "2026-11-29",
            "timezone": "Asia/Seoul",
            "verification_method": "코드 화면과 활동 설명",
            "privacy_scope": "OBJECTIVE_ONLY",
            "checkin_days": ["MON", "WED", "FRI"],
        },
        "rejection_feedback": [],
    }


def service(db):
    return PlanningService(db, MockPlanningProvider(), get_settings())


def test_missing_required_agreements_returns_questions_without_draft(db):
    payload = sample_request()
    payload["known_constraints"]["end_date"] = None
    payload["known_constraints"]["verification_method"] = None
    result = service(db).generate(GoalPlanningRequest.model_validate(payload))
    assert result.proposal.status == PlanStatus.needs_clarification
    assert len(result.proposal.missing_questions) == 2
    assert result.approved is False


def test_generate_assigns_every_member_and_redacts_pii(db):
    result = service(db).generate(GoalPlanningRequest.model_validate(sample_request()))
    assert result.proposal.status == PlanStatus.draft
    assert {item.member_id for item in result.proposal.member_todos} == {"a", "b"}
    from app.models import AIRun
    run = db.get(AIRun, result.proposal.run_id)
    assert "mail@example.com" not in (run.raw_response or "")


def test_approve_is_idempotent(db):
    plan = service(db).generate(GoalPlanningRequest.model_validate(sample_request()))
    payload = ApprovalRequest(idempotency_key="approval-key-123")
    first = approve_plan(plan.plan_id, payload, None, db)
    second = approve_plan(plan.plan_id, payload, None, db)
    assert first == second
    assert first.approved is True


def test_revision_creates_new_version(db):
    plan = service(db).generate(GoalPlanningRequest.model_validate(sample_request()))
    feedback = RevisionRequest.model_validate({"rejection_feedback": [{"member_id": "a", "reason": "첫 주 난이도를 낮춰주세요."}]})
    revised = revise_plan(plan.plan_id, feedback, db, get_settings())
    assert revised.version == 2
    from app.models import PlanRecord
    records = db.query(PlanRecord).filter(PlanRecord.group_id == "test-group").order_by(PlanRecord.version).all()
    assert [item.version for item in records] == [1, 2]


def test_invalid_member_reference_rejected():
    payload = deepcopy(sample_request())
    payload["kickoff_messages"][0]["member_id"] = "unknown"
    with pytest.raises(ValidationError):
        GoalPlanningRequest.model_validate(payload)


def test_evaluation_dataset_has_fifty_cases_and_runs(db):
    assert len(load_cases(50)) == 50
    result = run_evaluation(db, get_settings(), 50, False)
    assert result.case_count == 50
    assert result.metrics["schema_pass_rate"] == 1
    assert result.metrics["member_coverage_rate"] == 1
    assert result.metrics["safe_activation_rate"] == 1

