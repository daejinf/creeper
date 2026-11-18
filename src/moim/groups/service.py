"""Invite rooms (FORMING), codes, joining, activation and ProjectBrief revisions.

Every state change is one transaction. Rows that guard a transition are locked (`FOR UPDATE`) so two concurrent
requests cannot both win: a second activation sees ACTIVE, a second join sees the room full, a stale brief
revision sees a newer one. Group rows are always locked in id order so crossing requests cannot deadlock.
Objects are authorized here: a caller that is not a member of a group gets NOT_FOUND, exactly like a missing one.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..models import (
    activation_cohorts,
    cohort_members,
    goal_displays,
    group_members,
    groups,
    invite_codes,
    project_briefs,
    users,
)
from ..timeutil import DISPLAY_TZ, utcnow
from .validation import BriefData

log = logging.getLogger("moim.groups")

FORMING, ACTIVE, CANCELLED = "FORMING", "ACTIVE", "CANCELLED"
LEADER, MEMBER = "LEADER", "MEMBER"
MAX_MEMBERS = 6  # the original invite page's MAX
MIN_MEMBERS_TO_CREATE = 2  # leader included (D-A5)
CODE_TTL = timedelta(hours=24)
SHAPES = ("c", "a", "q", "t")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CODE_RE = re.compile(r"^[0-9]{6}$")

MSG_NOT_FOUND = "찾을 수 없어요."
MSG_INVALID_CODE = "존재하지 않는 코드예요. 다시 확인해 주세요."
MSG_CODE_EXPIRED = "만료된 코드예요. 그룹장에게 새 코드를 요청해 주세요."
# Approved text (D-A5 / ROADMAP): an ACTIVE group cannot be joined until GATE B decides late joining.
MSG_NOT_JOINABLE = "이미 시작된 그룹이에요. 그룹장에게 새 초대 가능 여부를 확인해 주세요"
MSG_FULL = "인원이 가득 찼어요."
MSG_ALREADY_IN_ROOM = "이미 다른 초대방에서 대기 중이에요. 그룹이 시작될 때까지 기다려 주세요."
MSG_OWN_ROOM_HAS_MEMBERS = "참여자가 있는 내 초대방이 있어요. 초대방을 먼저 닫은 뒤 참여해 주세요."
MSG_FORBIDDEN = "그룹장만 할 수 있어요."
MSG_NEED_MEMBERS = "팀원 한 명 이상이 참여하면 그룹을 만들 수 있어요"
MSG_NOT_FORMING = "이미 시작되었거나 닫힌 초대방이에요."
MSG_ALREADY_ACTIVE = "이미 시작된 그룹이에요."
MSG_NOT_ACTIVE = "아직 시작되지 않은 그룹이에요."


class GroupError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def not_found() -> GroupError:
    return GroupError(404, "NOT_FOUND", MSG_NOT_FOUND)


def is_group_id(value: str) -> bool:
    return bool(UUID_RE.fullmatch(value))


@dataclass(frozen=True)
class Member:
    user_id: str
    name: str
    role: str
    shape: str


@dataclass(frozen=True)
class Room:
    group_id: str
    status: str
    owner_id: str
    is_leader: bool
    members: list[Member]
    code: str | None  # the live code, shown to the leader only
    code_expires_at: datetime | None
    brief_revision: int


@dataclass(frozen=True)
class GroupSummary:
    group_id: str
    status: str
    name: str | None
    end_date: date | None
    members: list[Member]


def today_kst(now: datetime) -> date:
    return now.astimezone(DISPLAY_TZ).date()


class GroupService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Callable[[], datetime] = utcnow):
        self._sf = session_factory
        self._clock = clock

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _lock_user(s: Session, user_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": "user:" + user_id})

    @staticmethod
    def _lock_groups(s: Session, *group_ids: str) -> dict[str, "object"]:
        ids = sorted(set(group_ids))
        rows = s.execute(select(groups).where(groups.c.id.in_(ids)).order_by(groups.c.id).with_for_update()).all()
        return {r.id: r for r in rows}

    @staticmethod
    def _members(s: Session, group_id: str) -> list[Member]:
        rows = s.execute(
            select(group_members.c.user_id, group_members.c.role, users.c.display_name)
            .join(users, users.c.id == group_members.c.user_id)
            .where(group_members.c.group_id == group_id)
            .order_by(group_members.c.joined_at, group_members.c.user_id)
        ).all()
        return [Member(r.user_id, r.display_name, r.role, SHAPES[i % len(SHAPES)]) for i, r in enumerate(rows)]

    @staticmethod
    def _forming_membership(s: Session, user_id: str):
        """The user's FORMING room, if any (at most one by construction)."""
        return s.execute(
            select(groups.c.id, groups.c.owner_id)
            .join(group_members, group_members.c.group_id == groups.c.id)
            .where(group_members.c.user_id == user_id, groups.c.status == FORMING)
        ).first()

    def _new_code(self, s: Session, group_id: str) -> str:
        """A random six-digit code that was never used before. The unique constraint is the arbiter."""
        now = self._clock()
        for _ in range(12):
            code = f"{secrets.randbelow(10**6):06d}"
            try:
                with s.begin_nested():
                    s.execute(
                        insert(invite_codes).values(
                            id=str(uuid.uuid4()), group_id=group_id, code=code, created_at=now, expires_at=now + CODE_TTL
                        )
                    )
                return code
            except IntegrityError:
                continue
        raise GroupError(503, "CODE_UNAVAILABLE", "초대코드를 만들지 못했어요. 잠시 후 다시 시도해주세요.")

    @staticmethod
    def _revoke_live_code(s: Session, group_id: str, reason: str, now: datetime) -> None:
        s.execute(
            update(invite_codes)
            .where(invite_codes.c.group_id == group_id, invite_codes.c.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )

    def _require_member(self, s: Session, group_id: str, user_id: str):
        """The locked group row, or NOT_FOUND for a missing group and a non-member alike."""
        if not is_group_id(group_id):
            raise not_found()
        row = self._lock_groups(s, group_id).get(group_id)
        if row is None:
            raise not_found()
        role = s.execute(
            select(group_members.c.role).where(group_members.c.group_id == group_id, group_members.c.user_id == user_id)
        ).scalar_one_or_none()
        if role is None:
            raise not_found()
        return row, role

    # ---- invite room ------------------------------------------------------------------------

    def create_or_reuse_room(self, user_id: str) -> tuple[str, bool]:
        """The only way a FORMING group comes to exist. Returns (group_id, reused)."""
        with self._sf.begin() as s:
            self._lock_user(s, user_id)
            existing = self._forming_membership(s, user_id)
            if existing is not None:
                if existing.owner_id != user_id:
                    raise GroupError(409, "ALREADY_IN_ROOM", MSG_ALREADY_IN_ROOM)
                return existing.id, True
            now = self._clock()
            group_id = str(uuid.uuid4())
            s.execute(insert(groups).values(id=group_id, owner_id=user_id, status=FORMING, created_at=now))
            s.execute(insert(group_members).values(group_id=group_id, user_id=user_id, role=LEADER, joined_at=now))
            self._new_code(s, group_id)
        log.info("group.room_created group_id=%s", group_id)
        return group_id, False

    def room(self, user_id: str, group_id: str) -> Room:
        with self._sf() as s:
            if not is_group_id(group_id):
                raise not_found()
            g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
            members = self._members(s, group_id) if g is not None else []
            me = next((m for m in members if m.user_id == user_id), None)
            if g is None or me is None:
                raise not_found()
            code = None
            expires = None
            if me.role == LEADER and g.status == FORMING:
                live = s.execute(
                    select(invite_codes.c.code, invite_codes.c.expires_at).where(
                        invite_codes.c.group_id == group_id, invite_codes.c.revoked_at.is_(None)
                    )
                ).first()
                if live is not None:
                    code, expires = live.code, live.expires_at
            return Room(group_id, g.status, g.owner_id, me.role == LEADER, members, code, expires, g.brief_revision)

    def my_forming_room_id(self, user_id: str) -> str | None:
        with self._sf() as s:
            row = self._forming_membership(s, user_id)
        return row.id if row else None

    def regenerate_code(self, user_id: str, group_id: str) -> str:
        """Discard the live code and issue a new one for the SAME room."""
        with self._sf.begin() as s:
            g, role = self._require_member(s, group_id, user_id)
            if role != LEADER:
                raise GroupError(403, "FORBIDDEN", MSG_FORBIDDEN)
            if g.status != FORMING:
                raise GroupError(409, "NOT_FORMING", MSG_NOT_FORMING)
            now = self._clock()
            self._revoke_live_code(s, group_id, "REISSUED", now)
            code = self._new_code(s, group_id)
        log.info("group.code_reissued group_id=%s", group_id)
        return code

    def cancel_room(self, user_id: str, group_id: str) -> None:
        """Explicit cleanup by the leader. Members of a closed room are simply no longer waiting."""
        with self._sf.begin() as s:
            g, role = self._require_member(s, group_id, user_id)
            if role != LEADER:
                raise GroupError(403, "FORBIDDEN", MSG_FORBIDDEN)
            if g.status == CANCELLED:
                return  # idempotent
            if g.status != FORMING:
                raise GroupError(409, "NOT_FORMING", MSG_NOT_FORMING)
            now = self._clock()
            s.execute(update(groups).where(groups.c.id == group_id).values(status=CANCELLED, cancelled_at=now))
            self._revoke_live_code(s, group_id, "CANCELLED", now)
        log.info("group.room_cancelled group_id=%s", group_id)

    def join_by_code(self, user_id: str, code: str) -> tuple[str, bool]:
        """Join the FORMING room that owns `code`. Returns (group_id, newly_joined)."""
        if not CODE_RE.fullmatch(code):
            raise GroupError(404, "INVALID_CODE", MSG_INVALID_CODE)
        with self._sf.begin() as s:
            self._lock_user(s, user_id)
            row = s.execute(select(invite_codes).where(invite_codes.c.code == code)).one_or_none()
            if row is None:
                raise GroupError(404, "INVALID_CODE", MSG_INVALID_CODE)
            if row.revoke_reason == "ACTIVATED":
                raise GroupError(409, "GROUP_NOT_JOINABLE", MSG_NOT_JOINABLE)
            mine = self._forming_membership(s, user_id)
            locked = self._lock_groups(s, row.group_id, *([mine.id] if mine else []))
            target = locked[row.group_id]
            if target.status == ACTIVE:
                raise GroupError(409, "GROUP_NOT_JOINABLE", MSG_NOT_JOINABLE)
            now = self._clock()
            if target.status != FORMING or row.revoked_at is not None or row.expires_at <= now:
                raise GroupError(410, "CODE_EXPIRED", MSG_CODE_EXPIRED)
            already = s.execute(
                select(group_members.c.user_id).where(
                    group_members.c.group_id == target.id, group_members.c.user_id == user_id
                )
            ).first()
            if already is not None:
                return target.id, False  # the same code again is not an error
            if mine is not None:  # the membership read above is re-checked under the lock below
                other = locked[mine.id]
                if other.status == FORMING:
                    if other.owner_id != user_id:
                        raise GroupError(409, "ALREADY_IN_ROOM", MSG_ALREADY_IN_ROOM)
                    n = s.execute(
                        select(func.count()).select_from(group_members).where(group_members.c.group_id == other.id)
                    ).scalar_one()
                    if n > 1:
                        raise GroupError(409, "OWN_ROOM_HAS_MEMBERS", MSG_OWN_ROOM_HAS_MEMBERS)
                    s.execute(update(groups).where(groups.c.id == other.id).values(status=CANCELLED, cancelled_at=now))
                    self._revoke_live_code(s, other.id, "CANCELLED", now)
                    log.info("group.room_cancelled group_id=%s reason=owner_joined_elsewhere", other.id)
            count = s.execute(
                select(func.count()).select_from(group_members).where(group_members.c.group_id == target.id)
            ).scalar_one()
            if count >= MAX_MEMBERS:
                raise GroupError(409, "ROOM_FULL", MSG_FULL)
            s.execute(insert(group_members).values(group_id=target.id, user_id=user_id, role=MEMBER, joined_at=now))
        log.info("group.joined group_id=%s user_id=%s", target.id, user_id)
        return target.id, True

    # ---- create-page gate -------------------------------------------------------------------

    def can_open_create(self, user_id: str, group_id: str) -> str:
        """For the ProjectBrief page: 'ok', or the reason it must not open (never changes state)."""
        with self._sf() as s:
            if not is_group_id(group_id):
                raise not_found()
            g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
            members = self._members(s, group_id) if g is not None else []
            me = next((m for m in members if m.user_id == user_id), None)
            if g is None or me is None:
                raise not_found()
            if me.role != LEADER:
                raise GroupError(403, "FORBIDDEN", MSG_FORBIDDEN)
            if g.status == ACTIVE:
                return "active"
            if g.status != FORMING:
                return "closed"
            return "ok" if len(members) >= MIN_MEMBERS_TO_CREATE else "need_members"

    # ---- activation -------------------------------------------------------------------------

    def activate(self, user_id: str, group_id: str, brief: BriefData) -> int:
        """ProjectBrief revision 1 + cohort snapshot + FORMING -> ACTIVE + code discard, in one transaction."""
        with self._sf.begin() as s:
            g, role = self._require_member(s, group_id, user_id)
            if role != LEADER:
                raise GroupError(403, "FORBIDDEN", MSG_FORBIDDEN)
            if g.status == ACTIVE:
                raise GroupError(409, "ALREADY_ACTIVE", MSG_ALREADY_ACTIVE)
            if g.status != FORMING:
                raise GroupError(409, "NOT_FORMING", MSG_NOT_FORMING)
            now = self._clock()
            if brief.start_date < today_kst(now):
                raise GroupError(422, "INVALID", "시작일은 오늘 이후여야 해요.", field="period")
            members = self._members(s, group_id)
            if len(members) < MIN_MEMBERS_TO_CREATE:
                raise GroupError(409, "NOT_ENOUGH_MEMBERS", MSG_NEED_MEMBERS)
            self._insert_brief(s, group_id, 1, brief, user_id, now)
            s.execute(
                insert(activation_cohorts).values(group_id=group_id, cohort_revision=1, activated_by=user_id, created_at=now)
            )
            s.execute(
                insert(cohort_members),
                [{"group_id": group_id, "cohort_revision": 1, "user_id": m.user_id} for m in members],
            )
            s.execute(
                update(groups)
                .where(groups.c.id == group_id)
                .values(status=ACTIVE, cohort_revision=1, brief_revision=1, activated_at=now)
            )
            self._revoke_live_code(s, group_id, "ACTIVATED", now)
        log.info("group.activated group_id=%s members=%d", group_id, len(members))
        return 1

    def _insert_brief(self, s: Session, group_id: str, revision: int, brief: BriefData, user_id: str, now: datetime) -> None:
        s.execute(
            insert(project_briefs).values(
                group_id=group_id,
                revision=revision,
                name=brief.name,
                content=brief.content,
                start_date=brief.start_date,
                end_date=brief.end_date,
                deliverable_types=list(brief.types),
                milestones=[{"name": m.name, "date": m.date.isoformat()} for m in brief.milestones],
                created_by=user_id,
                created_at=now,
            )
        )
        # The team goal sentence is derived from this revision's content. Empty content has nothing to derive from.
        s.execute(
            insert(goal_displays).values(
                group_id=group_id,
                brief_revision=revision,
                state="PENDING" if brief.content.strip() else "EMPTY",
                created_at=now,
                updated_at=now,
            )
        )

    # ---- ProjectBrief -----------------------------------------------------------------------

    def update_brief(self, user_id: str, group_id: str, base_revision: int, brief: BriefData) -> tuple[int, bool]:
        """New revision on top of `base_revision`. Returns (revision, changed). A stale base is a conflict."""
        with self._sf.begin() as s:
            g, role = self._require_member(s, group_id, user_id)
            if role != LEADER:
                raise GroupError(403, "FORBIDDEN", MSG_FORBIDDEN)
            if g.status != ACTIVE:
                raise GroupError(409, "GROUP_NOT_ACTIVE", MSG_NOT_ACTIVE)
            if g.brief_revision != base_revision:
                raise GroupError(
                    409, "REVISION_CONFLICT", "다른 곳에서 먼저 수정되었어요. 새로고침해서 최신 내용을 확인해 주세요."
                )
            current = self._brief_row(s, group_id, g.brief_revision)
            if current is not None and _same(current, brief):
                return g.brief_revision, False
            now = self._clock()
            if current is not None and brief.start_date != current.start_date and brief.start_date < today_kst(now):
                raise GroupError(422, "INVALID", "시작일은 오늘 이후여야 해요.", field="period")
            revision = g.brief_revision + 1
            self._insert_brief(s, group_id, revision, brief, user_id, now)
            s.execute(update(groups).where(groups.c.id == group_id).values(brief_revision=revision))
        log.info("group.brief_revised group_id=%s revision=%d", group_id, revision)
        return revision, True

    @staticmethod
    def _brief_row(s: Session, group_id: str, revision: int):
        return s.execute(
            select(project_briefs).where(project_briefs.c.group_id == group_id, project_briefs.c.revision == revision)
        ).one_or_none()

    def current_brief(self, user_id: str, group_id: str):
        """The current ProjectBrief for a cohort member of an ACTIVE group (row + group row + whether caller leads)."""
        with self._sf() as s:
            if not is_group_id(group_id):
                raise not_found()
            g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
            role = s.execute(
                select(group_members.c.role).where(group_members.c.group_id == group_id, group_members.c.user_id == user_id)
            ).scalar_one_or_none()
            if g is None or role is None:
                raise not_found()
            if g.status != ACTIVE:
                raise GroupError(409, "GROUP_NOT_ACTIVE", MSG_NOT_ACTIVE)
            cohort = s.execute(
                select(cohort_members.c.user_id).where(
                    cohort_members.c.group_id == group_id,
                    cohort_members.c.cohort_revision == g.cohort_revision,
                    cohort_members.c.user_id == user_id,
                )
            ).first()
            if cohort is None:
                raise not_found()
            brief = self._brief_row(s, group_id, g.brief_revision)
            members = self._cohort_member_list(s, group_id, g.cohort_revision)
        return g, brief, role == LEADER, members

    @staticmethod
    def _cohort_member_list(s: Session, group_id: str, cohort_revision: int) -> list[Member]:
        rows = s.execute(
            select(cohort_members.c.user_id, users.c.display_name, group_members.c.role, group_members.c.joined_at)
            .join(users, users.c.id == cohort_members.c.user_id)
            .join(
                group_members,
                (group_members.c.group_id == cohort_members.c.group_id)
                & (group_members.c.user_id == cohort_members.c.user_id),
            )
            .where(cohort_members.c.group_id == group_id, cohort_members.c.cohort_revision == cohort_revision)
            .order_by(group_members.c.joined_at, cohort_members.c.user_id)
        ).all()
        return [Member(r.user_id, r.display_name, r.role, SHAPES[i % len(SHAPES)]) for i, r in enumerate(rows)]

    # ---- lists ------------------------------------------------------------------------------

    def groups_for(self, user_id: str) -> list[GroupSummary]:
        with self._sf() as s:
            rows = s.execute(
                select(groups)
                .join(group_members, group_members.c.group_id == groups.c.id)
                .where(group_members.c.user_id == user_id, groups.c.status.in_((FORMING, ACTIVE)))
                .order_by(func.coalesce(groups.c.activated_at, groups.c.created_at).desc(), groups.c.id)
            ).all()
            out = []
            for g in rows:
                name = end = None
                if g.status == ACTIVE:
                    b = self._brief_row(s, g.id, g.brief_revision)
                    name, end = b.name, b.end_date
                out.append(GroupSummary(g.id, g.status, name, end, self._members(s, g.id)))
        return out

    def cohort_has(self, user_id: str, group_id: str) -> str:
        """Chat gate: 'ok' for a cohort member of an ACTIVE group; otherwise why not."""
        with self._sf() as s:
            if not is_group_id(group_id):
                raise not_found()
            g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
            role = s.execute(
                select(group_members.c.role).where(group_members.c.group_id == group_id, group_members.c.user_id == user_id)
            ).scalar_one_or_none()
            if g is None or role is None:
                raise not_found()
            if g.status != ACTIVE:
                return g.status.lower()
            in_cohort = s.execute(
                select(cohort_members.c.user_id).where(
                    cohort_members.c.group_id == group_id,
                    cohort_members.c.cohort_revision == g.cohort_revision,
                    cohort_members.c.user_id == user_id,
                )
            ).first()
            return "ok" if in_cohort else "not_in_cohort"


def _same(row, brief: BriefData) -> bool:
    return (
        row.name == brief.name
        and row.content == brief.content
        and row.start_date == brief.start_date
        and row.end_date == brief.end_date
        and list(row.deliverable_types) == list(brief.types)
        and [(m["name"], m["date"]) for m in row.milestones] == [(m.name, m.date.isoformat()) for m in brief.milestones]
    )
