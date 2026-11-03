from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanStatus(str, Enum):
    needs_clarification = "NEEDS_CLARIFICATION"
    draft = "DRAFT"
    failed = "FAILED"


class KickoffMessage(BaseModel):
    message_id: str | None = None
    member_id: str
    text: str = Field(min_length=1, max_length=4000)
    created_at: datetime


class MemberProfile(BaseModel):
    member_id: str
    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    avatar_color: str = "violet"
    skill_level: Literal["BEGINNER", "INTERMEDIATE", "ADVANCED"] = "BEGINNER"
    weekly_available_minutes: int = Field(default=180, ge=30, le=10080)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    preferred_times: list[str] = Field(default_factory=list, max_length=20)
    provisional: bool = False


class KnownConstraints(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = "Asia/Seoul"
    verification_method: str | None = None
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"] | None = None
    checkin_days: list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]] = Field(default_factory=list)
    goal_title: str | None = None
    metric: str | None = None
    target_value: float | None = Field(default=None, gt=0)
    unit: str | None = None
    frequency_per_week: int | None = Field(default=None, ge=1, le=7)
    checkin_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class RejectionFeedback(BaseModel):
    member_id: str
    reason: str = Field(min_length=1, max_length=1000)


class GoalPlanningRequest(BaseModel):
    group_id: str = Field(min_length=1, max_length=80)
    kickoff_messages: list[KickoffMessage] = Field(min_length=1, max_length=200)
    members: list[MemberProfile] = Field(min_length=1, max_length=30)
    known_constraints: KnownConstraints = Field(default_factory=KnownConstraints)
    previous_plan_id: str | None = None
    rejection_feedback: list[RejectionFeedback] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_member_references(self):
        member_ids = {member.member_id for member in self.members}
        unknown = {message.member_id for message in self.kickoff_messages} - member_ids
        unknown |= {feedback.member_id for feedback in self.rejection_feedback} - member_ids
        if unknown:
            raise ValueError(f"unknown member_id references: {sorted(unknown)}")
        return self


class SharedGoal(BaseModel):
    title: str
    description: str
    metric: str
    target_value: float = Field(gt=0)
    unit: str
    start_date: date
    end_date: date


class Milestone(BaseModel):
    title: str
    due_date: date
    target_value: float = Field(gt=0)
    completion_criteria: str
    evidence_refs: list[str] = Field(default_factory=list)


class TodoItem(BaseModel):
    todo_id: str
    title: str
    description: str
    difficulty: Literal["EASY", "MEDIUM", "HARD"]
    frequency: str
    estimated_minutes: int = Field(ge=5, le=1440)
    weight: float = Field(default=1, gt=0, le=10)
    completion_criteria: str
    evidence_refs: list[str] = Field(default_factory=list)


class MemberTodos(BaseModel):
    member_id: str
    rationale: str
    assignment_status: Literal["CONFIRMED", "PROVISIONAL"] = "CONFIRMED"
    tasks: list[TodoItem] = Field(min_length=1)


class CheckinSchedule(BaseModel):
    day: Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str
    purpose: str


class VerificationPolicy(BaseModel):
    method: str
    pass_criteria: list[str]
    on_insufficient_evidence: Literal["REQUEST_RETRY", "HUMAN_REVIEW"] = "REQUEST_RETRY"


class PrivacyPolicy(BaseModel):
    group_visible_fields: list[str] = Field(default_factory=lambda: ["completion_status", "achievement_rate", "consecutive_misses"])
    private_fields: list[str] = Field(default_factory=lambda: ["failure_reason", "ai_analysis"])
    requires_member_consent_for_expansion: bool = True


class WeeklyPlan(BaseModel):
    week: int = Field(ge=1)
    group_focus: str
    target_value: float = Field(gt=0)
    member_targets: dict[str, str]


class FirstWeekTodo(BaseModel):
    member_id: str
    scheduled_date: date
    title: str
    amount: str
    estimated_minutes: int = Field(ge=5, le=1440)
    difficulty: Literal["EASY", "MEDIUM", "HARD"]
    completion_criteria: str
    assignment_status: Literal["CONFIRMED", "PROVISIONAL"] = "CONFIRMED"
    preferred_time: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class AdaptationRule(BaseModel):
    condition: str
    adjustment: str


