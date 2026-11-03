import hashlib
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AIRun, PlanRecord
from .privacy import sanitized_request
from .providers import PlanningProvider, ProviderError
from .schemas import (
    GoalPlanningRequest,
    PlanProposal,
    PlanResponse,
    PlanStatus,
    PrivacyPolicy,
)


PROMPT_VERSION = "m1-plan-v2"


def ensure_message_ids(request: GoalPlanningRequest) -> GoalPlanningRequest:
    payload = request.model_dump(mode="json")
    for index, message in enumerate(payload["kickoff_messages"], start=1):
        if not message.get("message_id"):
            message["message_id"] = f"msg-{index:03d}"
    return GoalPlanningRequest.model_validate(payload)


def clarification_questions(request: GoalPlanningRequest) -> list[str]:
    constraints = request.known_constraints
    questions: list[str] = []
    if not constraints.start_date:
        questions.append("목표를 시작할 날짜는 언제인가요?")
    if not constraints.end_date:
        questions.append("목표를 완료할 마감일은 언제인가요?")
    if not constraints.verification_method:
        questions.append("활동 완료를 어떤 방식으로 인증할까요?")
    if not constraints.privacy_scope:
        questions.append("그룹에 공개할 활동 정보의 범위를 정해주세요.")
    if constraints.start_date and constraints.end_date and constraints.start_date >= constraints.end_date:
        questions.append("마감일은 시작일보다 뒤여야 합니다. 날짜를 다시 정해주세요.")
    return questions


def build_prompt(request: GoalPlanningRequest) -> str:
    return (
        "아래 JSON의 합의 내용만 근거로 그룹 계획을 작성하세요. 대화에 없는 마감일, 벌점, 공개 범위를 "
        "추측하지 마세요. 모든 구성원에게 최소 한 개의 할 일을 배정하고 evidence_refs에는 실제 message_id만 사용하세요. "
        "JSON Schema에 맞는 JSON만 반환하세요.\nINPUT_JSON:\n"
        + json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
    )


def deterministic_errors(proposal: PlanProposal, request: GoalPlanningRequest) -> list[str]:
    errors: list[str] = []
    if proposal.status != PlanStatus.draft:
        errors.append("provider must return DRAFT after preflight validation")
        return errors
    if not proposal.shared_goal:
        errors.append("shared_goal is required for DRAFT")
        return errors
    if proposal.shared_goal.start_date >= proposal.shared_goal.end_date:
        errors.append("shared_goal end_date must be after start_date")
    if proposal.shared_goal.start_date != request.known_constraints.start_date:
        errors.append("shared_goal start_date differs from confirmed constraint")
    if proposal.shared_goal.end_date != request.known_constraints.end_date:
        errors.append("shared_goal end_date differs from confirmed constraint")
    if request.known_constraints.target_value is not None and proposal.shared_goal.target_value != request.known_constraints.target_value:
        errors.append("shared_goal target_value differs from confirmed constraint")
    expected_members = {member.member_id for member in request.members}
    actual_members = {item.member_id for item in proposal.member_todos}
    if expected_members != actual_members:
        errors.append(f"member todo coverage mismatch: expected={sorted(expected_members)}, actual={sorted(actual_members)}")
    known_refs = {message.message_id for message in request.kickoff_messages}
    used_refs = set(proposal.evidence_refs)
    for milestone in proposal.milestones:
        used_refs.update(milestone.evidence_refs)
    for assignment in proposal.member_todos:
        member = next((item for item in request.members if item.member_id == assignment.member_id), None)
        planned_minutes = 0
        for task in assignment.tasks:
            used_refs.update(task.evidence_refs)
            match = re.search(r"주\s*(\d+)\s*회", task.frequency)
            if request.known_constraints.frequency_per_week is not None:
                if not match or int(match.group(1)) != request.known_constraints.frequency_per_week:
                    errors.append(f"task frequency differs from confirmed constraint: {assignment.member_id}")
            planned_minutes += task.estimated_minutes * (int(match.group(1)) if match else 1)
        if member and planned_minutes > member.weekly_available_minutes:
            errors.append(f"weekly plan exceeds member availability: {assignment.member_id}")
    normalized_task_sets = [tuple(task.title for task in item.tasks) for item in proposal.member_todos]
    if len(normalized_task_sets) > 1 and len(set(normalized_task_sets)) == 1:
        errors.append("identical generic tasks were copied to every member")
    first_week_members = {item.member_id for item in proposal.first_week_todos}
    if proposal.first_week_todos and first_week_members != expected_members:
        errors.append("first-week todo coverage mismatch")
    for item in proposal.first_week_todos:
        used_refs.update(item.evidence_refs)
    unknown_refs = used_refs - known_refs
    if unknown_refs:
        errors.append(f"unknown evidence refs: {sorted(unknown_refs)}")
    private_defaults = {"failure_reason", "ai_analysis"}
    if private_defaults - set(proposal.privacy_policy.private_fields):
        errors.append("mandatory private fields cannot be exposed")
    for milestone in proposal.milestones:
        if milestone.due_date < proposal.shared_goal.start_date or milestone.due_date > proposal.shared_goal.end_date:
            errors.append(f"milestone outside goal period: {milestone.title}")
    return errors


