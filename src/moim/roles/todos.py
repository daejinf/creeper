"""Todo draft, edits and confirmation (stage 4, steps 7-10). Owner-only, one member at a time, never blocking anyone else.

A member whose post-assignment interview is DONE and whose group's team coverage SUCCEEDED gets one draft for the
CURRENT assignment revision (`todo_drafts`, linked to the interview snapshot and the coverage of that revision). AI
steps (`todo_ai_steps`: DRAFT, REVISE, CRITERIA) run as background tasks, PENDING -> RUNNING -> SUCCEEDED | FAILED, and a
failure is an explicit retry. Direct edits are plain code. `투두 확정하기` copies the visible todos and their hidden
criteria into one immutable revision; todos whose title changed or that were added first get new hidden criteria
(one CRITERIA step), and confirming waits for it. A failed criteria step blocks the confirmation.

The browser sees titles, due dates and a draft-local `key` (item_no). `task_id`, the confirmation revision and the
hidden criteria never leave the server. The group sees only role name, title and due date of each member's newest
confirmation (D-B1 B7/B9).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import date
from typing import Callable

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import today_kst
from ..interview.candidates import project_input
from ..interview.ranking import RankingService
from ..models import (
    member_progress,
    project_briefs,
    role_assignment_members,
    role_assignments,
    role_interview_snapshots,
    role_interviews,
    team_coverages,
    todo_actions,
    todo_ai_steps,
    todo_confirmations,
    todo_confirmed_items,
    todo_drafts,
    todo_items,
)
from ..timeutil import utcnow
from . import interview_ai
from . import todo_ai as ai
from .assignment import AssignmentService

log = logging.getLogger("moim.todos")

PENDING, RUNNING, SUCCEEDED, FAILED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED"
EDITING, CONFIRMED = "EDITING", "CONFIRMED"
DRAFT, REVISE, CRITERIA = "DRAFT", "REVISE", "CRITERIA"
ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SLOW_CODES = {"TIMEOUT", "NETWORK_ERROR", "UPSTREAM_ERROR", "RATE_LIMITED"}
MAX_STEPS = 60
MSG_DRAFT_FAILED = "투두 초안을 만들지 못했어요. 다시 시도해주세요."
MSG_DRAFT_INVALID = "만든 투두 초안이 기준(기간·분량·내 결과물)에 맞지 않아 저장하지 않았어요. 다시 시도해주세요."
MSG_REVISE_FAILED = "수정 요청을 처리하지 못했어요. 다시 시도해주세요."
MSG_CRITERIA_FAILED = "고치거나 추가한 투두의 완료 기준을 만들지 못해 확정하지 않았어요. 다시 시도해주세요."
MSG_SLOW = "AI 응답이 늦거나 연결되지 않았어요. 다시 시도해주세요."
MSG_RESTART = "서버가 다시 시작되어 작업이 중단되었어요. 다시 시도해주세요."
MSG_CONFLICT = "그 사이 투두가 바뀌어 이 요청은 적용하지 않았어요. 다시 말해 주세요."
MSG_CONFIRMED = "투두를 확정했어요. 그룹 구성원에게는 역할명과 투두 제목·마감일만 보여요."


class TodoError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def _action(action_id: str) -> str:
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
    return action_id


def _key_no(key: str) -> int:
    m = re.fullmatch(r"k([1-9][0-9]{0,4})", key or "")
    if not m:
        raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
    return int(m[1])


class _StepFailed(Exception):
    def __init__(self, code: str, done=None):
        super().__init__(code)
        self.code, self.done = code, done


class TodoService:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], object], clock=utcnow):
        self._sf = session_factory
        self._client_factory = client_factory
        self._clock = clock

    # ---- shared reads -----------------------------------------------------------------------------------------------

    @staticmethod
    def _lock(s: Session, group_id: str, user_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"todos:{group_id}:{user_id}"})

    @staticmethod
    def _key(group_id: str, user_id: str, rev: int):
        return lambda t: (t.c.group_id == group_id) & (t.c.user_id == user_id) & (t.c.source_assignment_revision == rev)

    def _current(self, s: Session, group_id: str, user_id: str):
        """The CURRENT assignment revision for a cohort member in ROLE_TODOS. 404 for outsiders, 409 otherwise."""
        RankingService._cohort_group(s, group_id, user_id)
        progress = s.execute(
            select(member_progress).where(member_progress.c.group_id == group_id, member_progress.c.user_id == user_id)
        ).one_or_none()
        current = AssignmentService.current(s, group_id)
        if progress is None or progress.status != "ROLE_TODOS" or current is None or progress.assignment_revision != current.revision:
            raise TodoError(409, "NOT_ASSIGNED", "역할이 정해진 뒤에 투두를 만들 수 있어요.")
        return current

    def _draft(self, s: Session, group_id: str, user_id: str, rev: int, lock: bool = False):
        k = self._key(group_id, user_id, rev)
        q = select(todo_drafts).where(k(todo_drafts))
        row = s.execute(q.with_for_update() if lock else q).one_or_none()
        if row is not None and row.stale_at is not None:
            raise TodoError(409, "STALE", "역할이나 프로젝트 정보가 바뀌어 이 투두는 더 이상 고칠 수 없어요.")
        return row

    def _items(self, s: Session, group_id: str, user_id: str, rev: int):
        k = self._key(group_id, user_id, rev)
        return s.execute(
            select(todo_items).where(k(todo_items), todo_items.c.deleted_at.is_(None)).order_by(todo_items.c.due_date.asc().nulls_last(), todo_items.c.position, todo_items.c.item_no)
        ).all()

    def _steps(self, s: Session, group_id: str, user_id: str, rev: int):
        k = self._key(group_id, user_id, rev)
        return s.execute(select(todo_ai_steps).where(k(todo_ai_steps)).order_by(todo_ai_steps.c.seq)).all()

    def _running(self, s: Session, group_id: str, user_id: str, rev: int):
        k = self._key(group_id, user_id, rev)
        return s.execute(
            select(todo_ai_steps.c.seq, todo_ai_steps.c.kind).where(k(todo_ai_steps), todo_ai_steps.c.state.in_((PENDING, RUNNING)))
        ).first()

    def _inputs(self, s: Session, group_id: str, user_id: str, rev: int, now) -> tuple[dict, dict, date]:
        """(B3 provider payload, deadline limits, today). Raises TodoError when an input is missing or STALE."""
        brief = s.execute(
            select(project_briefs)
            .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                  & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
            .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == rev)
        ).one()
        mine = AssignmentService.mine(s, group_id, user_id, rev)
        slots = AssignmentService.team_table(s, group_id, rev)
        cov = s.execute(
            select(team_coverages.c.state, team_coverages.c.parts, team_coverages.c.stale_at).where(
                team_coverages.c.group_id == group_id, team_coverages.c.source_assignment_revision == rev
            )
        ).one_or_none()
        if cov is None or cov.state != SUCCEEDED or cov.stale_at is not None:
            raise TodoError(409, "COVERAGE_NOT_READY", "팀 결과물 분담이 아직 정해지지 않았어요.")
        snap = s.execute(
            select(role_interview_snapshots).where(self._key(group_id, user_id, rev)(role_interview_snapshots))
        ).one_or_none()
        if snap is None or snap.stale_at is not None:
            raise TodoError(409, "INTERVIEW_NOT_DONE", "인터뷰를 마친 뒤에 투두를 만들 수 있어요.")
        team, my_parts = ai.team_input(slots, cov.parts, mine.slot_id)
        today = today_kst(now)
        payload = ai.base_payload(
            project_input(brief), {"role": mine.role_name, "description": mine.role_description}, team, my_parts,
            snap.provider_input, brief.start_date, brief.end_date, today,
        )
        return payload, snap.deadline_limits, today

    # ---- screen -----------------------------------------------------------------------------------------------------

    def view(self, s: Session, group_id: str, user_id: str, rev: int, interview_done: bool, coverage_state: str) -> dict:
        """The owner's draft: visible fields only. Never task_id, a confirmation revision or a hidden criterion."""
        draft = s.execute(select(todo_drafts).where(self._key(group_id, user_id, rev)(todo_drafts))).one_or_none()
        brief = s.execute(
            select(project_briefs.c.start_date, project_briefs.c.end_date)
            .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                  & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
            .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == rev)
        ).one()
        today = today_kst(self._clock())
        base = {
            "max_todos": ai.MAX_TODOS,
            "period": {"first_day": ai.first_day(brief.start_date, today).isoformat(), "end_date": brief.end_date.isoformat(),
                       "today": today.isoformat()},
        }
        if draft is None:
            return {**base, "state": "NONE", "can_start": interview_done and coverage_state == SUCCEEDED, "items": [],
                    "messages": [], "busy": False, "failed": None, "mode": EDITING, "draft_revision": 0,
                    "confirmed_once": False, "confirming": False, "stale": False, "input_open": False}
        steps = self._steps(s, group_id, user_id, rev)
        items = self._items(s, group_id, user_id, rev) if draft.draft_revision > 0 else []
        messages, busy, failed, confirming = [], False, None, False
        for st in steps:
            if st.kind == REVISE:
                messages.append({"from": "me", "text": st.user_message})
            if st.state in (PENDING, RUNNING):
                busy = True
                confirming = confirming or st.kind == CRITERIA
                continue
            if st.state == FAILED:
                messages.append({"from": "ai", "text": st.error_message or MSG_DRAFT_FAILED, "err": True})
                continue
            if st.reply:
                messages.append({"from": "ai", "text": st.reply})
        last = steps[-1] if steps else None
        if last is not None and last.state == FAILED:
            failed = {"seq": last.seq, "attempt": last.attempt, "kind": last.kind}
        state = "READY" if draft.draft_revision > 0 else ("FAILED" if failed else "GENERATING")
        editable = state == "READY" and draft.mode == EDITING and not busy and draft.stale_at is None
        return {
            **base,
            "state": state,
            "can_start": False,
            "items": [{"key": f"k{it.item_no}", "title": it.title, "due_date": it.due_date.isoformat() if it.due_date else None}
                      for it in items],
            "messages": messages,
            "busy": busy,
            "failed": failed,
            "mode": draft.mode,
            "draft_revision": draft.draft_revision,
            "confirmed_once": draft.confirmed_revision is not None,
            "confirming": confirming,
            "stale": draft.stale_at is not None,
            "input_open": editable,
        }

    # ---- start: the first draft -------------------------------------------------------------------------------------

    def start(self, user_id: str, group_id: str, action_id: str) -> tuple[str, int | None]:
        _action(action_id)
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            current = self._current(s, group_id, user_id)
            rev = current.revision
            if self._draft(s, group_id, user_id, rev) is not None:
                return "noop", None
            interview = s.execute(select(role_interviews.c.state).where(self._key(group_id, user_id, rev)(role_interviews))).scalar_one()
            if interview != "DONE":
                raise TodoError(409, "INTERVIEW_NOT_DONE", "인터뷰를 마친 뒤에 투두를 만들 수 있어요.")
            self._inputs(s, group_id, user_id, rev, self._clock())  # coverage and snapshot must be ready
            snap_rev = s.execute(
                select(role_interview_snapshots.c.member_state_revision).where(self._key(group_id, user_id, rev)(role_interview_snapshots))
            ).scalar_one()
            reused = s.execute(select(todo_ai_steps.c.seq).where(
                todo_ai_steps.c.group_id == group_id, todo_ai_steps.c.user_id == user_id,
                todo_ai_steps.c.action_id == action_id)).first()
            if reused is not None:  # the id of an earlier (STALE) revision's draft: a page left open across a reassignment
                raise TodoError(409, "ACTION_REUSED", "이 화면이 오래됐어요. 새로고침해 주세요.")
            now = self._clock()
            s.execute(insert(todo_drafts).values(group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                                                 member_state_revision=snap_rev, created_at=now, updated_at=now))
            s.execute(insert(todo_ai_steps).values(group_id=group_id, user_id=user_id, source_assignment_revision=rev, seq=1,
                                                   kind=DRAFT, action_id=action_id, state=PENDING, base_draft_revision=0,
                                                   created_at=now, updated_at=now))
        log.info("todo_event group_id=%s user_id=%s revision=%d kind=DRAFT state=PENDING", group_id, user_id, rev)
        return "started", rev

    # ---- direct edits (code only) -----------------------------------------------------------------------------------

    def _editable(self, s, group_id, user_id, action_id, kind, draft_revision):
        """Lock, check and record an action. Returns (rev, draft) or raises; ("replay", None) for a resent action."""
        self._lock(s, group_id, user_id)
        rev = self._current(s, group_id, user_id).revision
        draft = self._draft(s, group_id, user_id, rev, lock=True)
        if draft is None or draft.draft_revision == 0:
            raise TodoError(409, "NO_DRAFT", "투두 초안이 아직 없어요.")
        seen = s.execute(select(todo_actions.c.kind).where(
            todo_actions.c.group_id == group_id, todo_actions.c.user_id == user_id, todo_actions.c.action_id == action_id
        )).first()
        if seen is not None:
            return rev, None
        if self._running(s, group_id, user_id, rev) is not None:
            raise TodoError(409, "BUSY", "AI가 투두를 처리하고 있어요. 잠시 후 다시 시도해주세요.")
        if kind == "EDIT" and draft.mode == CONFIRMED:
            raise TodoError(409, "CONFIRMED", "확정된 투두예요. 고치려면 먼저 확정을 풀어 주세요.")
        if draft_revision is not None and draft_revision != draft.draft_revision:
            raise TodoError(409, "CONFLICT", "다른 곳에서 투두가 바뀌었어요. 최신 내용으로 다시 불러왔어요.")
        s.execute(insert(todo_actions).values(group_id=group_id, user_id=user_id, action_id=action_id,
                                              source_assignment_revision=rev, kind=kind, created_at=self._clock()))
        return rev, draft

    def edit(self, user_id: str, group_id: str, action_id: str, draft_revision: int, op: str, key: str | None,
             title: str | None, due_date: str | None) -> str:
        """update (title and/or due date), add (title, optional due date) or delete. Returns "done" or "replay"."""
        _action(action_id)
        if op not in ("update", "add", "delete"):
            raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
        if title is not None:
            title = re.sub(r"\s+", " ", title.strip())
            if not (1 <= len(title) <= ai.TITLE_MAX) or re.search(r"[\x00-\x1f\x7f]", title):
                raise TodoError(422, "INVALID_TITLE", f"할 일은 1~{ai.TITLE_MAX}자로 적어 주세요.", field="title")
        with self._sf.begin() as s:
            rev, draft = self._editable(s, group_id, user_id, action_id, "EDIT", draft_revision)
            if draft is None:
                return "replay"
            k = self._key(group_id, user_id, rev)
            due = self._due(s, group_id, rev, due_date) if due_date is not None else None
            now = self._clock()
            items = self._items(s, group_id, user_id, rev)
            if op == "add":
                if title is None or key is not None:
                    raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
                if len(items) >= ai.MAX_TODOS:
                    raise TodoError(409, "TOO_MANY", f"투두는 최대 {ai.MAX_TODOS}개까지 만들 수 있어요.")
                s.execute(insert(todo_items).values(
                    task_id=str(uuid.uuid4()), group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                    item_no=draft.next_item_no, position=(max((i.position for i in items), default=0) + 1), title=title,
                    due_date=due, origin="USER", created_at=now, updated_at=now,
                ))
                extra = {"next_item_no": draft.next_item_no + 1}
            else:
                no = _key_no(key)
                target = next((i for i in items if i.item_no == no), None)
                if target is None:
                    raise TodoError(404, "NOT_FOUND", "찾을 수 없는 투두예요.")
                if op == "delete":
                    if title is not None or due_date is not None:
                        raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
                    s.execute(update(todo_items).where(todo_items.c.task_id == target.task_id).values(deleted_at=now, updated_at=now))
                else:
                    if title is None and due_date is None:
                        raise TodoError(422, "INVALID", "요청 형식이 올바르지 않아요.")
                    values = {"updated_at": now}
                    if title is not None:
                        values["title"] = title
                    if due_date is not None:
                        values["due_date"] = due
                    s.execute(update(todo_items).where(todo_items.c.task_id == target.task_id).values(**values))
                extra = {}
            s.execute(update(todo_drafts).where(k(todo_drafts)).values(draft_revision=draft.draft_revision + 1, updated_at=now, **extra))
        return "done"

    def _due(self, s, group_id: str, rev: int, raw: str) -> date:
        try:
            d = date.fromisoformat(raw)
        except (TypeError, ValueError):
            raise TodoError(422, "INVALID_DATE", "마감일 형식이 올바르지 않아요.", field="due_date") from None
        brief = s.execute(
            select(project_briefs.c.start_date, project_briefs.c.end_date)
            .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                  & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
            .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == rev)
        ).one()
        first = ai.first_day(brief.start_date, today_kst(self._clock()))
        if not (first <= d <= brief.end_date):
            raise TodoError(422, "OUTSIDE_PERIOD", "마감일은 오늘부터 프로젝트 기한 사이에서 골라 주세요.", field="due_date")
        return d

    # ---- natural-language revision -----------------------------------------------------------------------------------

    def revise(self, user_id: str, group_id: str, action_id: str, message: str) -> tuple[str, int | None, int | None]:
        _action(action_id)
        message = (message or "").strip()
        if not (1 <= len(message) <= 300):
            raise TodoError(422, "INVALID", "요청은 1~300자로 적어 주세요.", field="message")
        if not interview_ai.meaningful(message):  # nothing stored, nothing called
            raise TodoError(422, "MEANINGLESS", "요청을 알아보기 어려워요. 어떻게 바꿀지 조금 더 자세히 적어 주세요.", field="message")
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            rev = self._current(s, group_id, user_id).revision
            replay = s.execute(select(todo_ai_steps.c.seq).where(
                todo_ai_steps.c.group_id == group_id, todo_ai_steps.c.user_id == user_id, todo_ai_steps.c.action_id == action_id
            )).first()
            if replay is not None:
                return "replay", None, None
            draft = self._draft(s, group_id, user_id, rev, lock=True)
            if draft is None or draft.draft_revision == 0:
                raise TodoError(409, "NO_DRAFT", "투두 초안이 아직 없어요.")
            if draft.mode == CONFIRMED:
                raise TodoError(409, "CONFIRMED", "확정된 투두예요. 고치려면 먼저 확정을 풀어 주세요.")
            if self._running(s, group_id, user_id, rev) is not None:
                raise TodoError(409, "BUSY", "AI가 투두를 처리하고 있어요. 잠시 후 다시 시도해주세요.")
            seq = self._next_seq(s, group_id, user_id, rev)
            now = self._clock()
            s.execute(insert(todo_ai_steps).values(group_id=group_id, user_id=user_id, source_assignment_revision=rev, seq=seq,
                                                   kind=REVISE, action_id=action_id, user_message=message, state=PENDING,
                                                   base_draft_revision=draft.draft_revision, created_at=now, updated_at=now))
        return "started", rev, seq

    def _next_seq(self, s, group_id, user_id, rev) -> int:
        seq = s.execute(select(func.coalesce(func.max(todo_ai_steps.c.seq), 0)).where(self._key(group_id, user_id, rev)(todo_ai_steps))).scalar_one() + 1
        if seq > MAX_STEPS:
            raise TodoError(409, "TOO_MANY", "수정 요청이 너무 많아요. 오른쪽에서 직접 고쳐 주세요.")
        return seq

    # ---- confirm / unlock -------------------------------------------------------------------------------------------

    def confirm(self, user_id: str, group_id: str, action_id: str, draft_revision: int) -> tuple[str, int | None, int | None]:
        """("confirmed" | "noop" | "replay", None, None) or ("started", rev, seq) when hidden criteria must be made first."""
        _action(action_id)
        with self._sf.begin() as s:
            rev, draft = self._editable(s, group_id, user_id, action_id, "CONFIRM", None)
            if draft is None:
                return "replay", None, None
            if draft.mode == CONFIRMED:
                return "noop", None, None  # a second click on an already confirmed draft
            if draft_revision != draft.draft_revision:
                raise TodoError(409, "CONFLICT", "다른 곳에서 투두가 바뀌었어요. 최신 내용으로 다시 불러왔어요.")
            items = self._items(s, group_id, user_id, rev)
            if not items:
                raise TodoError(422, "EMPTY", "투두를 하나 이상 만들어 주세요.")
            if any(i.due_date is None for i in items):
                raise TodoError(422, "MISSING_DUE", "마감일이 비어 있는 투두가 있어요.")
            first, end = self._period(s, group_id, rev)
            if any(not (first <= i.due_date <= end) for i in items):
                raise TodoError(422, "OUTSIDE_PERIOD", "프로젝트 기간을 벗어난 마감일이 있어요. 고친 뒤 확정해 주세요.")
            if any(i.criteria_for_title is None or i.criteria_for_title != i.title for i in items):
                seq = self._next_seq(s, group_id, user_id, rev)
                now = self._clock()
                s.execute(insert(todo_ai_steps).values(group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                                                       seq=seq, kind=CRITERIA, action_id=action_id, state=PENDING,
                                                       base_draft_revision=draft.draft_revision, created_at=now, updated_at=now))
                return "started", rev, seq
            self._confirm_now(s, group_id, user_id, rev, draft, items, action_id)
        return "confirmed", None, None

    def _period(self, s, group_id, rev):
        brief = s.execute(
            select(project_briefs.c.start_date, project_briefs.c.end_date)
            .join(role_assignments, (role_assignments.c.group_id == project_briefs.c.group_id)
                  & (role_assignments.c.source_candidate_revision == project_briefs.c.revision))
            .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == rev)
        ).one()
        return ai.first_day(brief.start_date, today_kst(self._clock())), brief.end_date

    def _confirm_now(self, s, group_id, user_id, rev, draft, items, action_id) -> int:
        k = self._key(group_id, user_id, rev)
        number = s.execute(select(func.coalesce(func.max(todo_confirmations.c.revision), 0)).where(k(todo_confirmations))).scalar_one() + 1
        now = self._clock()
        s.execute(insert(todo_confirmations).values(group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                                                    revision=number, draft_revision=draft.draft_revision,
                                                    member_state_revision=draft.member_state_revision, action_id=action_id,
                                                    created_at=now))
        for pos, it in enumerate(sorted(items, key=lambda i: (i.due_date, i.position)), 1):
            s.execute(insert(todo_confirmed_items).values(
                group_id=group_id, user_id=user_id, source_assignment_revision=rev, confirmation_revision=number,
                task_id=it.task_id, position=pos, title=it.title, due_date=it.due_date, part_id=it.part_id,
                deliverable=it.deliverable, completion_criteria=it.completion_criteria, evidence_types=it.evidence_types,
                estimated_minutes=it.estimated_minutes,
            ))
        s.execute(update(todo_drafts).where(k(todo_drafts)).values(mode=CONFIRMED, confirmed_revision=number, updated_at=now))
        log.info("todo_event group_id=%s user_id=%s revision=%d state=CONFIRMED confirmation=%d todos=%d",
                 group_id, user_id, rev, number, len(items))
        return number

    def unlock(self, user_id: str, group_id: str, action_id: str) -> str:
        """확정 풀기: back to editing. The confirmed revision stays and the group keeps seeing it until a re-confirm."""
        _action(action_id)
        with self._sf.begin() as s:
            rev, draft = self._editable(s, group_id, user_id, action_id, "UNLOCK", None)
            if draft is None:
                return "replay"
            if draft.mode != CONFIRMED:
                return "noop"
            s.execute(update(todo_drafts).where(self._key(group_id, user_id, rev)(todo_drafts))
                      .values(mode=EDITING, updated_at=self._clock()))
        return "done"

    # ---- background AI step -----------------------------------------------------------------------------------------

    def _cas(self, group_id, user_id, rev, seq, attempt, from_state, **values) -> bool:
        k = self._key(group_id, user_id, rev)
        stmt = (update(todo_ai_steps)
                .where(k(todo_ai_steps), todo_ai_steps.c.seq == seq, todo_ai_steps.c.attempt == attempt, todo_ai_steps.c.state == from_state)
                .values(updated_at=self._clock(), **values))
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount == 1

    def run(self, group_id: str, user_id: str, rev: int, seq: int) -> None:
        """PENDING -> RUNNING -> SUCCEEDED | FAILED for one step. Safe to call twice."""
        now = self._clock()
        with self._sf() as s:
            k = self._key(group_id, user_id, rev)
            step = s.execute(select(todo_ai_steps).where(k(todo_ai_steps), todo_ai_steps.c.seq == seq)).one_or_none()
            if step is None or step.state != PENDING:
                return
            draft = s.execute(select(todo_drafts).where(k(todo_drafts))).one()
            try:
                payload, limits, today = self._inputs(s, group_id, user_id, rev, now)
            except TodoError as exc:
                payload = None
                input_error = exc.code
            items = self._items(s, group_id, user_id, rev)
        attempt = step.attempt
        if not self._cas(group_id, user_id, rev, seq, attempt, PENDING, state=RUNNING, base_draft_revision=draft.draft_revision):
            return
        if payload is None or draft.stale_at is not None:
            self._fail(group_id, user_id, rev, seq, attempt, step.kind, input_error if payload is None else "STALE")
            return
        refs = {f"t{i}": it for i, it in enumerate(items, 1)}
        if step.kind == DRAFT:
            policy, schema = ai.DRAFT_POLICY, ai.DraftOutput
            body = payload
        elif step.kind == REVISE:
            policy, schema = ai.REVISE_POLICY, ai.ReviseOutput
            body = {**payload, "todos": [self._ref_view(r, it) for r, it in refs.items()], "request": step.user_message}
        else:
            policy, schema = ai.CRITERIA_POLICY, ai.CriteriaOutput
            targets = [r for r, it in refs.items() if it.criteria_for_title is None or it.criteria_for_title != it.title]
            body = {**payload, "todos": [self._ref_view(r, it) for r, it in refs.items()], "targets": targets}
        started = time.perf_counter()
        try:
            done = self._call(policy, body, schema)
        except _StepFailed as exc:
            self._fail(group_id, user_id, rev, seq, attempt, step.kind, exc.code)
            return
        usage = dict(model=(done.model or "")[:64], request_id=(getattr(done, "provider_request_id", None) or None),
                     latency_ms=done.latency_ms or int((time.perf_counter() - started) * 1000),
                     prompt_tokens=done.usage.prompt_tokens, completion_tokens=done.usage.completion_tokens,
                     total_tokens=done.usage.total_tokens)
        try:
            if step.kind == DRAFT:
                checked = ai.check_draft(done.parsed_output, payload, limits, today)
            elif step.kind == CRITERIA:
                tlist = [{"ref": r, "title": refs[r].title, "part_id": refs[r].part_id} for r in targets]
                checked = ai.check_criteria(done.parsed_output, tlist, payload)
            else:
                checked = None
        except ai.TodoInvalid as exc:
            self._fail(group_id, user_id, rev, seq, attempt, step.kind, f"INVALID_{exc.code}", usage)
            return
        reply, applied = None, None
        if step.kind == REVISE:
            current = [{"ref": r, "item_no": it.item_no, "title": it.title, "due_date": it.due_date, "part_id": it.part_id,
                        "estimated_minutes": it.estimated_minutes} for r, it in refs.items()]
            try:
                applied, reply = ai.check_revision(done.parsed_output, payload, current, limits)
            except ai.TodoInvalid as exc:
                out = done.parsed_output
                r = (out.reply or "").strip()
                reply = exc.message or (r if exc.code == "NOT_UNDERSTOOD" and interview_ai._usable_text(r, 60) else ai.MSG_NOT_UNDERSTOOD)
                applied = None
                usage["error_code"] = f"NOT_APPLIED_{exc.code}"[:64]
        try:
            self._finish(group_id, user_id, rev, seq, attempt, step.kind, draft.draft_revision, checked, applied, reply, usage)
        except Exception as exc:  # the save rolled back as a whole: nothing half-written, the step is a retryable failure
            log.error("todo_event group_id=%s user_id=%s revision=%d seq=%d state=SAVE_FAILED error_type=%s",
                      group_id, user_id, rev, seq, type(exc).__name__)
            usage.pop("error_code", None)
            self._fail(group_id, user_id, rev, seq, attempt, step.kind, "SAVE_FAILED", usage)

    @staticmethod
    def _ref_view(ref: str, it) -> dict:
        return {"ref": ref, "title": it.title, "due_date": it.due_date.isoformat() if it.due_date else None, "part": it.part_id}

    def _finish(self, group_id, user_id, rev, seq, attempt, kind, base, checked, applied, reply, usage) -> None:
        now = self._clock()
        k = self._key(group_id, user_id, rev)
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            draft = s.execute(select(todo_drafts).where(k(todo_drafts)).with_for_update()).one()
            step = s.execute(select(todo_ai_steps.c.state, todo_ai_steps.c.attempt, todo_ai_steps.c.action_id)
                             .where(k(todo_ai_steps), todo_ai_steps.c.seq == seq)).one()
            if step.state != RUNNING or step.attempt != attempt:
                return
            if draft.stale_at is not None:
                s.execute(update(todo_ai_steps).where(k(todo_ai_steps), todo_ai_steps.c.seq == seq)
                          .values(**{**usage, "state": FAILED, "error_code": "STALE", "error_message": MSG_CONFLICT, "updated_at": now}))
                return
            result_rev = None
            if draft.draft_revision != base:  # cannot happen while edits are refused during a step; kept as a guard
                reply = MSG_CONFLICT
                usage = {**usage, "error_code": "CONFLICT"}
            elif kind == DRAFT:
                self._write_draft(s, group_id, user_id, rev, checked, now)
                result_rev = 1
                reply = f"{self._role_name(s, group_id, user_id, rev)} 역할에 맞춰 투두 초안 {len(checked)}개를 만들었어요. 오른쪽에서 직접 고치거나, 여기에 '마감을 더 여유 있게 해줘'처럼 말해 주세요."
            elif kind == REVISE and applied is not None:
                result_rev = self._apply_revision(s, group_id, user_id, rev, draft, applied, now)
            elif kind == CRITERIA:
                for ref_no, it in enumerate(self._items(s, group_id, user_id, rev), 1):
                    c = checked.get(f"t{ref_no}")
                    if c is not None:
                        s.execute(update(todo_items).where(todo_items.c.task_id == it.task_id)
                                  .values(criteria_for_title=it.title, updated_at=now, **c))
                fresh = self._items(s, group_id, user_id, rev)
                self._confirm_now(s, group_id, user_id, rev, draft, fresh, step.action_id)
                reply = MSG_CONFIRMED
            s.execute(update(todo_ai_steps).where(k(todo_ai_steps), todo_ai_steps.c.seq == seq)
                      .values(state=SUCCEEDED, reply=reply, result_draft_revision=result_rev, error_message=None,
                              updated_at=now, **{"error_code": None, **usage}))
        log.info("todo_event group_id=%s user_id=%s revision=%d seq=%d attempt=%d kind=%s state=SUCCEEDED latency_ms=%s total_tokens=%s%s",
                 group_id, user_id, rev, seq, attempt, kind, usage.get("latency_ms"), usage.get("total_tokens"),
                 f" outcome={usage['error_code']}" if usage.get("error_code") else "")

    @staticmethod
    def _role_name(s, group_id, user_id, rev) -> str:
        return AssignmentService.mine(s, group_id, user_id, rev).role_name

    def _write_draft(self, s, group_id, user_id, rev, items: list[dict], now) -> None:
        for no, it in enumerate(items, 1):
            s.execute(insert(todo_items).values(
                task_id=str(uuid.uuid4()), group_id=group_id, user_id=user_id, source_assignment_revision=rev, item_no=no,
                position=no, title=it["title"], due_date=it["due_date"], origin="AI", part_id=it["part_id"], phase=it["phase"],
                milestone_id=it["milestone_id"], deliverable=it["deliverable"], completion_criteria=it["completion_criteria"],
                evidence_types=it["evidence_types"], estimated_minutes=it["estimated_minutes"], criteria_for_title=it["title"],
                created_at=now, updated_at=now,
            ))
        s.execute(update(todo_drafts).where(self._key(group_id, user_id, rev)(todo_drafts))
                  .values(draft_revision=1, next_item_no=len(items) + 1, updated_at=now))

    def _apply_revision(self, s, group_id, user_id, rev, draft, applied: list[dict], now) -> int:
        current = {it.item_no: it for it in self._items(s, group_id, user_id, rev)}
        keep = {a["item_no"] for a in applied if a.get("op") != "add"}
        for no, it in current.items():
            if no not in keep:
                s.execute(update(todo_items).where(todo_items.c.task_id == it.task_id).values(deleted_at=now, updated_at=now))
        next_no = draft.next_item_no
        pos = max((it.position for it in current.values()), default=0)
        for a in applied:
            if a.get("op") == "add":
                pos += 1
                s.execute(insert(todo_items).values(
                    task_id=str(uuid.uuid4()), group_id=group_id, user_id=user_id, source_assignment_revision=rev,
                    item_no=next_no, position=pos, title=a["title"], due_date=a["due_date"], origin="AI",
                    part_id=a.get("part_id"), created_at=now, updated_at=now,
                ))
                next_no += 1
            elif a.get("op") == "update":
                it = current[a["item_no"]]
                s.execute(update(todo_items).where(todo_items.c.task_id == it.task_id)
                          .values(title=a["title"], due_date=a["due_date"], updated_at=now))
        new_rev = draft.draft_revision + 1
        s.execute(update(todo_drafts).where(self._key(group_id, user_id, rev)(todo_drafts))
                  .values(draft_revision=new_rev, next_item_no=next_no, updated_at=now))
        return new_rev

    def _call(self, policy: str, body: dict, schema):
        from wemeet_ai.m0.client import AIError

        messages = [{"role": "system", "content": policy}, {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]
        try:
            return self._client_factory().complete_json(messages, schema, {"reasoning_effort": "low"})
        except AIError as exc:
            raise _StepFailed(exc.code) from None
        except Exception as exc:  # the type name only: the message may hold private content
            log.error("todo_event state=ERROR error_type=%s", type(exc).__name__)
            raise _StepFailed("HANDLER_ERROR") from None

    def _fail(self, group_id, user_id, rev, seq, attempt, kind, code, usage=None) -> None:
        message = MSG_SLOW if code in SLOW_CODES else {
            DRAFT: MSG_DRAFT_INVALID if code.startswith("INVALID_") else MSG_DRAFT_FAILED,
            REVISE: MSG_REVISE_FAILED,
            CRITERIA: MSG_CRITERIA_FAILED,
        }[kind]
        if code in ("COVERAGE_NOT_READY", "INTERVIEW_NOT_DONE", "STALE"):
            message = MSG_CONFLICT if kind != DRAFT else MSG_DRAFT_FAILED
        values = dict(state=FAILED, error_code=code[:64], error_message=message, **(usage or {}))
        self._cas(group_id, user_id, rev, seq, attempt, RUNNING, **values)
        log.info("todo_event group_id=%s user_id=%s revision=%d seq=%d attempt=%d kind=%s state=FAILED error=%s",
                 group_id, user_id, rev, seq, attempt, kind, code)

    # ---- explicit retry ---------------------------------------------------------------------------------------------

    def retry(self, user_id: str, group_id: str, seq: int, expected_attempt: int) -> tuple[str, int | None]:
        """Only the newest step, only when FAILED. A failed REVISE runs again against the current draft."""
        with self._sf.begin() as s:
            self._lock(s, group_id, user_id)
            rev = self._current(s, group_id, user_id).revision
            draft = self._draft(s, group_id, user_id, rev)
            if draft is None:
                return "refused", None
            steps = self._steps(s, group_id, user_id, rev)
            last = steps[-1] if steps else None
            if last is None or last.seq != seq:
                return "refused", None
            if last.attempt == expected_attempt + 1 and last.state != FAILED:
                return "replay", None
            if last.state != FAILED or last.attempt != expected_attempt:
                return "refused", None
            if last.kind != DRAFT and draft.mode == CONFIRMED:
                return "refused", None
            s.execute(update(todo_ai_steps).where(self._key(group_id, user_id, rev)(todo_ai_steps), todo_ai_steps.c.seq == seq)
                      .values(state=PENDING, attempt=last.attempt + 1, error_code=None, error_message=None, model=None,
                              request_id=None, latency_ms=None, prompt_tokens=None, completion_tokens=None,
                              total_tokens=None, updated_at=self._clock()))
        return "started", rev

    def fail_interrupted(self) -> int:
        stmt = (update(todo_ai_steps).where(todo_ai_steps.c.state.in_((PENDING, RUNNING)))
                .values(state=FAILED, error_code="SERVER_RESTARTED", error_message=MSG_RESTART, updated_at=self._clock()))
        with self._sf.begin() as s:
            return s.execute(stmt).rowcount

    # ---- group read: role name, todo title, due date ------------------------------------------------------------------

    def group_view(self, user_id: str, group_id: str) -> dict:
        """For any member of the group's ACTIVE cohort: each member's newest confirmed todos, as role name + (title, due
        date) only. No name, user id, slot, task id, revision, rank or hidden criterion.

        Shown: the newest confirmation of the CURRENT assignment (an earlier one stays until a re-confirmation, B7), or,
        after a ProjectBrief change (B8), the newest confirmation of the BRIEF_CHANGED revision, read-only, until the
        member confirms again. A confirmation of a revision replaced by a reassignment is STALE and not shown (B4)."""
        with self._sf() as s:
            g = RankingService._cohort_group(s, group_id, user_id)
            shown = self.shown_confirmations(s, group_id)
            # A member who left (D-D1 D6): their todos are no longer part of the group screen.
            from ..groups.service import active_cohort_query

            active = {r[0] for r in s.execute(active_cohort_query(group_id, g.cohort_revision))}
            shown = {uid: v for uid, v in shown.items() if uid in active}
            out = []
            for uid, (rev, conf) in shown.items():
                m = s.execute(
                    select(role_assignment_members.c.slot_id, role_assignment_members.c.role_name).where(
                        role_assignment_members.c.group_id == group_id, role_assignment_members.c.assignment_revision == rev,
                        role_assignment_members.c.user_id == uid)
                ).one()
                rows = s.execute(
                    select(todo_confirmed_items.c.title, todo_confirmed_items.c.due_date)
                    .where(todo_confirmed_items.c.group_id == group_id, todo_confirmed_items.c.user_id == uid,
                           todo_confirmed_items.c.source_assignment_revision == rev,
                           todo_confirmed_items.c.confirmation_revision == conf)
                    .order_by(todo_confirmed_items.c.position)
                ).all()
                out.append((-rev, int(m.slot_id[1:]), {"role_name": m.role_name,
                            "todos": [{"title": r.title, "due_date": r.due_date.isoformat()} for r in rows]}))
        return {"assignments": [x[2] for x in sorted(out, key=lambda x: (x[0], x[1]))]}

    @staticmethod
    def shown_confirmations(s: Session, group_id: str) -> dict[str, tuple[int, int]]:
        """user_id -> (assignment revision, confirmation revision) of the confirmation the group sees for that member.

        The member's newest confirmation, when it belongs to the CURRENT assignment and is not stale, or, after a
        ProjectBrief change (B8), belongs to the BRIEF_CHANGED revision (read-only until the member confirms again).
        A confirmation of a revision replaced by a reassignment is not shown (B4). Stage 5 execution uses the same rule:
        only these items can be completed or submitted against."""
        from .reassign import BRIEF_CHANGED

        confs = s.execute(
            select(todo_confirmations.c.user_id, todo_confirmations.c.source_assignment_revision,
                   todo_confirmations.c.revision, todo_confirmations.c.stale_at,
                   role_assignments.c.state, role_assignments.c.stale_reason)
            .join(role_assignments, (role_assignments.c.group_id == todo_confirmations.c.group_id)
                  & (role_assignments.c.revision == todo_confirmations.c.source_assignment_revision))
            .where(todo_confirmations.c.group_id == group_id)
            .order_by(todo_confirmations.c.user_id, todo_confirmations.c.source_assignment_revision.desc(),
                      todo_confirmations.c.revision.desc())
        ).all()
        shown: dict[str, tuple[int, int]] = {}
        seen: set[str] = set()
        for c in confs:
            if c.user_id in seen:
                continue
            seen.add(c.user_id)  # only the member's newest confirmation counts
            if (c.state == "CURRENT" and c.stale_at is None) or c.stale_reason == BRIEF_CHANGED:
                shown[c.user_id] = (c.source_assignment_revision, c.revision)
        return shown