class PlanProposal(BaseModel):
    status: PlanStatus
    shared_goal: SharedGoal | None = None
    milestones: list[Milestone] = Field(default_factory=list)
    member_todos: list[MemberTodos] = Field(default_factory=list)
    checkin_schedule: list[CheckinSchedule] = Field(default_factory=list)
    verification_policy: VerificationPolicy | None = None
    privacy_policy: PrivacyPolicy = Field(default_factory=PrivacyPolicy)
    assumptions: list[str] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    weekly_plans: list[WeeklyPlan] = Field(default_factory=list)
    first_week_todos: list[FirstWeekTodo] = Field(default_factory=list)
    adaptation_rules: list[AdaptationRule] = Field(default_factory=list)
    prompt_version: str = "m1-plan-v1"
    model: str
    run_id: str


class PlanResponse(BaseModel):
    plan_id: str
    version: int
    approved: bool
    proposal: PlanProposal


class ApprovalRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class RevisionRequest(BaseModel):
    rejection_feedback: list[RejectionFeedback] = Field(min_length=1)


class AIRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    provider: str
    model: str
    prompt_version: str
    input_hash: str
    status: str
    raw_response: str | None
    validation_errors: list
    error_code: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    created_at: datetime


class EvaluationRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    use_live_provider: bool = False


class EvaluationResponse(BaseModel):
    evaluation_id: str
    dataset_version: str
    provider: str
    case_count: int
    metrics: dict[str, float | int]


class DimensionStatus(str, Enum):
    missing = "MISSING"
    partial = "PARTIAL"
    confirmed = "CONFIRMED"
    conflicted = "CONFLICTED"


class CoachAction(str, Enum):
    ask_clarification = "ASK_CLARIFICATION"
    summarize = "SUMMARIZE"
    offer_options = "OFFER_OPTIONS"
    request_private_intake = "REQUEST_PRIVATE_INTAKE"
    wait = "WAIT"
    plan_ready = "PLAN_READY"


class CoachMessageType(str, Enum):
    question = "QUESTION"
    consensus_summary = "CONSENSUS_SUMMARY"
    conflict_options = "CONFLICT_OPTIONS"
    private_intake_request = "PRIVATE_INTAKE_REQUEST"
    plan_draft = "PLAN_DRAFT"
    status = "STATUS"


class SessionMemberInput(BaseModel):
    member_id: str
    nickname: str = Field(min_length=1, max_length=40)
    avatar_color: str = "violet"


class InitialMessageInput(BaseModel):
    member_id: str
    text: str = Field(min_length=1, max_length=4000)


class CreateKickoffSessionRequest(BaseModel):
    group_id: str
    deadline_at: datetime
    timezone: str = "Asia/Seoul"
    members: list[SessionMemberInput] = Field(min_length=1, max_length=30)
    initial_messages: list[InitialMessageInput] = Field(default_factory=list)


class CoachChoiceInput(BaseModel):
    dimension: str
    option_index: int = Field(ge=0, le=1)


class SendMessageRequest(BaseModel):
    member_id: str
    text: str = Field(min_length=1, max_length=4000)
    choice: CoachChoiceInput | None = None


class PrivateIntakeRequest(BaseModel):
    skill_level: Literal["BEGINNER", "INTERMEDIATE", "ADVANCED"]
    weekly_available_minutes: int = Field(ge=30, le=10080)
    preferred_times: list[str] = Field(default_factory=list, max_length=14)
    constraints: list[str] = Field(default_factory=list, max_length=20)


class ReadinessDimension(BaseModel):
    dimension: str
    label: str
    status: DimensionStatus
    summary: str | None = None
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    choices: list[str] = Field(default_factory=list)


class CoachingStateResponse(BaseModel):
    dimensions: list[ReadinessDimension]
    confirmed_count: int
    partial_count: int
    missing_count: int
    conflicted_count: int
    readiness_percent: int
    next_action: CoachAction
    next_needed: str | None = None
    plan_ready: bool
    deadline_passed: bool


class SessionMemberResponse(BaseModel):
    nickname: str
    avatar_color: str
    intake_status: Literal["PENDING", "COMPLETED", "PROVISIONAL"]


