"""D-H1: when a completion attempt is decided.

A todo is completed only by an M2 PASS or by the team. Any other M2 outcome (REVIEW_NEEDED, NEEDS_INPUT, an
unsupported format, over the review limit, a failure) opens a team vote at once. The voters are the submitter's
**other current active members** (D-D1 D5 gate: still in `group_members`); the submitter does not vote. With N voters
the attempt is completed when more than half agree (`N // 2 + 1`), and rejected as soon as that many agreements can no
longer be reached (a tie is not a majority). Votes of members who left stop counting. A group where nobody else is
left completes the attempt on the submitter's own submission (SELF): there is nobody to ask.

Pure functions over the caller's session; the caller holds the group's execution lock.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from ..models import execution_events, execution_records, execution_votes, group_members

REVIEWING, VOTING, DONE, REJECTED, WITHDRAWN = "REVIEWING", "VOTING", "DONE", "REJECTED", "WITHDRAWN"
OPEN = (REVIEWING, VOTING, DONE)  # at most one per todo (unique index)


def tally(s: Session, rec) -> dict:
    """{"voters", "need", "agree", "reject", "votes": {user_id: agree}} counting current active members only."""
    voters = s.execute(select(group_members.c.user_id).where(
        group_members.c.group_id == rec.group_id, group_members.c.user_id != rec.user_id)).scalars().all()
    votes = {v.user_id: v.agree for v in s.execute(select(execution_votes.c.user_id, execution_votes.c.agree).where(
        execution_votes.c.record_id == rec.id)).all() if v.user_id in set(voters)}
    n = len(voters)
    agree = sum(1 for a in votes.values() if a)
    return {"voters": n, "need": n // 2 + 1 if n else 0, "agree": agree, "reject": len(votes) - agree, "votes": votes}


def _set(s: Session, rec, completion: str, by: str | None, now: datetime) -> None:
    s.execute(update(execution_records).where(execution_records.c.id == rec.id).values(
        completion=completion, completed_by=by, marks_done=completion == DONE))
    s.execute(insert(execution_events).values(
        group_id=rec.group_id, user_id=rec.user_id, kind="SETTLED", record_id=rec.id, task_id=rec.task_id,
        source_assignment_revision=rec.source_assignment_revision, confirmation_revision=rec.confirmation_revision,
        file_count=rec.file_count, detail=f"{completion}:{by}" if by else completion, created_at=now))


def settle(s: Session, rec, now: datetime) -> str:
    """Decide a VOTING attempt if it can be decided. Returns its completion state afterwards."""
    if rec.completion != VOTING:
        return rec.completion
    owner_active = s.execute(select(group_members.c.user_id).where(
        group_members.c.group_id == rec.group_id, group_members.c.user_id == rec.user_id)).first() is not None
    if not owner_active:  # the submitter left: nobody is waiting for the answer
        _set(s, rec, WITHDRAWN, None, now)
        return WITHDRAWN
    t = tally(s, rec)
    if t["voters"] == 0:
        _set(s, rec, DONE, "SELF", now)
        return DONE
    if t["agree"] >= t["need"]:
        _set(s, rec, DONE, "TEAM", now)
        return DONE
    if t["agree"] + (t["voters"] - t["agree"] - t["reject"]) < t["need"]:
        _set(s, rec, REJECTED, None, now)
        return REJECTED
    return VOTING


def open_vote(s: Session, rec, now: datetime) -> str:
    """REVIEWING -> VOTING (any M2 outcome but PASS), then settle (a lone member completes at once)."""
    if rec.completion != REVIEWING:
        return rec.completion
    s.execute(update(execution_records).where(execution_records.c.id == rec.id).values(completion=VOTING))
    rec = s.execute(select(execution_records).where(execution_records.c.id == rec.id)).one()
    return settle(s, rec, now)


def pass_review(s: Session, rec, now: datetime) -> str:
    """REVIEWING -> DONE on an M2 PASS."""
    if rec.completion != REVIEWING:
        return rec.completion
    _set(s, rec, DONE, "AI", now)
    return DONE


def settle_group(s: Session, group_id: str, now: datetime) -> None:
    """Every open vote of one group, after the membership changed (someone left)."""
    for rec in s.execute(select(execution_records).where(
            execution_records.c.group_id == group_id, execution_records.c.completion == VOTING)).all():
        settle(s, rec, now)
