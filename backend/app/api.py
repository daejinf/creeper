from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .coaching import CoachingService
from .conversation_evaluation import ConversationEvaluationService, DecisionsNotReady
from .database import SessionLocal, get_db
from .evaluation import run_evaluation
from .models import AIRun, IdempotencyRecord, PlanRecord
from .planner import PlanningService
from .providers import ProviderError, build_provider
from .schemas import (
    AIRunResponse,
    ApprovalRequest,
    EvaluationRequest,
    EvaluationResponse,
    CreateKickoffSessionRequest,
    KickoffSessionResponse,
    MessageResponse,
    ManualOptionRequest,
    EvaluationSnapshotResponse,
    PrivateIntakeRequest,
    SendMessageRequest,
    GoalPlanningRequest,
    PlanProposal,
    PlanResponse,
    PlanStatus,
    RevisionRequest,
    SessionPlanRequest,
    VoteRequest,
)


router = APIRouter(prefix="/api/v1")


def enrich_evaluation_background(session_id: str, snapshot_id: str, settings: Settings) -> None:
    db = SessionLocal()
    try:
        provider = build_provider(settings)
        ConversationEvaluationService(db, provider, settings).enrich_hybrid_evaluation(session_id, snapshot_id)
    except Exception:
        db.rollback()
    finally:
        db.close()


def provider_or_503(settings: Settings):
    try:
        return build_provider(settings)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc


def plan_response(record: PlanRecord) -> PlanResponse:
    return PlanResponse(
        plan_id=record.id,
        version=record.version,
        approved=record.approved,
        proposal=PlanProposal.model_validate(record.proposal_payload),
    )


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    try:
        provider = build_provider(settings)
        return {"status": "ok", "service": settings.app_name, "provider": provider.name, "model": provider.model, "provider_mode": "TEST MOCK" if provider.name == "mock" else "LIVE · DeepSeek"}
    except ProviderError as exc:
        return {"status": "degraded", "service": settings.app_name, "provider": "unavailable", "model": settings.deepseek_model, "provider_mode": "연결 오류", "error_code": exc.code}