class ConversationMessageResponse(BaseModel):
    id: str
    nickname: str | None = None
    author_type: Literal["HUMAN", "AI"]
    message_type: str
    text: str
    created_at: datetime


class KickoffSessionResponse(BaseModel):
    session_id: str
    group_id: str
    deadline_at: datetime
    timezone: str
    provider_mode: str
    members: list[SessionMemberResponse]
    messages: list[ConversationMessageResponse]
    coaching_state: CoachingStateResponse
    plan_draft: PlanResponse | None = None
    messages_since_evaluation: int = 0
    evaluation_suggested: bool = False


class MessageResponse(BaseModel):
    user_message: ConversationMessageResponse | None = None
    ai_messages: list[ConversationMessageResponse]
    coaching_state: CoachingStateResponse
    plan_draft: PlanResponse | None = None
    messages_since_evaluation: int = 0
    evaluation_suggested: bool = False


class ExtractedFact(BaseModel):
    dimension: str
    value: dict
    status: DimensionStatus = DimensionStatus.confirmed
    confidence: float = Field(default=1, ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)


class FactExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


class AIDecisionOption(BaseModel):
    label: str
    value: dict
    evidence_refs: list[str] = Field(default_factory=list)
    proposer_member_ids: list[str] = Field(default_factory=list)
    recommended: bool = False


class AIDecisionTopic(BaseModel):
    dimension: Literal["goal", "metric_target", "timeline", "frequency", "verification", "checkin", "privacy"]
    title: str
    options: list[AIDecisionOption] = Field(default_factory=list)
    recommendation_reason: str | None = None
    missing_reason: str | None = None


class AIConversationEvaluation(BaseModel):
    summary: str
    topics: list[AIDecisionTopic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VoteRequest(BaseModel):
    member_id: str
    option_id: str | None = None
    choice: Literal["AGREE", "DISAGREE", "ABSTAIN"]
    reason: str | None = Field(default=None, max_length=1000)


class VoteDetailResponse(BaseModel):
    nickname: str
    option_id: str | None = None
    choice: Literal["AGREE", "DISAGREE", "ABSTAIN"]
    reason: str | None = None


class VoteSummaryResponse(BaseModel):
    participation_count: int
    member_count: int
    threshold: int
    revealed: bool
    results: list[VoteDetailResponse] = Field(default_factory=list)
    my_votes: dict[str, str] = Field(default_factory=dict)


class DecisionOptionResponse(BaseModel):
    option_id: str
    label: str
    value: dict
    evidence_refs: list[str]
    proposers: list[str]
    recommended: bool
    source: Literal["RULE", "HUMAN", "AI"] = "AI"


class DecisionTopicResponse(BaseModel):
    topic_id: str
    dimension: str
    title: str
    status: Literal["READY_TO_VOTE", "NEEDS_DISCUSSION", "NEEDS_INFORMATION", "APPROVED", "NO_MAJORITY", "SUPERSEDED"]
    required: bool
    recommendation_reason: str | None = None
    inferred: bool = False
    revision: int
    approved_option_id: str | None = None
    options: list[DecisionOptionResponse]
    vote_summary: VoteSummaryResponse


class EvaluationReadiness(BaseModel):
    required_total: int
    approved_count: int
    vote_needed_count: int
    information_needed_count: int
    intake_completed_count: int
    member_count: int
    plan_ready: bool
    blockers: list[str] = Field(default_factory=list)


class EvaluationSnapshotResponse(BaseModel):
    evaluation_id: str
    version: int
    summary: str
    warnings: list[str]
    topics: list[DecisionTopicResponse]
    readiness: EvaluationReadiness
    messages_since_evaluation: int
    evaluation_suggested: bool
    created_at: datetime
    evaluation_status: Literal["LOCAL_READY", "AI_RUNNING", "AI_ENRICHED", "AI_FAILED", "STALE"] = "LOCAL_READY"
    evaluated_message_count: int = 0
    error_code: str | None = None
    ai_run_id: str | None = None
    ai_latency_ms: int | None = None
    ai_total_tokens: int | None = None


class ManualOptionRequest(BaseModel):
    member_id: str
    label: str = Field(min_length=1, max_length=500)
    value: dict


class SessionPlanRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)