class PlanningService:
    def __init__(self, db: Session, provider: PlanningProvider, settings: Settings):
        self.db = db
        self.provider = provider
        self.settings = settings

    def generate(self, request: GoalPlanningRequest, session_id: str | None = None) -> PlanResponse:
        request = sanitized_request(ensure_message_ids(request))
        questions = clarification_questions(request)
        if questions:
            run_id = str(uuid4())
            proposal = PlanProposal(
                status=PlanStatus.needs_clarification,
                model=self.provider.model,
                run_id=run_id,
                missing_questions=questions,
                privacy_policy=PrivacyPolicy(),
                warnings=["필수 합의가 완료되기 전에는 계획을 생성하지 않습니다."],
            )
            self._save_ai_run(run_id, request, "NEEDS_CLARIFICATION", None, [])
            return self._save_plan(request, proposal, session_id)

        prompt = build_prompt(request)
        validation_errors: list[str] = []
        last_error_code: str | None = None
        started = time.perf_counter()
        raw_response: str | None = None
        result = None
        proposal = None
        for _attempt in range(3):
            try:
                result = self.provider.generate(request, prompt)
                raw_response = result.raw_response
                payload = dict(result.payload)
                payload["prompt_version"] = PROMPT_VERSION
                payload["model"] = self.provider.model
                payload["run_id"] = str(uuid4())
                proposal = PlanProposal.model_validate(payload)
                validation_errors = deterministic_errors(proposal, request)
                if not validation_errors:
                    break
                last_error_code = "DETERMINISTIC_VALIDATION_FAILED"
            except ValidationError as exc:
                validation_errors = [error["msg"] for error in exc.errors()]
                last_error_code = "SCHEMA_VALIDATION_FAILED"
            except ProviderError as exc:
                validation_errors = [str(exc)]
                last_error_code = exc.code
        latency_ms = int((time.perf_counter() - started) * 1000)

        if proposal is None or validation_errors:
            run_id = str(uuid4())
            failed = PlanProposal(
                status=PlanStatus.failed,
                model=self.provider.model,
                run_id=run_id,
                warnings=["AI 응답을 안전하게 검증하지 못해 계획을 활성화하지 않았습니다."],
            )
            self._save_ai_run(run_id, request, "FAILED", raw_response, validation_errors, last_error_code, latency_ms, result)
            return self._save_plan(request, failed, session_id)

        self._save_ai_run(proposal.run_id, request, "SUCCEEDED", raw_response, [], None, latency_ms, result)
        return self._save_plan(request, proposal, session_id)

    def _save_ai_run(
        self,
        run_id: str,
        request: GoalPlanningRequest,
        status: str,
        raw_response: str | None,
        validation_errors: list[str],
        error_code: str | None = None,
        latency_ms: int = 0,
        result=None,
    ) -> None:
        serialized = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        run = AIRun(
            id=run_id,
            task="goal_plan_generation",
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=PROMPT_VERSION,
            input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            status=status,
            raw_response=raw_response,
            validation_errors=validation_errors,
            error_code=error_code,
            latency_ms=latency_ms,
            prompt_tokens=getattr(result, "prompt_tokens", 0),
            completion_tokens=getattr(result, "completion_tokens", 0),
            total_tokens=getattr(result, "total_tokens", 0),
        )
        self.db.add(run)
        self.db.flush()

    def _save_plan(self, request: GoalPlanningRequest, proposal: PlanProposal, session_id: str | None = None) -> PlanResponse:
        current_version = self.db.scalar(
            select(func.max(PlanRecord.version)).where(PlanRecord.group_id == request.group_id)
        ) or 0
        plan = PlanRecord(
            group_id=request.group_id,
            version=current_version + 1,
            status=proposal.status.value,
            approved=False,
            request_payload=request.model_dump(mode="json"),
            proposal_payload=proposal.model_dump(mode="json"),
            previous_plan_id=request.previous_plan_id,
            ai_run_id=proposal.run_id,
            session_id=session_id,
        )
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        return PlanResponse(plan_id=plan.id, version=plan.version, approved=plan.approved, proposal=proposal)

