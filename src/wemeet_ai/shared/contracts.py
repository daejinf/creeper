"""Small explicit public contracts. External SW inputs are first-class sources."""
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

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
    prompt_version: str = "0.13.1"
    source_revision: str | None = None
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    transport_attempts: int = 0
    repair_attempts: int = 0
    provider_request_ids: list[str] = Field(default_factory=list)
    execution_source: Literal["LIVE", "CACHE", "CODE", "REPLAY"] = "CODE"
    source_run_id: str | None = None
    cached_usage: Usage | None = None
    cache_hit: bool = False

T = TypeVar("T")
class PersistenceInfo(Contract):
    operation_id: str
    record_revision: int | None = None
    persistence_status: Literal['PERSISTED','STALE','PERSIST_FAILED','RUNNING','UNKNOWN']
    entity_ids: dict[str,str] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    retry_action: Literal['NONE','POLL','SAVE_ONLY','REVIEW'] = 'NONE'

class Result(Contract, Generic[T]):
    status: Literal["OK", "NEEDS_INPUT", "FAILED"]
    data: T | None = None
    needed_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: Error | None = None
    meta: Meta
    persistence: PersistenceInfo | None = None

class Member(Contract):
    member_id: str = Field(min_length=1)
    nickname: str = Field(min_length=1)

class Message(Contract):
    message_id: str = Field(min_length=1)
    speaker_id: str | None = None
    role: Literal["user", "assistant"] = "user"
    text: str = Field(min_length=1, max_length=12000)
    created_at: datetime

class ConversationInput(Contract):
    conversation: list[Message] = Field(default_factory=list, max_length=200)
    members: list[Member] = Field(default_factory=list)
    reference_date: date = Field(default_factory=date.today)
    timezone: str = "Asia/Seoul"

    @model_validator(mode="after")
    def check_sources(self):
        ZoneInfo(self.timezone)
        ids = [m.message_id for m in self.conversation]
        members = [m.member_id for m in self.members]
        if len(set(ids)) != len(ids) or len(set(members)) != len(members):
            raise ValueError("Duplicate message/member ID")
        if any(m.role == "user" and m.speaker_id not in members for m in self.conversation):
            raise ValueError("Unknown user speaker")
        return self

class Evidence(Contract):
    source: Literal["USER_MESSAGE", "SW_CONFIRMED", "AI_SUGGESTED"]
    source_id: str = Field(min_length=1)

class ReviewField(Contract):
    value: Any = None
    origin: Literal["EXTRACTED", "AI_SUGGESTED", "UNRESOLVED"] = "UNRESOLVED"
    evidence: list[Evidence] = Field(default_factory=list)
    reason: str = ""
    review_status: Literal["PENDING", "ACCEPTED", "EDITED", "REVIEW_REQUIRED"] = "PENDING"
    previous_value: Any = None
    conflicted: bool = False

class GoalContext(Contract):
    goal: str = ""
    role: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    completion_criteria: list[str] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    verification_method: str | None = None
    privacy_scope: Literal["OBJECTIVE_ONLY", "PRIVATE", "CUSTOM"] | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("end_date must be after start_date")
        for milestone in self.milestones:
            if milestone.get("due_date"):
                try: due = date.fromisoformat(str(milestone["due_date"]))
                except ValueError: raise ValueError("마일스톤 날짜를 확인해주세요.") from None
                if self.start_date and due < self.start_date or self.end_date and due > self.end_date:
                    raise ValueError("마일스톤이 목표 기간 밖에 있습니다.")
        return self

class ConfirmedGoal(GoalContext):
    members: list[Member] = Field(default_factory=list)
    execution_method: str = ""
    confirmed: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    checkins: list[str] = Field(default_factory=list)
    frequency_per_week: int | None = Field(default=None, ge=1, le=7)

    @model_validator(mode="after")
    def unique_members(self):
        ids = [m.member_id for m in self.members]
        if len(ids) != len(set(ids)) or not set(self.roles) <= set(ids):
            raise ValueError("구성원 ID 중복 또는 알 수 없는 역할 담당자입니다.")
        return self

class Task(Contract):
    task_id: str = ""
    owner_member_id: str | None = None
    title: str = Field(min_length=1)
    deliverable: str = Field(min_length=1)
    completion_criteria: list[str] = Field(min_length=1)
    due_date: date
    estimated_minutes: int = Field(gt=0)
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "MEDIUM"
    dependencies: list[str] = Field(default_factory=list)

class Milestone(Contract):
    title: str
    due_date: date
    description: str = ""

class Plan(Contract):
    plan_version_id: str = ""
    previous_plan_version_id: str | None = None
    status: Literal["DRAFT"] = "DRAFT"
    goal: str
    start_date: date
    end_date: date
    owner_member_id: str | None = None
    milestones: list[Milestone] = Field(default_factory=list)
    weekly_outcomes: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(min_length=1)
    adaptation_rules: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("milestones", mode="before")
    @classmethod
    def milestone_aliases(cls, rows):
        if not isinstance(rows, list): return rows
        output=[]
        for row in rows:
            if isinstance(row, dict):
                row=dict(row)
                for old,new in [("target_date","due_date"),("date","due_date"),("name","title"),("outcome","description")]:
                    if old in row:
                        if new in row and row[new]!=row[old]:raise ValueError("충돌한 마일스톤 값입니다.")
                        row[new]=row.pop(old)
            output.append(row)
        return output

class Issue(Contract):
    code: str
    field_path: str
    message: str

class ValidationReport(Contract):
    valid: bool
    scope: Literal["GROUP", "MEMBER", "BUNDLE"]
    issues: list[Issue] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)
    integration_ready: bool = False
