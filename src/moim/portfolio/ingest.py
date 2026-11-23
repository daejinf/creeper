"""The ingest trigger (D-I1 I1, RAG_DESIGN 2.7): a completed own record with attachments gets one portfolio job.

`on_done` runs inside the transaction that turns a record's `completion` into DONE (`execution.votes._set`, the single
place every completion path goes through: AI PASS, TEAM majority, SELF; MANUAL records from before D-H1 come through
`enqueue_record`). It copies, as they are at that moment, the project name and description (the group's current
ProjectBrief), the submitter's role name (the assignment revision the record was made against), the todo title and
due date, the memo, how it was completed and the M2 decision word. It copies nothing else: never `deliverable`,
`completion_criteria`, evidence types, the M2 per-criterion reasons or guidance, votes, MemberState, interviews,
conversations, or anything of another member. The attachments themselves are read later by the job, from that one
record only (`repo.source_files`).

Idempotent: `portfolio_items.source_record_id` is unique, so a second call for the same record does nothing. The new
(user_id, job_id) pairs are kept on the session (`take_new`) so the caller can start them after its commit.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import execution_records, execution_reviews, groups, project_briefs, role_assignment_members, todo_confirmed_items
from . import repo

_NEW = "portfolio_new_jobs"


def on_done(s: Session, rec, completed_by: str, now: datetime) -> str | None:
    """`rec` is the record row as it was read by the caller (its owner, group, todo keys, file count, memo)."""
    if rec.task_id is None or not rec.file_count:
        return None  # a note, or a completion without attachments: nothing to collect (D-I1 I1)
    g = s.execute(select(groups.c.brief_revision).where(groups.c.id == rec.group_id)).one_or_none()
    if g is None:
        return None
    brief = s.execute(select(project_briefs.c.name, project_briefs.c.content).where(
        project_briefs.c.group_id == rec.group_id, project_briefs.c.revision == g.brief_revision)).one()
    role = s.execute(select(role_assignment_members.c.role_name).where(
        role_assignment_members.c.group_id == rec.group_id,
        role_assignment_members.c.assignment_revision == rec.source_assignment_revision,
        role_assignment_members.c.user_id == rec.user_id)).scalar_one()
    todo = s.execute(select(todo_confirmed_items.c.title, todo_confirmed_items.c.due_date).where(
        todo_confirmed_items.c.group_id == rec.group_id, todo_confirmed_items.c.user_id == rec.user_id,
        todo_confirmed_items.c.source_assignment_revision == rec.source_assignment_revision,
        todo_confirmed_items.c.confirmation_revision == rec.confirmation_revision,
        todo_confirmed_items.c.task_id == rec.task_id)).one()
    decision = s.execute(select(execution_reviews.c.decision).where(
        execution_reviews.c.record_id == rec.id)).scalar_one_or_none()
    project_id = repo.upsert_project(s, user_id=rec.user_id, source_group_id=rec.group_id, name=brief.name,
                                     description=brief.content or "", role_name=role, now=now)
    job_id = repo.create_item_with_job(
        s, user_id=rec.user_id, project_id=project_id, source_record_id=rec.id, source_task_id=rec.task_id,
        task_title=todo.title, due_date=todo.due_date, role_name=role, memo=rec.memo, file_count=rec.file_count,
        completed_by=completed_by, review_decision=decision, completed_at=now)
    if job_id is not None:
        s.info.setdefault(_NEW, []).append((rec.user_id, job_id))
    return job_id


def enqueue_record(s: Session, *, user_id: str, record_id: str, now: datetime) -> str | None:
    """A record that is already DONE (e.g. `MANUAL` from before D-H1, or the stage 8D `이전 기록 가져오기`): the same
    copy, only for the caller's own record."""
    rec = s.execute(select(execution_records).where(execution_records.c.id == record_id,
                                                    execution_records.c.user_id == repo._uid(user_id),
                                                    execution_records.c.completion == "DONE")).one_or_none()
    if rec is None:
        return None
    return on_done(s, rec, rec.completed_by, now)


def take_new(s: Session) -> list[tuple[str, str]]:
    """The (user_id, job_id) pairs created in this session so far; cleared."""
    return s.info.pop(_NEW, [])
