"""Stage 3 role-preference submission (D-B3): no personal interview, a shared candidate set, a private 1st/2nd/3rd choice.

Everything here is private to one cohort member except the submitted-count aggregate. A member's ranking is read and
written only through (group_id, user_id) of the caller, and a caller outside the group's ACTIVE cohort gets NOT_FOUND
exactly like a missing group. State changes are one transaction each, serialized per member by an advisory lock.
Nothing here needs an interview, a MemberState or an AI call, and nothing here assigns roles or makes todos.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import ACTIVE, is_group_id
from ..models import cohort_members, groups, member_progress, preference_submissions, role_candidate_sets
from ..timeutil import utcnow
from .candidates import SUCCEEDED as CANDIDATES_READY

log = logging.getLogger("moim.interview")

INTERVIEW, WAITING_ASSIGNMENT = "INTERVIEW", "WAITING_ASSIGNMENT"  # stored member status (INTERVIEW = ranking not submitted)
ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MSG_NOT_FOUND = "찾을 수 없어요."


class RankingError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def not_found() -> RankingError:
    return RankingError(404, "NOT_FOUND", MSG_NOT_FOUND)


def _action(action_id: str) -> str:
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise RankingError(422, "INVALID", "요청 형식이 올바르지 않아요.")
    return action_id


class RankingService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Callable = utcnow):
        self._sf = session_factory
        self._clock = clock

    @staticmethod
    def _lock_member(s: Session, group_id: str, user_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"ranking:{group_id}:{user_id}"})

    @staticmethod
    def _cohort_group(s: Session, group_id: str, user_id: str):
        """The group row for a member of its ACTIVE cohort; NOT_FOUND for everyone else (missing group included)."""
        if not is_group_id(group_id):
            raise not_found()
        g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
        if g is None or g.status != ACTIVE:
            raise not_found()
        member = s.execute(
            select(cohort_members.c.user_id).where(
                cohort_members.c.group_id == group_id,
                cohort_members.c.cohort_revision == g.cohort_revision,
                cohort_members.c.user_id == user_id,
            )
        ).first()
        if member is None:
            raise not_found()
        return g

    @staticmethod
    def _progress(s: Session, group_id: str, user_id: str):
        return s.execute(
            select(member_progress).where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
        ).one_or_none()

    @staticmethod
    def _candidate_row(s: Session, group_id: str, revision: int):
        return s.execute(
            select(role_candidate_sets).where(
                role_candidate_sets.c.group_id == group_id,
                role_candidate_sets.c.source_project_brief_revision == revision,
            )
        ).one_or_none()

    def view(self, user_id: str, group_id: str) -> dict:
        """What the chat screen needs from this member's own data, read in one pass."""
        with self._sf() as s:
            g = self._cohort_group(s, group_id, user_id)
            progress = self._progress(s, group_id, user_id)
            status = progress.status if progress else INTERVIEW
            cand_row = self._candidate_row(s, group_id, g.brief_revision)
            had_submission = (
                s.execute(
                    select(func.count()).select_from(preference_submissions).where(
                        preference_submissions.c.group_id == group_id, preference_submissions.c.user_id == user_id
                    )
                ).scalar_one()
                > 0
            )
            submitted_ranks = None
            if status == WAITING_ASSIGNMENT and progress.submission_revision is not None:
                sub = s.execute(
                    select(preference_submissions).where(
                        preference_submissions.c.group_id == group_id,
                        preference_submissions.c.user_id == user_id,
                        preference_submissions.c.revision == progress.submission_revision,
                    )
                ).one()
                names = {c["id"]: c["name"] for c in (cand_row.candidates or [])} if cand_row is not None else {}
                submitted_ranks = [
                    {"rank": i + 1, "id": cid, "name": names.get(cid, "")} for i, cid in enumerate((sub.rank1, sub.rank2, sub.rank3))
                ]
            total = s.execute(
                select(func.count()).select_from(cohort_members).where(
                    cohort_members.c.group_id == group_id, cohort_members.c.cohort_revision == g.cohort_revision
                )
            ).scalar_one()
            done = s.execute(
                select(func.count()).select_from(member_progress)
                .join(
                    cohort_members,
                    (cohort_members.c.group_id == member_progress.c.group_id)
                    & (cohort_members.c.user_id == member_progress.c.user_id)
                    & (cohort_members.c.cohort_revision == g.cohort_revision),
                )
                .where(member_progress.c.group_id == group_id, member_progress.c.status == WAITING_ASSIGNMENT)
            ).scalar_one()
        return {
            "phase": status,
            # The stored ranking is the member's own; it is shown back only to them, with the role names.
            "ranking_reset": status == INTERVIEW and had_submission,
            "submitted_ranks": submitted_ranks,
            # D-B1 B2: the count (never who) is shown to every cohort member, the leader included.
            "progress": {"submitted": done, "total": total},
            "brief_revision": g.brief_revision,
        }

    def submit_preferences(self, user_id: str, group_id: str, action_id: str, candidate_revision: int, ranks: list[str]) -> bool:
        """Store the member's 1st/2nd/3rd choice once. Returns True if stored now, False for a replay of the same action."""
        _action(action_id)
        with self._sf.begin() as s:
            self._lock_member(s, group_id, user_id)
            g = self._cohort_group(s, group_id, user_id)
            progress = self._progress(s, group_id, user_id)
            replay = s.execute(
                select(preference_submissions.c.revision).where(
                    preference_submissions.c.group_id == group_id,
                    preference_submissions.c.user_id == user_id,
                    preference_submissions.c.action_id == action_id,
                )
            ).first()
            if replay is not None:
                return False
            if progress is not None and progress.status == WAITING_ASSIGNMENT:
                raise RankingError(409, "ALREADY_SUBMITTED", "이미 순위를 제출했어요.")
            if candidate_revision != g.brief_revision:
                raise RankingError(
                    409, "STALE_CANDIDATES", "프로젝트 정보가 바뀌어 역할 후보가 새로 만들어졌어요. 새로고침해서 다시 골라 주세요."
                )
            cand = self._candidate_row(s, group_id, g.brief_revision)
            if cand is None or cand.state != CANDIDATES_READY or not cand.candidates:
                raise RankingError(409, "NO_CANDIDATES", "역할 후보가 아직 준비되지 않았어요.")
            ids = [c["id"] for c in cand.candidates]
            if (
                not isinstance(ranks, list) or len(ranks) != 3 or any(not isinstance(r, str) for r in ranks)
                or len(set(ranks)) != 3 or any(r not in ids for r in ranks)
            ):
                raise RankingError(422, "INVALID_RANKS", "서로 다른 역할 후보 세 개를 1·2·3순위로 골라 주세요.", field="ranks")
            prev = s.execute(
                select(func.coalesce(func.max(preference_submissions.c.revision), 0)).where(
                    preference_submissions.c.group_id == group_id, preference_submissions.c.user_id == user_id
                )
            ).scalar_one()
            revision = prev + 1
            now = self._clock()
            s.execute(
                insert(preference_submissions).values(
                    group_id=group_id, user_id=user_id, revision=revision, source_candidate_revision=g.brief_revision,
                    action_id=action_id, rank1=ranks[0], rank2=ranks[1], rank3=ranks[2], created_at=now,
                )
            )
            if progress is None:
                s.execute(
                    insert(member_progress).values(
                        group_id=group_id, user_id=user_id, status=WAITING_ASSIGNMENT, submission_revision=revision, updated_at=now
                    )
                )
            else:
                s.execute(
                    update(member_progress)
                    .where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
                    .values(status=WAITING_ASSIGNMENT, submission_revision=revision, updated_at=now)
                )
        log.info("ranking_event group_id=%s user_id=%s state=WAITING_ASSIGNMENT submission_revision=%d", group_id, user_id, revision)
        return True
