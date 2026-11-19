"""Stage 5 execution: progress, todos, team activity, completion records, attachments and the per-todo M2 review.

Access: every call is for a **current active member** of the group (in the activation cohort and still in
`group_members`); everyone else, a missing group included, gets 404 (D-D1 D5). The user id always comes from the
session.

What the group sees (team artifact): member names (already on the page), each member's confirmed role name and todo
titles/due dates (the confirmation `TodoService.shown_confirmations` picks), which of them are done, and the
completion records (title, memo, attachments) the members chose to post. Never shown to anyone: `deliverable`,
`completion_criteria`, evidence types, MemberState, conversations, rankings. The M2 result line is returned only to the
record's own submitter (D-D1 D3).

A record that names a todo is bound to exactly one confirmed item of its submitter: `task_id` + the assignment and
confirmation revision that were shown when it was made (DB foreign key to `todo_confirmed_items`). A request made
against a confirmation that is no longer the shown one is a 409 conflict, never silently re-bound.

The checkbox meaning stays the original's (D-D1 D2): `첨부하면 이 할 일을 완료로 표시` completes at once; the M2 result
never completes or un-completes anything. `되돌리기` of a completion with files deletes the record, its files and its
review permanently, only after the caller confirmed (D-D1 D5).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import ACTIVE, GroupService, is_group_id, today_kst
from ..models import (
    cohort_members,
    execution_events,
    execution_files,
    execution_records,
    execution_reviews,
    group_members,
    groups,
    project_briefs,
    role_assignment_members,
    todo_confirmed_items,
)
from ..roles.todos import TodoService
from ..timeutil import DISPLAY_TZ, utcnow
from . import files as F
from . import review_ai as R

log = logging.getLogger("moim.execution")

ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
UUID_RE = F.UUID_RE
PENDING, RUNNING, SUCCEEDED, FAILED, SKIPPED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"
FEED_LIMIT = 300
LEFT_KEY = "left"  # feed rows of members who left the group (D-D1 D6); the browser shows "나간 팀원"
MSG_NOT_FOUND = "찾을 수 없어요."
MSG_INVALID = "요청 형식이 올바르지 않아요."
MSG_CONFLICT = "그 사이 할 일이 바뀌었어요. 새로고침한 뒤 다시 해 주세요."
MSG_DONE = "이미 완료로 표시한 할 일이에요."
MSG_EMPTY = "파일이나 제목·설명 중 하나는 있어야 해요."
MSG_ETC_FILE = "파일은 할 일을 골라야 첨부할 수 있어요."
MSG_FILES = "한 번에 파일은 5개까지 첨부할 수 있어요."
MSG_GROUP_FULL = "그룹의 첨부 용량(500MB)이 가득 차서 첨부하지 않았어요."
MSG_CONFIRM = "첨부 자료도 삭제되며 복구할 수 없어요. 되돌릴까요?"
MSG_NOT_RETRYABLE = "지금은 다시 시도할 수 없어요. 새로고침해서 상태를 확인해 주세요."


class ExecutionError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def not_found() -> ExecutionError:
    return ExecutionError(404, "NOT_FOUND", MSG_NOT_FOUND)


def _action(action_id: str) -> str:
    if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
        raise ExecutionError(422, "INVALID", MSG_INVALID)
    return action_id


def time_label(at: datetime, today: date) -> str:
    local = at.astimezone(DISPLAY_TZ)
    d = local.date()
    if d == today:
        return f"오늘 {local:%H:%M}"
    if d == today - timedelta(days=1):
        return "어제"
    return f"{local.month:02d}/{local.day:02d}"


class ExecutionService:
    def __init__(self, session_factory: sessionmaker[Session], client_factory: Callable[[], object], storage: F.Storage,
                 review_enabled: bool = True, clock=utcnow):
        self._sf = session_factory
        self._client_factory = client_factory
        self.storage = storage
        self.review_enabled = review_enabled
        self._clock = clock

    # ---- access -------------------------------------------------------------------------------------------------------

    @staticmethod
    def _member(s: Session, group_id: str, user_id: str):
        """The group row for a current active member; 404 for everyone else (D-D1 D5)."""
        if not is_group_id(group_id or ""):
            raise not_found()
        g = s.execute(select(groups).where(groups.c.id == group_id)).one_or_none()
        if g is None or g.status != ACTIVE:
            raise not_found()
        ok = s.execute(
            select(cohort_members.c.user_id)
            .join(group_members, (group_members.c.group_id == cohort_members.c.group_id)
                  & (group_members.c.user_id == cohort_members.c.user_id))
            .where(cohort_members.c.group_id == group_id, cohort_members.c.cohort_revision == g.cohort_revision,
                   cohort_members.c.user_id == user_id)
        ).first()
        if ok is None:
            raise not_found()
        return g

    @staticmethod
    def _lock(s: Session, group_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"execution:{group_id}"})

    @staticmethod
    def _event(s: Session, group_id: str, user_id: str, kind: str, record=None, detail: str | None = None,
               file_count: int = 0) -> None:
        s.execute(insert(execution_events).values(
            group_id=group_id, user_id=user_id, kind=kind, detail=detail, file_count=file_count,
            record_id=getattr(record, "id", None) if record is not None and not isinstance(record, str) else record,
            task_id=getattr(record, "task_id", None) if record is not None and not isinstance(record, str) else None,
            source_assignment_revision=getattr(record, "source_assignment_revision", None) if record is not None and not isinstance(record, str) else None,
            confirmation_revision=getattr(record, "confirmation_revision", None) if record is not None and not isinstance(record, str) else None,
        ))

    # ---- read ---------------------------------------------------------------------------------------------------------

    def view(self, user_id: str, group_id: str) -> dict:
        now = self._clock()
        today = today_kst(now)
        with self._sf() as s:
            g = self._member(s, group_id, user_id)
            brief = s.execute(select(project_briefs.c.start_date, project_briefs.c.end_date).where(
                project_briefs.c.group_id == group_id, project_briefs.c.revision == g.brief_revision)).one()
            members = GroupService._cohort_member_list(s, group_id, g.cohort_revision)
            keys = {m.user_id: f"m{i + 1}" for i, m in enumerate(members)}
            shown = TodoService.shown_confirmations(s, group_id)
            done = {r.task_id: r for r in s.execute(
                select(execution_records.c.task_id, execution_records.c.id, execution_records.c.created_at,
                       execution_records.c.file_count, execution_records.c.user_id)
                .where(execution_records.c.group_id == group_id, execution_records.c.marks_done)).all()}
            out_members, member_todos, my_todos = [], {}, []
            for m in members:
                key = keys[m.user_id]
                role_name, items = None, []
                if m.user_id in shown:
                    rev, conf = shown[m.user_id]
                    role_name = s.execute(select(role_assignment_members.c.role_name).where(
                        role_assignment_members.c.group_id == group_id, role_assignment_members.c.assignment_revision == rev,
                        role_assignment_members.c.user_id == m.user_id)).scalar_one()
                    items = s.execute(
                        select(todo_confirmed_items.c.task_id, todo_confirmed_items.c.title, todo_confirmed_items.c.due_date)
                        .where(todo_confirmed_items.c.group_id == group_id, todo_confirmed_items.c.user_id == m.user_id,
                               todo_confirmed_items.c.source_assignment_revision == rev,
                               todo_confirmed_items.c.confirmation_revision == conf)
                        .order_by(todo_confirmed_items.c.due_date, todo_confirmed_items.c.position)).all()
                n_done = 0
                rows = []
                for it in items:
                    d = done.get(it.task_id)
                    on = d.created_at.astimezone(DISPLAY_TZ).date().isoformat() if d is not None else None
                    n_done += d is not None
                    rows.append({"title": it.title, "due_date": it.due_date.isoformat(), "done_on": on})
                    if m.user_id == user_id:
                        my_todos.append({
                            "task_id": it.task_id, "confirmation_revision": shown[m.user_id][1], "title": it.title,
                            "due_date": it.due_date.isoformat(), "done_on": on,
                            "record_id": d.id if d is not None else None, "has_files": bool(d is not None and d.file_count),
                        })
                member_todos[key] = rows
                out_members.append({"key": key, "name": m.name, "shape": m.shape, "is_me": m.user_id == user_id,
                                    "leader": m.role == "LEADER", "role_name": role_name, "total": len(items), "done": n_done})

            recs = s.execute(
                select(execution_records, todo_confirmed_items.c.title.label("task_title"))
                .outerjoin(todo_confirmed_items,
                           (todo_confirmed_items.c.group_id == execution_records.c.group_id)
                           & (todo_confirmed_items.c.user_id == execution_records.c.user_id)
                           & (todo_confirmed_items.c.source_assignment_revision == execution_records.c.source_assignment_revision)
                           & (todo_confirmed_items.c.confirmation_revision == execution_records.c.confirmation_revision)
                           & (todo_confirmed_items.c.task_id == execution_records.c.task_id))
                .where(execution_records.c.group_id == group_id)
                .order_by(execution_records.c.created_at.desc(), execution_records.c.id)
                .limit(FEED_LIMIT)).all()
            ids = [r.id for r in recs]
            files_by: dict[str, list] = {}
            if ids:
                for f in s.execute(select(execution_files.c.id, execution_files.c.record_id, execution_files.c.original_name,
                                          execution_files.c.kind)
                                   .where(execution_files.c.record_id.in_(ids)).order_by(execution_files.c.position)).all():
                    files_by.setdefault(f.record_id, []).append({"id": f.id, "name": f.original_name, "kind": f.kind})
            mine = [r.id for r in recs if r.user_id == user_id]
            reviews = {}
            if mine:
                reviews = {v.record_id: v for v in s.execute(
                    select(execution_reviews).where(execution_reviews.c.record_id.in_(mine))).all()}
            activity: dict[str, dict[str, int]] = {k: {} for k in keys.values()}
            for r in s.execute(select(execution_records.c.user_id, execution_records.c.created_at)
                               .where(execution_records.c.group_id == group_id)).all():
                if r.user_id in keys:
                    day = r.created_at.astimezone(DISPLAY_TZ).date().isoformat()
                    bucket = activity[keys[r.user_id]]
                    bucket[day] = bucket.get(day, 0) + 1
            feed = []
            for r in recs:
                # A member who left keeps their records on the team feed as "나간 팀원" (D-D1 D6): no name, no key that
                # could tell two departed members apart. A deleted account's records are gone (D7).
                own = r.user_id == user_id
                item = {
                    "id": r.id if own else None,
                    "who": keys.get(r.user_id, LEFT_KEY),
                    "title": r.title or r.task_title or "기타 자료",
                    "memo": r.memo or "",
                    "files": files_by.get(r.id, []),
                    "at": r.created_at.isoformat(),
                    "label": time_label(r.created_at, today),
                    "done": bool(r.marks_done),
                }
                if own and r.id in reviews:
                    item["review"] = self._review_view(reviews[r.id])
                feed.append(item)
        return {
            "today": today.isoformat(), "start_date": brief.start_date.isoformat(), "end_date": brief.end_date.isoformat(),
            "members": out_members, "member_todos": member_todos, "my_todos": my_todos, "feed": feed,
            "activity": activity, "review_enabled": self.review_enabled,
        }

    @staticmethod
    def _review_view(v) -> dict:
        return {
            "state": v.state, "attempt": v.attempt,
            "line": R.line(v.state, v.decision, v.guidance, v.skip_reason, v.error_code, v.reviewed_files,
                           v.excluded_files, v.embedded_images_skipped),
            "can_retry": v.state == FAILED,
        }

    def review_status(self, user_id: str, group_id: str, record_id: str) -> dict:
        with self._sf() as s:
            self._member(s, group_id, user_id)
            v = self._own_review(s, group_id, user_id, record_id)
            return self._review_view(v)

    @staticmethod
    def _own_review(s: Session, group_id: str, user_id: str, record_id: str, lock: bool = False):
        if not UUID_RE.fullmatch(record_id or ""):
            raise not_found()
        q = (select(execution_reviews).join(execution_records, execution_records.c.id == execution_reviews.c.record_id)
             .where(execution_reviews.c.record_id == record_id, execution_records.c.group_id == group_id,
                    execution_records.c.user_id == user_id))
        v = s.execute(q.with_for_update(of=execution_reviews) if lock else q).one_or_none()
        if v is None:
            raise not_found()
        return v

    # ---- write --------------------------------------------------------------------------------------------------------

    def submit(self, user_id: str, group_id: str, action_id: str, task_id: str | None, confirmation_revision: int | None,
               title: str | None, memo: str | None, marks_done: bool, checked: list[F.CheckedFile]) -> tuple[dict, bool]:
        """One completion record. Returns (record view for the caller, created). A replayed action id returns the record
        it made the first time and writes nothing."""
        action_id = _action(action_id)
        title = (title or "").strip() or None
        memo = (memo or "").strip() or None
        if title is not None and len(title) > F.MAX_TITLE or memo is not None and len(memo) > F.MAX_MEMO:
            raise ExecutionError(422, "INVALID", MSG_INVALID)
        if len(checked) > F.MAX_FILES:
            raise ExecutionError(422, "TOO_MANY_FILES", MSG_FILES)
        if task_id is None:
            if checked:
                raise ExecutionError(422, "ETC_NO_FILE", MSG_ETC_FILE)
            if marks_done or (title is None and memo is None):
                raise ExecutionError(422, "EMPTY", MSG_EMPTY)
        elif not UUID_RE.fullmatch(task_id) or not isinstance(confirmation_revision, int):
            raise ExecutionError(422, "INVALID", MSG_INVALID)
        elif not marks_done and not checked and title is None and memo is None:
            raise ExecutionError(422, "EMPTY", MSG_EMPTY)

        written: list[str] = []
        now = self._clock()
        try:
            with self._sf() as s, s.begin():
                self._member(s, group_id, user_id)
                self._lock(s, group_id)
                old = s.execute(select(execution_records).where(
                    execution_records.c.group_id == group_id, execution_records.c.user_id == user_id,
                    execution_records.c.action_id == action_id)).one_or_none()
                if old is not None:
                    return self._mine(s, old), False
                rev = None
                if task_id is not None:
                    shown = TodoService.shown_confirmations(s, group_id).get(user_id)
                    if shown is None or shown[1] != confirmation_revision:
                        raise ExecutionError(409, "REVISION_CONFLICT", MSG_CONFLICT)
                    rev = shown[0]
                    item = s.execute(select(todo_confirmed_items.c.task_id).where(
                        todo_confirmed_items.c.group_id == group_id, todo_confirmed_items.c.user_id == user_id,
                        todo_confirmed_items.c.source_assignment_revision == rev,
                        todo_confirmed_items.c.confirmation_revision == confirmation_revision,
                        todo_confirmed_items.c.task_id == task_id)).first()
                    if item is None:  # someone else's todo, an old one, or a made-up id: indistinguishable
                        raise ExecutionError(409, "REVISION_CONFLICT", MSG_CONFLICT)
                    if marks_done and s.execute(select(execution_records.c.id).where(
                            execution_records.c.task_id == task_id, execution_records.c.marks_done)).first() is not None:
                        raise ExecutionError(409, "ALREADY_DONE", MSG_DONE)
                if checked:
                    used = s.execute(select(func.coalesce(func.sum(execution_files.c.size_bytes), 0)).where(
                        execution_files.c.group_id == group_id)).scalar_one()
                    if used + sum(len(c.data) for c in checked) > F.GROUP_TOTAL_BYTES:
                        raise ExecutionError(413, "GROUP_FULL", MSG_GROUP_FULL)
                rid = str(uuid.uuid4())
                s.execute(insert(execution_records).values(
                    id=rid, group_id=group_id, user_id=user_id, action_id=action_id, task_id=task_id,
                    source_assignment_revision=rev, confirmation_revision=confirmation_revision if task_id else None,
                    title=title, memo=memo, marks_done=bool(marks_done), file_count=len(checked), created_at=now))
                for pos, c in enumerate(checked):
                    fid = str(uuid.uuid4())
                    self.storage.write(group_id, fid, c.data)
                    written.append(fid)
                    s.execute(insert(execution_files).values(
                        id=fid, record_id=rid, group_id=group_id, position=pos, original_name=c.name, kind=c.kind,
                        size_bytes=len(c.data), sha256=c.sha256, review_kind=c.review_kind,
                        embedded_images=c.embedded_images, created_at=now))
                rec = s.execute(select(execution_records).where(execution_records.c.id == rid)).one()
                kind = "NOTE" if task_id is None else "COMPLETE" if marks_done and not checked and not title and not memo else "SUBMIT"
                self._event(s, group_id, user_id, kind, rec, detail="DONE" if marks_done else None, file_count=len(checked))
                if task_id is not None and checked and self.review_enabled:
                    reviewable = [c for c in checked if c.review_kind]
                    if reviewable:
                        s.execute(insert(execution_reviews).values(record_id=rid, state=PENDING, created_at=now))
                        self._event(s, group_id, user_id, "REVIEW_START", rec, file_count=len(reviewable))
                    else:
                        s.execute(insert(execution_reviews).values(
                            record_id=rid, state=SKIPPED, skip_reason="UNSUPPORTED", excluded_files=len(checked),
                            created_at=now, finished_at=now))
                        self._event(s, group_id, user_id, "REVIEW_SKIPPED", rec, detail="UNSUPPORTED")
                view = self._mine(s, rec)
        except Exception:
            for fid in written:
                self.storage.delete(group_id, fid)
            raise
        log.info("execution_event group_id=%s user_id=%s record_id=%s kind=%s files=%d done=%s",
                 group_id, user_id, view["id"], kind, len(checked), bool(marks_done))
        return view, True

    def _mine(self, s: Session, rec) -> dict:
        v = s.execute(select(execution_reviews).where(execution_reviews.c.record_id == rec.id)).one_or_none()
        return {"id": rec.id, "marks_done": bool(rec.marks_done), "file_count": rec.file_count,
                "review": self._review_view(v) if v is not None else None}

    def undo(self, user_id: str, group_id: str, record_id: str, confirm_delete: bool) -> int:
        """Own completion only. With files, only after the caller confirmed; then the record, its files and its review
        are deleted permanently. Returns the number of deleted files."""
        if not UUID_RE.fullmatch(record_id or ""):
            raise not_found()
        with self._sf() as s, s.begin():
            self._member(s, group_id, user_id)
            self._lock(s, group_id)
            rec = s.execute(select(execution_records).where(
                execution_records.c.id == record_id, execution_records.c.group_id == group_id,
                execution_records.c.user_id == user_id, execution_records.c.marks_done)).one_or_none()
            if rec is None:
                raise not_found()
            if rec.file_count and not confirm_delete:
                raise ExecutionError(409, "CONFIRM_REQUIRED", MSG_CONFIRM)
            fids = s.execute(select(execution_files.c.id).where(execution_files.c.record_id == record_id)).scalars().all()
            s.execute(delete(execution_records).where(execution_records.c.id == record_id))  # files, review: cascade
            self._event(s, group_id, user_id, "UNDO", rec, file_count=len(fids))
        for fid in fids:
            self.storage.delete(group_id, fid)
        log.info("execution_event group_id=%s user_id=%s record_id=%s kind=UNDO files=%d", group_id, user_id, record_id, len(fids))
        return len(fids)

    def download(self, user_id: str, group_id: str, file_id: str):
        """(path, display name) for a current active member of the file's own group; 404 for everything else."""
        if not UUID_RE.fullmatch(file_id or ""):
            raise not_found()
        with self._sf() as s:
            self._member(s, group_id, user_id)
            f = s.execute(select(execution_files.c.original_name).where(
                execution_files.c.id == file_id, execution_files.c.group_id == group_id)).one_or_none()
        if f is None:
            raise not_found()
        path = self.storage.path(group_id, file_id)
        if not path.is_file():
            raise not_found()
        return path, f.original_name

    # ---- M2 review ----------------------------------------------------------------------------------------------------

    def retry_review(self, user_id: str, group_id: str, record_id: str, expected_attempt: int) -> dict:
        with self._sf() as s, s.begin():
            self._member(s, group_id, user_id)
            v = self._own_review(s, group_id, user_id, record_id, lock=True)
            if v.state != FAILED or v.attempt != expected_attempt:
                raise ExecutionError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
            s.execute(update(execution_reviews).where(execution_reviews.c.record_id == record_id).values(
                state=PENDING, attempt=v.attempt + 1, error_code=None, decision=None, criteria_results=None,
                guidance=None, model=None, request_id=None, latency_ms=None, prompt_tokens=None,
                completion_tokens=None, total_tokens=None, started_at=None, finished_at=None))
            self._event(s, group_id, user_id, "REVIEW_RETRY", record_id, detail=str(v.attempt + 1))
            v = self._own_review(s, group_id, user_id, record_id)
            return self._review_view(v)

    def pending_attempt(self, record_id: str) -> int | None:
        with self._sf() as s:
            v = s.execute(select(execution_reviews.c.state, execution_reviews.c.attempt).where(
                execution_reviews.c.record_id == record_id)).one_or_none()
        return v.attempt if v is not None and v.state == PENDING else None

    def _cas(self, record_id: str, attempt: int, from_state: str, **values) -> bool:
        with self._sf() as s, s.begin():
            n = s.execute(update(execution_reviews).where(
                execution_reviews.c.record_id == record_id, execution_reviews.c.attempt == attempt,
                execution_reviews.c.state == from_state).values(**values)).rowcount
        return n == 1

    def _finish_event(self, record_id: str, kind: str, detail: str | None) -> None:
        with self._sf() as s, s.begin():
            rec = s.execute(select(execution_records).where(execution_records.c.id == record_id)).one_or_none()
            if rec is not None:
                self._event(s, rec.group_id, rec.user_id, kind, rec, detail=detail)

    def run_review(self, record_id: str, attempt: int) -> None:
        """Background: PENDING -> RUNNING -> SUCCEEDED | FAILED | SKIPPED. One Timely call at most, no automatic retry.
        A record deleted meanwhile (`되돌리기`) simply makes every write a no-op."""
        if not self._cas(record_id, attempt, PENDING, state=RUNNING, started_at=self._clock()):
            return
        try:
            with self._sf() as s:
                rec = s.execute(select(execution_records).where(execution_records.c.id == record_id)).one_or_none()
                if rec is None or rec.task_id is None:
                    return
                snap = s.execute(select(todo_confirmed_items.c.title, todo_confirmed_items.c.deliverable,
                                        todo_confirmed_items.c.completion_criteria, todo_confirmed_items.c.evidence_types)
                                 .where(todo_confirmed_items.c.group_id == rec.group_id,
                                        todo_confirmed_items.c.user_id == rec.user_id,
                                        todo_confirmed_items.c.source_assignment_revision == rec.source_assignment_revision,
                                        todo_confirmed_items.c.confirmation_revision == rec.confirmation_revision,
                                        todo_confirmed_items.c.task_id == rec.task_id)).one()
                fs = s.execute(select(execution_files).where(execution_files.c.record_id == record_id)
                               .order_by(execution_files.c.position)).all()
            evidence, excluded_kinds = [], []
            pages = chars = 0
            embedded = False
            for f in fs:
                if not f.review_kind:
                    excluded_kinds.append(f.kind)
                    continue
                data = self.storage.read(rec.group_id, f.id)
                eid = f"e{len(evidence) + 1}"
                if f.review_kind == "IMAGE":
                    evidence.append({"id": eid, "image": F.review_image(data)})
                elif f.review_kind == "PDF":
                    url, n = F.review_pdf(data)
                    pages += n
                    evidence.append({"id": eid, "pdf": url})
                else:
                    t = F.review_text(f.kind, data)
                    chars += len(t)
                    embedded = embedded or bool(f.embedded_images)
                    evidence.append({"id": eid, "text": t or "(추출된 텍스트 없음)"})
            counts = dict(reviewed_files=len(evidence), excluded_files=len(excluded_kinds), embedded_images_skipped=embedded)
            if pages > F.REVIEW_MAX_PDF_PAGES or chars > F.REVIEW_MAX_TEXT:
                self._cas(record_id, attempt, RUNNING, state=SKIPPED, skip_reason="OVER_LIMIT", finished_at=self._clock(), **counts)
                self._finish_event(record_id, "REVIEW_SKIPPED", "OVER_LIMIT")
                log.info("review_event record_id=%s attempt=%d state=SKIPPED reason=OVER_LIMIT", record_id, attempt)
                return
            criteria = list(snap.completion_criteria)
            messages = R.build_messages({"title": snap.title, "deliverable": snap.deliverable,
                                         "completion_criteria": criteria, "evidence_types": snap.evidence_types},
                                        rec.title, rec.memo, evidence, R.excluded_phrases(excluded_kinds))
            del evidence  # data URLs live only for the call
        except Exception as exc:  # never the message: it may hold file content
            log.error("review_event record_id=%s attempt=%d state=FAILED error_type=%s", record_id, attempt, type(exc).__name__)
            self._cas(record_id, attempt, RUNNING, state=FAILED, error_code="PREPARE_FAILED", finished_at=self._clock())
            self._finish_event(record_id, "REVIEW_FAILED", "PREPARE_FAILED")
            return

        from wemeet_ai.m0.client import AIError
        from wemeet_ai.m2.contracts import CompletionReview

        started = time.perf_counter()
        usage = {}
        try:
            completion = self._client_factory().complete_json(messages, CompletionReview, {"reasoning_effort": "low"})
            u = getattr(completion, "usage", None)
            usage = dict(model=(getattr(completion, "model", None) or "")[:64] or None,
                         request_id=(getattr(completion, "provider_request_id", None) or "")[:128] or None,
                         latency_ms=int(getattr(completion, "latency_ms", None) or (time.perf_counter() - started) * 1000),
                         prompt_tokens=getattr(u, "prompt_tokens", None), completion_tokens=getattr(u, "completion_tokens", None),
                         total_tokens=getattr(u, "total_tokens", None))
            review = completion.parsed_output
            R.check(review, criteria, {f"e{i + 1}" for i in range(counts["reviewed_files"])},
                    bool((rec.title or "").strip() or (rec.memo or "").strip()))
        except AIError as exc:
            self._cas(record_id, attempt, RUNNING, state=FAILED, error_code=exc.code[:64], finished_at=self._clock(), **counts, **usage)
            self._finish_event(record_id, "REVIEW_FAILED", exc.code[:32])
            log.info("review_event record_id=%s attempt=%d state=FAILED error=%s", record_id, attempt, exc.code)
            return
        except Exception as exc:
            log.error("review_event record_id=%s attempt=%d state=FAILED error_type=%s", record_id, attempt, type(exc).__name__)
            self._cas(record_id, attempt, RUNNING, state=FAILED, error_code="HANDLER_ERROR", finished_at=self._clock(), **counts, **usage)
            self._finish_event(record_id, "REVIEW_FAILED", "HANDLER_ERROR")
            return
        results = [{"criterion_index": r.criterion_index, "status": r.status, "reason": r.reason,
                    "evidence_refs": [e.evidence_id for e in r.evidence_refs]} for r in review.criteria_results]
        if self._cas(record_id, attempt, RUNNING, state=SUCCEEDED, decision=review.decision, criteria_results=results,
                     guidance=R.guidance(review), finished_at=self._clock(), **counts, **usage):
            self._finish_event(record_id, "REVIEW_DONE", review.decision)
        log.info("review_event record_id=%s attempt=%d state=SUCCEEDED decision=%s reviewed=%d excluded=%d",
                 record_id, attempt, review.decision, counts["reviewed_files"], counts["excluded_files"])

    def fail_interrupted(self) -> int:
        """Reviews lost with the previous process become a retryable failure (no automatic re-run)."""
        with self._sf() as s, s.begin():
            return s.execute(update(execution_reviews).where(execution_reviews.c.state.in_((PENDING, RUNNING)))
                             .values(state=FAILED, error_code="SERVER_RESTARTED", finished_at=self._clock())).rowcount
