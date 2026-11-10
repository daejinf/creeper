from datetime import date, datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(Contract):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class Error(Contract):
    code: str
    message: str
    retryable: bool = False


class Meta(Contract):
    run_id: str
    task: str
    prompt_version: str = "CODE"
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    transport_attempts: int = 0
    repair_attempts: int = 0
    output_expansions: int = 0
    provider_request_ids: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    execution_source: Literal["LIVE", "CODE", "REUSED"] = "CODE"


class Result(Contract):
    status: Literal["OK", "NEEDS_INPUT", "FAILED"] = "OK"
    data: Any = None
    needed_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: Error | None = None
    meta: Meta
    persistence: dict = Field(default_factory=dict)


class Member(Contract):
    member_id: str = Field(min_length=1)
    nickname: str = Field(min_length=1, max_length=60)
    external_user_id: str | None = Field(default=None, min_length=1)


class ActorContext(Contract):
    """Only construct from an identity verified by the SW server."""

    external_user_id: str = Field(min_length=1)


class Message(Contract):
    message_id: str
    speaker_id: str | None = None
    role: Literal["user", "assistant"] = "user"
    text: str = Field(min_length=1, max_length=12000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Task(Contract):
    task_id: str = Field(
        default="",
        description="이 응답 안의 고유한 임시 작업 ID(예: t1). 서버가 정식 ID로 변환합니다.",
    )
    owner_member_id: str | None = None
    title: str = Field(min_length=1)
    deliverable: str = Field(min_length=1)
    due_date: date = Field(description="결과물을 완료해야 하는 기한")
    scheduled_date: date | None = Field(
        default=None,
        description="실제 수행 예정일. 미지정인 기존 계획은 due_date를 수행일로 해석합니다. 수행일은 기한을 넘을 수 없습니다.",
    )
    estimated_minutes: int = Field(gt=0)
    completion_criteria: list[str] = Field(min_length=1)
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "MEDIUM"
    dependencies: list[str] = Field(
        default_factory=list,
        description="이 응답 tasks에 실제 존재하는 task_id만 참조합니다. 공동 원본 작업 ID나 제목을 넣지 않습니다.",
    )


class Milestone(Contract):
    title: str
    due_date: date


class Goal(Contract):
    title: str = Field(min_length=1)
    start_date: date
    end_date: date
    completion_criteria: list[str] = Field(min_length=1)
    execution_method: str = Field(min_length=1)
    verification_method: str = Field(min_length=1)
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE"] = "OBJECTIVE_ONLY"
    roles: dict[str, str]

    @model_validator(mode="after")
    def dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("마감일은 시작일 이후여야 합니다.")
        return self


class GroupProposal(Contract):
    goal: Goal
    milestones: list[Milestone] = Field(default_factory=list)
    tasks: list[Task] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    change_summary: list[str] = Field(default_factory=list)


class GroupRequest(Contract):
    action: Literal["COACH", "PROPOSE", "REVISE"] = "COACH"
    conversation: list[Message] = Field(default_factory=list)
    members: list[Member] = Field(min_length=1)
    reference_date: date = Field(default_factory=date.today)
    timezone: str = "Asia/Seoul"
    previous: GroupProposal | None = None
    approval_status: Literal[
        "UNCONFIRMED", "VOTING", "APPROVED", "REJECTED", "SUPERSEDED"
    ] = "UNCONFIRMED"
    objections: list[str] = Field(default_factory=list)
    revision_fields: (
        list[
            Literal[
                "title",
                "start_date",
                "end_date",
                "completion_criteria",
                "execution_method",
                "verification_method",
                "privacy_scope",
                "roles",
            ]
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def sources(self):
        ids = [m.member_id for m in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("구성원 중복")
        if any(m.role == "user" and m.speaker_id not in ids for m in self.conversation):
            raise ValueError("알 수 없는 발언자")
        mids = [m.message_id for m in self.conversation]
        if len(mids) != len(set(mids)):
            raise ValueError("메시지 중복")
        return self


class CoachingSummary(Contract):
    established: list[str] = Field(default_factory=list)
    decisions_needed: list[str] = Field(default_factory=list)
    refinements: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    def as_text(self):
        labels = [
            ("정해진 내용", self.established),
            ("결정할 내용", self.decisions_needed),
            ("더 구체화하면 좋은 내용", self.refinements),
            ("다음 행동", self.next_actions),
        ]
        return "\n\n".join(
            "## "
            + title
            + "\n"
            + "\n".join("- " + v for v in (values or ["현재 추가 항목 없음"]))
            for title, values in labels
        )


class CoachingTurn(Contract):
    summary: CoachingSummary
    details: str = ""


class GroupReply(Contract):
    reply: str
    proposal: GroupProposal | None = None
    questions: list[str] = Field(default_factory=list)
    summary: CoachingSummary | None = None
    details: str = ""


class PersonalField(Contract):
    value: Any = None
    status: Literal["KNOWN", "UNKNOWN", "SKIPPED", "SUGGESTED"] = "UNKNOWN"
    evidence_refs: list[str] = Field(default_factory=list)


class Option(Contract):
    option_id: str
    label: str
    value: Any = None
    action: Literal["VALUE", "UNKNOWN", "SKIP"] = "VALUE"


class Question(Contract):
    question_id: str = ""
    field: Literal[
        "level",
        "weekly_minutes",
        "scope",
        "progress",
        "tools",
        "preferred_times",
        "available_weekdays",
        "constraints",
        "support",
    ]
    text: str
    reason: str
    options: list[Option] = Field(default_factory=list, max_length=6)


class PersonalState(Contract):
    fields: dict[str, PersonalField] = Field(default_factory=dict)
    question: Question | None = None
    answered_questions: list[str] = Field(default_factory=list)
    answers_since_summary: int = 0
    processed_message_ids: list[str] = Field(default_factory=list)


class PersonalPlan(Contract):
    plan_version_id: str = ""
    contribution: str
    deliverable: str
    completion_criteria: list[str] = Field(min_length=1)
    tasks: list[Task] = Field(min_length=1)
    adaptation_rules: list[str] = Field(default_factory=list)
    weekly_outcomes: list[str] = Field(default_factory=list)
    private_notes: list[str] = Field(default_factory=list)


class PersonalRequest(Contract):
    action: Literal["TALK", "PLAN"] = "TALK"
    goal: Goal
    owner_member_id: str
    assigned_tasks: list[Task] = Field(default_factory=list)
    public_dependencies: list[Task] = Field(
        default_factory=list,
        description="본인 담당 작업에 필요한 공개 선행 작업. 다른 사람의 개인 조건이 아닙니다.",
    )
    conversation: list[Message] = Field(default_factory=list)
    state: PersonalState = Field(default_factory=PersonalState)
    answer: dict | None = None
    reference_date: date = Field(default_factory=date.today)

    @model_validator(mode="after")
    def owner(self):
        if self.owner_member_id not in self.goal.roles:
            raise ValueError("본인 역할이 없습니다.")
        if any(
            m.role == "user" and m.speaker_id != self.owner_member_id
            for m in self.conversation
        ):
            raise ValueError("다른 사람의 개인 대화")
        if any(t.owner_member_id != self.owner_member_id for t in self.assigned_tasks):
            raise ValueError("본인에게 배정된 공동 작업만 전달하세요.")
        if any(
            t.owner_member_id not in self.goal.roles for t in self.public_dependencies
        ):
            raise ValueError("공개 선행 작업의 담당자는 공동 구성원이어야 합니다.")
        if len({m.message_id for m in self.conversation}) != len(self.conversation):
            raise ValueError("개인 메시지 ID 중복")
        return self


class PersonalReply(Contract):
    time_allocation: dict | None = None
    reply: str
    state: PersonalState
    plan: PersonalPlan | None = None
    missing: list[str] = Field(default_factory=list)
    summary: CoachingSummary | None = None
    details: str = ""