@router.post("/plans/generate", response_model=PlanResponse, status_code=201)
def generate_plan(
    request: GoalPlanningRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlanResponse:
    return PlanningService(db, provider_or_503(settings), settings).generate(request)


@router.post("/plans/{plan_id}/revise", response_model=PlanResponse, status_code=201)
def revise_plan(
    plan_id: str,
    revision: RevisionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlanResponse:
    existing = db.get(PlanRecord, plan_id)
    if not existing:
        raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND")
    if existing.approved:
        raise HTTPException(status_code=409, detail="APPROVED_PLAN_CANNOT_BE_REVISED")
    payload = dict(existing.request_payload)
    payload["previous_plan_id"] = existing.id
    payload["rejection_feedback"] = [item.model_dump(mode="json") for item in revision.rejection_feedback]
    request = GoalPlanningRequest.model_validate(payload)
    return PlanningService(db, provider_or_503(settings), settings).generate(request)


@router.post("/plans/{plan_id}/approve", response_model=PlanResponse)
def approve_plan(
    plan_id: str,
    approval: ApprovalRequest,
    x_idempotency_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PlanResponse:
    key = x_idempotency_key or approval.idempotency_key
    operation = f"approve_plan:{plan_id}"
    cached = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    if cached:
        return PlanResponse.model_validate(cached.response_payload)

    record = db.get(PlanRecord, plan_id)
    if not record:
        raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND")
    if record.status != PlanStatus.draft.value:
        raise HTTPException(status_code=409, detail="ONLY_DRAFT_CAN_BE_APPROVED")
    record.approved = True
    record.approved_at = datetime.now(timezone.utc)
    response = plan_response(record)
    response.approved = True
    db.add(
        IdempotencyRecord(
            operation=operation,
            idempotency_key=key,
            response_payload=response.model_dump(mode="json"),
        )
    )
    db.commit()
    db.refresh(record)
    return plan_response(record)


@router.get("/plans/{plan_id}/versions", response_model=list[PlanResponse])
def plan_versions(plan_id: str, db: Session = Depends(get_db)) -> list[PlanResponse]:
    record = db.get(PlanRecord, plan_id)
    if not record:
        raise HTTPException(status_code=404, detail="PLAN_NOT_FOUND")
    records = db.scalars(
        select(PlanRecord).where(PlanRecord.group_id == record.group_id).order_by(PlanRecord.version)
    ).all()
    return [plan_response(item) for item in records]


@router.get("/ai-runs", response_model=list[AIRunResponse])
def list_ai_runs(limit: int = 30, db: Session = Depends(get_db)) -> list[AIRun]:
    return list(db.scalars(select(AIRun).order_by(AIRun.created_at.desc()).limit(min(limit, 100))).all())


@router.get("/ai-runs/{run_id}", response_model=AIRunResponse)
def get_ai_run(run_id: str, db: Session = Depends(get_db)) -> AIRun:
    run = db.get(AIRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="AI_RUN_NOT_FOUND")
    return run


@router.post("/evaluations/run", response_model=EvaluationResponse, status_code=201)
def evaluate(
    request: EvaluationRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationResponse:
    if request.use_live_provider and not settings.deepseek_api_key:
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY_REQUIRED")
    return run_evaluation(db, settings, request.limit, request.use_live_provider)


@router.post("/kickoff/sessions", response_model=KickoffSessionResponse, status_code=201)
def create_kickoff_session(
    request: CreateKickoffSessionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> KickoffSessionResponse:
    try:
        return CoachingService(db, None, settings).create_session(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/kickoff/sessions/{session_id}/messages", response_model=MessageResponse)
def send_kickoff_message(
    session_id: str,
    request: SendMessageRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    try:
        return CoachingService(db, None, settings).send_message(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from exc


@router.put("/kickoff/sessions/{session_id}/members/{member_id}/intake", response_model=MessageResponse)
def save_private_intake(
    session_id: str,
    member_id: str,
    request: PrivateIntakeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MessageResponse:
    try:
        return CoachingService(db, None, settings).save_intake(session_id, member_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kickoff/sessions/{session_id}", response_model=KickoffSessionResponse)
def get_kickoff_session(
    session_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> KickoffSessionResponse:
    try:
        return CoachingService(db, None, settings).get_session(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kickoff/sessions/{session_id}/evaluations", response_model=EvaluationSnapshotResponse, status_code=201)
def evaluate_kickoff_conversation(
    session_id: str,
    background_tasks: BackgroundTasks,
    viewer_member_id: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationSnapshotResponse:
    try:
        provider_or_503(settings)
        service = ConversationEvaluationService(db, None, settings)
        response = service.start_hybrid_evaluation(session_id, viewer_member_id)
        background_tasks.add_task(enrich_evaluation_background, session_id, response.evaluation_id, settings)
        return response
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/kickoff/sessions/{session_id}/evaluations/latest", response_model=EvaluationSnapshotResponse)
def latest_kickoff_evaluation(
    session_id: str,
    viewer_member_id: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationSnapshotResponse:
    try:
        return ConversationEvaluationService(db, None, settings).get_latest(session_id, viewer_member_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kickoff/sessions/{session_id}/topics/{topic_id}/vote", response_model=EvaluationSnapshotResponse)
def vote_on_topic(
    session_id: str,
    topic_id: str,
    request: VoteRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationSnapshotResponse:
    try:
        return ConversationEvaluationService(db, None, settings).vote(session_id, topic_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post("/kickoff/sessions/{session_id}/topics/{topic_id}/reopen", response_model=EvaluationSnapshotResponse)
def reopen_topic(
    session_id: str,
    topic_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationSnapshotResponse:
    try:
        return ConversationEvaluationService(db, None, settings).reopen(session_id, topic_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post("/kickoff/sessions/{session_id}/topics/{topic_id}/options", response_model=EvaluationSnapshotResponse, status_code=201)
def add_topic_option(
    session_id: str,
    topic_id: str,
    request: ManualOptionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvaluationSnapshotResponse:
    try:
        return ConversationEvaluationService(db, None, settings).add_manual_option(session_id, topic_id, request.member_id, request.label, request.value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc


@router.post("/kickoff/sessions/{session_id}/plans", response_model=PlanResponse, status_code=201)
def generate_session_plan(
    session_id: str,
    request: SessionPlanRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlanResponse:
    try:
        return ConversationEvaluationService(db, provider_or_503(settings), settings).generate_plan(session_id, request.idempotency_key)
    except DecisionsNotReady as exc:
        raise HTTPException(status_code=409, detail={"code": "DECISIONS_NOT_READY", "blockers": exc.blockers}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from exc

