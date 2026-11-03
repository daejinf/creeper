from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(Contract):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Error(Contract):
    code: str
    message: str
    retryable: bool = False


class Meta(Contract):
    run_id: str
    task: str
    prompt_version: str
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    transport_attempts: int = 0
    repair_attempts: int = 0
    plan_repair_attempts: int = 0
    provider_request_ids: list[str] = Field(default_factory=list)
    trace_id: str = ""
    parent_run_id: str | None = None
    prompt_variant: str | None = None
    reference_case_ids: list[str] = Field(default_factory=list)
    reference_data_version: str | None = None
    execution_source: Literal['LIVE','CACHE','CODE','REPLAY'] = 'CODE'
    source_run_id: str | None = None
    cache_hit: bool = False
    cached_usage: Usage | None = None
    source_execution_source: Literal['LIVE','CACHE','CODE','REPLAY'] | None = None
    validator_version: str | None = None


T = TypeVar("T")


class Result(Contract, Generic[T]):
    status: Literal["OK", "NEEDS_INPUT", "FAILED"]
    data: T | None = None
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: Error | None = None
    meta: Meta
    traces: list[dict[str, Any]] = Field(default_factory=list)


class Member(Contract):
    member_id: str = Field(min_length=1)
    nickname: str = Field(min_length=1, max_length=40)


class Message(Contract):
    message_id: str = Field(min_length=1)
    speaker_id: str | None = None
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=12000)
    created_at: datetime


class AnalysisRequest(Contract):
    conversation: list[Message] = Field(min_length=1, max_length=200)
    members: list[Member] = Field(min_length=1, max_length=30)
    reference_date: date
    timezone: str = "Asia/Seoul"

    @model_validator(mode="after")
    def references(self):
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError("Unknown timezone") from None
        ids = [m.member_id for m in self.members]
        refs = [m.message_id for m in self.conversation]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("Duplicate member or message IDs")
        if any(m.role == "user" and m.speaker_id not in ids for m in self.conversation):
            raise ValueError("Unknown speaker_id")
        return self


class SharedContext(Contract):
    goal: str | None = None
    dates: dict[str, str] = Field(default_factory=dict)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    confirmed_facts: list[str] = Field(default_factory=list)
    proposals: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reviewed_proposals: list[dict[str, Any]] = Field(default_factory=list)


class CoachState(Contract):
    consecutive_questions: int = Field(default=0, ge=0, le=2)
    last_question_topic: str | None = None
    answered_topics: list[str] = Field(default_factory=list)


class CoachRequest(AnalysisRequest):
    coaching_mode: Literal["LEGACY", "PROACTIVE", "DIALOGUE"] = "LEGACY"
    focus_topic: str | None = None
    scope: Literal["GROUP", "PRIVATE"] = "GROUP"
    owner_member_id: str | None = None
    shared_context: SharedContext = Field(default_factory=SharedContext)
    coach_state: CoachState = Field(default_factory=CoachState)

    @model_validator(mode="after")
    def private_owner(self):
        if self.scope == "PRIVATE":
            if self.owner_member_id not in {m.member_id for m in self.members}:
                raise ValueError("PRIVATE scope requires a known owner_member_id")
            if any(m.role == "user" and m.speaker_id != self.owner_member_id for m in self.conversation):
                raise ValueError("Private conversation contains another member")
        return self


class CoachReply(Contract):
    contract_issues: list[str] = Field(default_factory=list)
    reply: str
    kind: Literal["ADVICE", "CLARIFICATION", "PLAN_PROPOSAL", "SUMMARY"] = "ADVICE"
    question: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    next_action: Literal["CONTINUE", "REVIEW_GOAL_DRAFT"] = "CONTINUE"
    updated_coach_state: CoachState = Field(default_factory=CoachState)
    observations: list[str] = Field(default_factory=list)
    missing_or_conflicting: list[str] = Field(default_factory=list)
    proposals: list["CoachProposal"] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class CoachProposal(Contract):
    field_errors: dict[str,str] = Field(default_factory=dict)
    proposal_id: str = ""
    title: str
    rationale: str
    field_updates: dict[str, Any]
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal["PENDING", "ACCEPTED", "DEFERRED", "REJECTED"] = "PENDING"


