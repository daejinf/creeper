"""ProjectBrief input validation. Limits mirror the original group-create form (30 / 1000 / 12 / 20 characters)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..auth.errors import AuthError

NAME_MAX, CONTENT_MAX, TYPE_MAX, MS_NAME_MAX = 30, 1000, 12, 20
MAX_TYPES, MAX_MILESTONES = 10, 10
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # newline and tab stay legal in the content


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MilestoneBody(_Body):
    name: str = Field(max_length=200)
    date: str = Field(max_length=10, pattern=DATE_RE)


class BriefBody(_Body):
    name: str = Field(max_length=200)
    content: str = Field(default="", max_length=5000)
    start_date: str = Field(max_length=10, pattern=DATE_RE)
    end_date: str = Field(max_length=10, pattern=DATE_RE)
    types: list[str] = Field(max_length=50)
    milestones: list[MilestoneBody] = Field(default_factory=list, max_length=50)


class ActivateBody(BriefBody):
    pass


class UpdateBriefBody(BriefBody):
    base_revision: int = Field(ge=1, le=1_000_000)


@dataclass(frozen=True)
class Milestone:
    name: str
    date: date


@dataclass(frozen=True)
class BriefData:
    name: str
    content: str
    start_date: date
    end_date: date
    types: tuple[str, ...]
    milestones: tuple[Milestone, ...]


def _invalid(message: str, field: str) -> AuthError:
    return AuthError(422, "INVALID", message, field=field)


def _day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _invalid("날짜 형식이 올바르지 않아요.", field) from None


def validate_brief(body: BriefBody) -> BriefData:
    name = body.name.strip()
    if not name:
        raise _invalid("프로젝트 명을 입력해주세요.", "name")
    if len(name) > NAME_MAX or _CONTROL.search(name) or "\n" in name or "\t" in name:
        raise _invalid("프로젝트 명은 30자 이하로 입력해주세요.", "name")
    content = body.content.replace("\r\n", "\n").strip()
    if len(content) > CONTENT_MAX or _CONTROL.search(content):
        raise _invalid("프로젝트 내용은 1000자 이하로 입력해주세요.", "content")
    start, end = _day(body.start_date, "period"), _day(body.end_date, "period")
    if end < start:
        raise _invalid("마감일은 시작일 이후여야 해요.", "period")
    types: list[str] = []
    for raw in body.types:
        t = raw.strip()
        if not t or len(t) > TYPE_MAX or _CONTROL.search(t) or "\n" in t or "\t" in t:
            raise _invalid("결과물 형태는 12자 이하로 입력해주세요.", "types")
        if t.lower() not in {x.lower() for x in types}:
            types.append(t)
    if not types or len(types) > MAX_TYPES:
        raise _invalid("결과물 형태를 1~10개 골라주세요.", "types")
    if len(body.milestones) > MAX_MILESTONES:
        raise _invalid("중간 일정은 10개까지 추가할 수 있어요.", "milestones")
    milestones: list[Milestone] = []
    for m in body.milestones:
        mname = m.name.strip()
        if not mname or len(mname) > MS_NAME_MAX or _CONTROL.search(mname) or "\n" in mname or "\t" in mname:
            raise _invalid("중간 일정 이름은 1~20자로 입력해주세요.", "milestones")
        mday = _day(m.date, "milestones")
        if not (start <= mday <= end):
            raise _invalid("중간 일정은 프로젝트 기간 안에 있어야 해요.", "milestones")
        milestones.append(Milestone(mname, mday))
    return BriefData(name, content, start, end, tuple(types), tuple(milestones))


__all__ = ["ActivateBody", "UpdateBriefBody", "BriefBody", "BriefData", "Milestone", "validate_brief", "ValidationError"]
