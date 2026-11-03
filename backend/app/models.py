from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AIRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    task: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanRecord(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("group_id", "version", name="uq_plan_group_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), index=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    request_payload: Mapped[dict] = mapped_column(JSON)
    proposal_payload: Mapped[dict] = mapped_column(JSON)
    previous_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ai_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("operation", "idempotency_key", name="uq_idempotency_operation_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    operation: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    response_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset_version: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    case_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KickoffSession(Base):
    __tablename__ = "kickoff_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Seoul")
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE", index=True)
    current_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latest_evaluation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    messages_since_evaluation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionMember(Base):
    __tablename__ = "session_members"
    __table_args__ = (UniqueConstraint("session_id", "member_id", name="uq_session_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(80))
    nickname: Mapped[str] = mapped_column(String(40))
    avatar_color: Mapped[str] = mapped_column(String(20), default="violet")
    intake_status: Mapped[str] = mapped_column(String(30), default="PENDING")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    author_type: Mapped[str] = mapped_column(String(20))
    visibility: Mapped[str] = mapped_column(String(20), default="GROUP")
    message_type: Mapped[str] = mapped_column(String(40), default="CHAT")
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CoachingStateRecord(Base):
    __tablename__ = "coaching_states"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dimensions: Mapped[list] = mapped_column(JSON, default=list)
    next_action: Mapped[str] = mapped_column(String(50), default="ASK_CLARIFICATION")
    plan_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    last_question_dimension: Mapped[str | None] = mapped_column(String(80), nullable=True)
    human_messages_since_ai: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CoachingFact(Base):
    __tablename__ = "coaching_facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    dimension: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(default=1.0)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String(20), default="GROUP")
    owner_member_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PrivateIntake(Base):
    __tablename__ = "private_intakes"
    __table_args__ = (UniqueConstraint("session_id", "member_id", name="uq_private_intake_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(80))
    skill_level: Mapped[str] = mapped_column(String(30))
    weekly_available_minutes: Mapped[int] = mapped_column(Integer)
    preferred_times: Mapped[list] = mapped_column(JSON, default=list)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EvaluationSnapshot(Base):
    __tablename__ = "evaluation_snapshots"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_evaluation_session_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    evaluated_through_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="m0-evaluation-v3")
    ai_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    evaluation_status: Mapped[str] = mapped_column(String(30), default="LOCAL_READY", index=True)
    evaluated_message_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionTopic(Base):
    __tablename__ = "decision_topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    snapshot_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    dimension: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), index=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    recommendation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    approved_option_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionOption(Base):
    __tablename__ = "decision_options"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    topic_id: Mapped[str] = mapped_column(String(36), index=True)
    label: Mapped[str] = mapped_column(String(500))
    value: Mapped[dict] = mapped_column(JSON)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    proposer_member_ids: Mapped[list] = mapped_column(JSON, default=list)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="AI")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionVote(Base):
    __tablename__ = "decision_votes"
    __table_args__ = (UniqueConstraint("topic_id", "member_id", "option_id", name="uq_decision_vote_target"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    topic_id: Mapped[str] = mapped_column(String(36), index=True)
    option_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    member_id: Mapped[str] = mapped_column(String(80), index=True)
    choice: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