class ProactiveCoachOutput(Contract):
    reply: str
    observations: list[str]
    missing_or_conflicting: list[str] = Field(default_factory=list)
    proposals: list[CoachProposal] = Field(min_length=1, max_length=3)
    question: str | None = None
    next_actions: list[str] = Field(default_factory=list)


class ProposalReviewRequest(Contract):
    proposal: CoachProposal
    decision: Literal["ACCEPTED", "DEFERRED", "REJECTED"]
    reviewer: Member


DIMENSIONS = ("goal", "reason", "timeline", "success", "frequency", "verification", "checkin", "privacy", "roles", "milestones")


class Candidate(Contract):
    candidate_id: str
    value: dict[str, Any]
    original_text: str
    evidence_refs: list[str] = Field(min_length=1)
    proposer_ids: list[str] = Field(default_factory=list)
    inferred: bool = False
    origin: Literal["HUMAN", "ASSISTANT_PROPOSAL"] = "HUMAN"


class Support(Contract):
    member_id: str
    message_id: str
    supported_candidate: str | None = None


class AnalysisField(Contract):
    status: Literal["MISSING", "PARTIAL", "EXTRACTED", "CONFLICTED"]
    candidates: list[Candidate] = Field(default_factory=list)
    support_refs: list[Support] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class CoachingNote(Contract):
    assistant_message_id: str
    suggestion_text: str
    user_response_refs: list[str] = Field(default_factory=list)
    disposition: Literal['UNDECIDED','SUPPORTED','REJECTED','AMBIGUOUS'] = 'UNDECIDED'
    values: dict[str,Any] = Field(default_factory=dict)


class ConversationAnalysis(Contract):
    coaching_notes: list[CoachingNote] = Field(default_factory=list)
    summary: str
    fields: dict[str, AnalysisField]
    members: list[Member] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DraftField(Contract):
    value: Any = None
    origin: Literal["EXTRACTED", "AI_SUGGESTED", "UNRESOLVED"] = "UNRESOLVED"
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    review_status: Literal["PENDING", "ACCEPTED", "EDITED"] = "PENDING"
    validation_errors: list[str] = Field(default_factory=list)


class GoalSpecDraft(Contract):
    draft_id: str = ""
    summary: str
    fields: dict[str, DraftField]


class DraftValidationRequest(Contract):
    draft: GoalSpecDraft
    members: list[Member] = Field(default_factory=list)


class DraftRequest(Contract):
    draft_mode: Literal['GROUNDED','SUGGEST','ASSISTED'] = 'GROUNDED'
    analysis: ConversationAnalysis
    shared_context: SharedContext = Field(default_factory=SharedContext)
    previous_draft: GoalSpecDraft | None = None
    reference_date: date = Field(default_factory=date.today)
    timezone: str = "Asia/Seoul"
    target_fields: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def legacy_explicit_supplement(self):
        if self.target_fields and 'draft_mode' not in self.model_fields_set:self.draft_mode='SUGGEST'
        return self


class PrivateIntakeAnalysis(Contract):
    owner_member_id: str
    fields: dict[str, DraftField]
    evidence_ids: list[str] = Field(default_factory=list)


class WorkflowRequest(AnalysisRequest):
    shared_context: SharedContext = Field(default_factory=SharedContext)
    previous_draft: GoalSpecDraft | None = None


class DraftWorkflow(Contract):
    analysis_result: Result[ConversationAnalysis]
    draft_result: Result[GoalSpecDraft] | None = None


class MemberProfile(Contract):
    member_id: str
    current_level: str = Field(min_length=1)
    weekly_available_minutes: int = Field(gt=0, le=10080)
    preferred_times: list[str]
    constraints: list[str]
    planning_context: dict[str, "DraftField"] = Field(default_factory=dict)


class Metric(Contract):
    name: str
    target: float = Field(gt=0)
    unit: str
    allocation: Literal["PER_MEMBER", "GROUP_TOTAL"]


class ConfirmedDecisions(Contract):
    confirmed: bool = False
    goal: str | None = None
    expected_outcome: str | None = None
    completion_criteria: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    frequency_per_week: int | None = Field(default=None, ge=1, le=7)
    execution_method: str | None = None
    verification_method: str | None = None
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"] | None = None
    custom_privacy_fields: list[str] = Field(default_factory=list)
    metric: Metric | None = None
    checkins: list[str] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)

    @field_validator("checkins", mode="before")
    @classmethod
    def normalize_checkin_input(cls, value):
        from .draft_validation import normalize_checkins
        return normalize_checkins(value) if value is not None else []


