import json
from pathlib import Path

from sqlalchemy.orm import Session

from .config import Settings
from .models import AIRun, EvaluationRun
from .planner import PlanningService
from .providers import build_provider
from .schemas import EvaluationResponse, GoalPlanningRequest, PlanStatus


DATASET_VERSION = "m1-v1"
DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_cases.jsonl"


def load_cases(limit: int) -> list[dict]:
    cases: list[dict] = []
    with DATASET_PATH.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                cases.append(json.loads(line))
            if len(cases) >= limit:
                break
    return cases


def run_evaluation(db: Session, settings: Settings, limit: int, use_live_provider: bool) -> EvaluationResponse:
    cases = load_cases(limit)
    provider = build_provider(settings, force_mock=not use_live_provider)
    schema_pass = 0
    member_coverage_pass = 0
    safe_activation_pass = 0
    clarification_correct = 0
    clarification_total = 0
    latencies: list[int] = []

    for case in cases:
        request = GoalPlanningRequest.model_validate(case["request"])
        response = PlanningService(db, provider, settings).generate(request)
        proposal = response.proposal
        schema_pass += 1
        if proposal.status == PlanStatus.draft:
            expected = {member.member_id for member in request.members}
            actual = {assignment.member_id for assignment in proposal.member_todos}
            member_coverage_pass += int(expected == actual)
        else:
            member_coverage_pass += int(proposal.status == PlanStatus.needs_clarification)
        safe_activation_pass += int(not response.approved)
        expected_status = case.get("expected_status")
        if expected_status == PlanStatus.needs_clarification.value:
            clarification_total += 1
            clarification_correct += int(proposal.status == PlanStatus.needs_clarification)
        if proposal.run_id:
            run = db.get(AIRun, proposal.run_id)
            if run:
                latencies.append(run.latency_ms)

    sorted_latencies = sorted(latencies)
    p95_index = max(0, int(len(sorted_latencies) * 0.95) - 1)
    metrics = {
        "schema_pass_rate": round(schema_pass / len(cases), 4) if cases else 0,
        "member_coverage_rate": round(member_coverage_pass / len(cases), 4) if cases else 0,
        "safe_activation_rate": round(safe_activation_pass / len(cases), 4) if cases else 0,
        "clarification_accuracy": round(clarification_correct / clarification_total, 4) if clarification_total else 1,
        "p95_latency_ms": sorted_latencies[p95_index] if sorted_latencies else 0,
    }
    record = EvaluationRun(
        dataset_version=DATASET_VERSION,
        provider=provider.name,
        case_count=len(cases),
        metrics=metrics,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return EvaluationResponse(
        evaluation_id=record.id,
        dataset_version=record.dataset_version,
        provider=record.provider,
        case_count=record.case_count,
        metrics=record.metrics,
    )