class GoalSpec(Contract):
    spec_id: str
    goal: str
    expected_outcome: str
    completion_criteria: list[str]
    start_date: date
    end_date: date
    execution_method: str
    verification_method: str
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"]
    custom_privacy_fields: list[str] = Field(default_factory=list)
    frequency_per_week: int | None = None
    metric: Metric | None = None
    checkins: list[str] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    member_profiles: list[MemberProfile]
    evidence_ids: list[str]
    confirmed: Literal[True] = True

    @model_validator(mode="after")
    def valid_period(self):
        if self.start_date >= self.end_date:
            raise ValueError("end_date must be after start_date")
        ids = [m.member_id for m in self.member_profiles]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Member profiles must be nonempty and unique")
        return self


class PrepareRequest(Contract):
    analysis: ConversationAnalysis
    confirmed_decisions: ConfirmedDecisions
    member_profiles: list[MemberProfile] = Field(default_factory=list)
    reviewed_draft: GoalSpecDraft | None = None


class GroupPrepareRequest(Contract):
    analysis: ConversationAnalysis
    confirmed_decisions: ConfirmedDecisions
    reviewed_draft: GoalSpecDraft | None = None


class GroupGoalSpec(Contract):
    spec_id: str
    goal: str
    expected_outcome: str
    completion_criteria: list[str]
    start_date: date
    end_date: date
    execution_method: str
    verification_method: str
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"]
    custom_privacy_fields: list[str] = Field(default_factory=list)
    frequency_per_week: int | None = None
    metric: Metric | None = None
    checkins: list[str] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    members: list[Member] = Field(min_length=1)
    evidence_ids: list[str]
    confirmed: Literal[True] = True

    @model_validator(mode="after")
    def valid_period(self):
        if self.start_date >= self.end_date:
            raise ValueError("end_date must be after start_date")
        ids = [m.member_id for m in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("Members must be unique")
        return self


class Milestone(Contract):
    title: str
    due_date: date
    completion_criteria: str
    evidence_refs: list[str]


class WeeklyOutline(Contract):
    week: int = Field(ge=1)
    outcome: str
    tasks: list[str] = Field(min_length=1)


class GroupPlan(Contract):
    plan_id: str = ""
    spec_id: str = ""
    revision: int = 1
    previous_plan_id: str | None = None
    status: Literal["DRAFT"] = "DRAFT"
    goal: str
    expected_outcome: str
    completion_criteria: list[str]
    start_date: date
    end_date: date
    frequency_per_week: int | None = None
    metric: Metric | None = None
    milestones: list[Milestone] = Field(min_length=1)
    weekly_outline: list[WeeklyOutline] = Field(min_length=1)
    suggested_checkins: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_refs: list[str]


class WeeklyTarget(Contract):
    week: int = Field(ge=1)
    outcome: str
    estimated_minutes: int = Field(gt=0)
    target_amount: float | None = Field(default=None, ge=0)


class Todo(Contract):
    task_id: str = ""
    scheduled_date: date = Field(description="Must be one of first_week_allowed_dates; no second-week tasks")
    action: str
    amount_or_deliverable: str
    estimated_minutes: int = Field(gt=0)
    difficulty: Literal["EASY", "MEDIUM", "HARD"]
    completion_criteria: str
    origin: Literal["AI_SUGGESTION"] = "AI_SUGGESTION"
    evidence_refs: list[str]
    dependencies: list[str] = Field(default_factory=list)


class MemberPlan(Contract):
    plan_id: str = ""
    group_plan_id: str
    member_id: str
    revision: int = 1
    previous_plan_id: str | None = None
    weekly_targets: list[WeeklyTarget] = Field(min_length=1)
    first_week_todos: list[Todo] = Field(min_length=1)
    adaptation_rules: list[str] = Field(min_length=1)
    public_rationale: str
    evidence_refs: list[str]


class MemberPlanDraft(Contract):
    public_plan: MemberPlan
    private_notes: list[str] = Field(default_factory=list)
    contribution_goal: "ContributionGoal | None" = None


class ContributionGoal(Contract):
    owner_member_id: str
    group_plan_id: str
    title: str
    deliverable: str
    completion_criteria: list[str] = Field(min_length=1)
    public_rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    review_status: Literal["PENDING", "ACCEPTED", "EDITED"] = "PENDING"


PersonalField = Literal["current_level", "weekly_available_minutes", "preferred_times", "constraints", "contribution_candidate", "current_progress", "scope", "tools", "support_needed"]


class IntakeDimension(Contract):
    field_id: PersonalField
    label: str
    reason: str


class AnswerOption(Contract):
    option_id: str = ""
    label: str
    value: Any = None
    action: Literal["VALUE", "UNKNOWN", "SKIP"] = "VALUE"


class PersonalQuestion(Contract):
    question_id: str = ""
    target_field: PersonalField
    text: str
    multiple: bool = False
    options: list[AnswerOption] = Field(min_length=1, max_length=6)


class MemberCoachState(Contract):
    owner_member_id: str | None = None
    group_plan_id: str | None = None
    fields: dict[str, DraftField] = Field(default_factory=dict)
    answer_states: dict[str, Literal["KNOWN", "UNKNOWN", "SKIPPED"]] = Field(default_factory=dict)
    current_question: PersonalQuestion | None = None
    answered_question_ids: list[str] = Field(default_factory=list)
    consecutive_questions: int = Field(default=0, ge=0, le=2)
    diagnostic_dimensions: list[IntakeDimension] = Field(default_factory=list)
    awaiting_continue: bool = False


class ButtonAnswer(Contract):
    question_id: str
    option_ids: list[str] = Field(min_length=1)
    message_id: str


class MemberGoalRequest(Contract):
    diagnostic_mode: Literal["LEGACY", "ADAPTIVE"] = "LEGACY"
    continue_diagnosis: bool = False
    goal_spec: GoalSpec | GroupGoalSpec
    group_plan: GroupPlan
    owner_member_id: str
    conversation: list[Message] = Field(default_factory=list, max_length=200)
    reference_date: date = Field(default_factory=date.today)
    timezone: str = "Asia/Seoul"
    state: MemberCoachState = Field(default_factory=MemberCoachState)
    answer: ButtonAnswer | None = None
    previous_draft: "MemberGoalDraft | None" = None

    @model_validator(mode="after")
    def personal_scope(self):
        members = self.goal_spec.members if isinstance(self.goal_spec, GroupGoalSpec) else self.goal_spec.member_profiles
        if self.owner_member_id not in {m.member_id for m in members}:
            raise ValueError("Unknown personal owner")
        if any(m.role == "user" and m.speaker_id != self.owner_member_id for m in self.conversation):
            raise ValueError("Personal conversation contains another member")
        if len({m.message_id for m in self.conversation}) != len(self.conversation):
            raise ValueError("Duplicate personal message IDs")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid personal timezone") from exc
        return self


class MemberCoachModel(Contract):
    reply: str
    kind: Literal["ADVICE", "CLARIFICATION", "PLAN_PROPOSAL", "SUMMARY"] = "ADVICE"
    question: PersonalQuestion | None = None
    extracted_fields: dict[str, DraftField] = Field(default_factory=dict)
    diagnostic_dimensions: list[IntakeDimension] = Field(default_factory=list)


class MemberCoachReply(Contract):
    contract_issues: list[str] = Field(default_factory=list)
    reply: str
    kind: str
    state: MemberCoachState
    missing_fields: list[str] = Field(default_factory=list)


class MemberGoalDraft(Contract):
    draft_id: str = ""
    owner_member_id: str
    group_plan_id: str
    contribution: ContributionGoal
    profile_fields: dict[str, DraftField] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class MemberPlanRequest(Contract):
    goal_spec: GoalSpec | GroupGoalSpec
    group_plan: GroupPlan
    member_profile: MemberProfile
    contribution_goal: ContributionGoal | None = None


class Issue(Contract):
    code: str
    field_path: str
    member_id: str | None = None
    message: str


class ValidationReport(Contract):
    valid: bool
    issues: list[Issue] = Field(default_factory=list)


class ValidationRequest(Contract):
    goal_spec: GoalSpec | GroupGoalSpec
    group_plan: GroupPlan
    member_plans: list[MemberPlanDraft] = Field(default_factory=list)
    require_all_members: bool = True
    member_profiles: list[MemberProfile] = Field(default_factory=list)


class RevisionRequest(Contract):
    confirmed_spec: GoalSpec | GroupGoalSpec
    group_plan: GroupPlan
    member_plan: MemberPlanDraft | None = None
    feedback: str = Field(min_length=1, max_length=4000)
    member_profile: MemberProfile | None = None


class PlanBundle(Contract):
    goal_spec: GoalSpec
    group_plan: GroupPlan
    member_results: dict[str, Result[MemberPlanDraft]]
    retry_member_ids: list[str]
    validation: ValidationReport


class BaselineRef(Contract):
    baseline_id: str
    scope: Literal['GROUP','PERSONAL']
    owner_member_id: str | None = None
    plan_version_id: str
    group_plan_version_id: str
    task_ids: list[str] = Field(default_factory=list)
    confirmed_by: str
    confirmed_at: str


class HandoffRequest(Contract):
    baseline_refs: list[BaselineRef] = Field(default_factory=list)
    goal_spec: GoalSpec | GroupGoalSpec | None = None
    member_profiles: list[MemberProfile] = Field(default_factory=list)
    group_plan: GroupPlan | None = None
    member_plans: list[MemberPlanDraft] = Field(default_factory=list)
    private_intakes: dict[str, PrivateIntakeAnalysis] = Field(default_factory=dict)
    personal_drafts: dict[str, MemberGoalDraft] = Field(default_factory=dict)
    personal_conversations: dict[str, list[Message]] = Field(default_factory=dict)
    reviewed_draft: GoalSpecDraft | None = None
    run_manifest: list[Meta] = Field(default_factory=list)
    source_fingerprint: str | None = None
    current_source_fingerprint: str | None = None

    @model_validator(mode="after")
    def scoped_personal_data(self):
        for mid, draft in self.personal_drafts.items():
            if draft.owner_member_id != mid or draft.contribution.owner_member_id != mid:
                raise ValueError("Personal draft owner mismatch")
        for mid, messages in self.personal_conversations.items():
            if any(m.role == "user" and m.speaker_id != mid for m in messages):
                raise ValueError("Personal conversation owner mismatch")
        return self


class HandoffPackage(Contract):
    group_file: dict[str, Any]
    private_files: dict[str, dict[str, Any]]


MemberPlanDraft.model_rebuild()
MemberGoalRequest.model_rebuild()
CoachReply.model_rebuild()


class GoalSetupState(Contract):
    schema_version: Literal['1.0'] = '1.0'
    revision: int = 0
    conversation: list[Message] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    reference_date: date = Field(default_factory=date.today)
    timezone: str = 'Asia/Seoul'
    coach_state: CoachState = Field(default_factory=CoachState)
    analysis: ConversationAnalysis | None = None
    draft: GoalSpecDraft | None = None
    source_fingerprint: str = ''
    draft_source_fingerprint: str = ''
    last_coach_input: str = ''
    last_coach_reply: str | None = None
    review_history: list[dict[str,Any]] = Field(default_factory=list)
    group_spec: GroupGoalSpec | None = None


class GoalSetupResponse(Contract):
    operation: Literal['ACCEPT','EDIT','ANSWER','ALTERNATIVE']
    fields: list[str] = Field(default_factory=list)
    values: dict[str,Any] = Field(default_factory=dict)
    text: str | None = None


class GoalSetupRequest(Contract):
    action: Literal['COACH','REVIEW','RESPOND','CONFIRM']
    state: GoalSetupState | None = None
    conversation: list[Message] | None = None
    members: list[Member] | None = None
    reference_date: date | None = None
    timezone: str | None = None
    actor_member_id: str | None = None
    response: GoalSetupResponse | None = None


class GoalSetupOutput(Contract):
    state: GoalSetupState
    reply: str | None = None
    review_items: list[dict[str,Any]] = Field(default_factory=list)
    needed_inputs: list[dict[str,Any]] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    can_generate_group_plan: bool = False
    group_spec: GroupGoalSpec | None = None
    runs: list[Meta] = Field(default_factory=list)
