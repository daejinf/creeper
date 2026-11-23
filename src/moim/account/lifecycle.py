"""Stage 6 account lifecycle: leaving a group (D-D1 D6) and deleting an account (D-D1 D7).

Every operation is ONE database transaction: it either happens completely or not at all, so a failure is never shown
as a partial success. Files on disk are removed only after the commit (a file whose row is gone is unreachable, and
`Storage` paths are built from server ids only); a file that could not be removed is logged and counted, never shown
as kept data.

Leaving a group (D6), for the caller only:
- access to the group's screens, APIs and files ends at once (the `group_members` row goes; every gate reads it);
  the activation cohort row stays as history only;
- the caller's private data in that group is deleted: the interview conversations (stage 3 and post-assignment), the
  MemberState revisions, the todo AI steps (their change requests) and unconfirmed todo working copies; the interview
  snapshot keeps only its key (`purged_at`, content erased) because the confirmed todos hang off it; rankings no
  assignment record points at are deleted;
- completion records and attachments stay on the team feed as "나간 팀원"; the caller's not-done todos leave the group
  screen (only current active members are shown); nobody else's assignment changes (no re-draw);
- a leader hands the group to the earliest-joined remaining member (ties: user id order), without a new screen;
- the last member leaving deletes the whole group (all rows and files);
- before the first assignment, a cohort that is now complete is assigned (nobody waits for someone who left).

Deleting an account (D7): password check, then D6 for every group, then the caller's own todos, records and files in
every group (also groups left earlier), the sessions and the user row. The username is released at once. What the
team keeps (the immutable assignment record, cohort history, ProjectBrief authorship, reassignment and audit metadata)
is moved to a fresh "탈퇴한 사용자" tombstone row with a new id, so nothing kept points at the deleted account.

Deleting a group (D-H1): only its leader, after confirming the impact; every row and file of the group goes at once
for every member (the same purge as the last member leaving).

Portfolio copies (stage 8A, D-I1 I4) belong to their owner, not to the group: leaving a group or deleting it leaves
them alone (they have no foreign key to group tables), deleting an account deletes them all in the same transaction,
and a replayed `되돌리기` deletes that record's copy again. A vote settled by someone leaving can complete another
member's attempt; its portfolio job is returned in `Outcome.portfolio_jobs` for the route to start.

Every leave, group deletion and account deletion also writes its internal ids to the deletion ledger (stage 7, D-G1, `ledger.py`)
inside its transaction, just before the commit, so a backup restore can run it again (`replay`). The ledger fails
closed: when the line cannot be written, the deletion is rolled back and reported as "try again later".
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from sqlalchemy import Text, and_, delete, exists, func, insert, literal, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..groups.service import ACTIVE, CANCELLED, FORMING, LEADER, GroupService, is_group_id
from ..models import (
    activation_cohorts,
    cohort_members,
    execution_events,
    execution_files,
    execution_records,
    goal_displays,
    group_members,
    groups,
    interview_turns,
    invite_codes,
    lifecycle_events,
    member_progress,
    member_states,
    preference_submissions,
    project_briefs,
    role_assignment_members,
    role_assignments,
    role_candidate_sets,
    role_interview_snapshots,
    role_interview_turns,
    role_interviews,
    role_member_states,
    role_reassignments,
    team_coverages,
    todo_actions,
    todo_ai_steps,
    todo_confirmations,
    todo_confirmed_items,
    todo_drafts,
    todo_items,
    users,
)
from ..portfolio import ingest as portfolio_ingest
from ..portfolio import repo as portfolio_repo
from ..roles.assignment import AssignmentService
from ..execution.votes import settle_group
from .ledger import DeletionLedger, LedgerUnavailable
from ..timeutil import utcnow

log = logging.getLogger("moim.lifecycle")

TOMBSTONE_NAME = "탈퇴한 사용자"
MSG_NOT_FOUND = "찾을 수 없어요."
MSG_CONFIRM = "확인이 필요해요. 내용을 확인한 뒤 다시 시도해 주세요."
MSG_UNAVAILABLE = "지금은 처리할 수 없어요. 잠시 후 다시 시도해 주세요."
MSG_LEADER_ONLY = "그룹장만 그룹을 삭제할 수 있어요."
ROOM_NAME = "초대방"  # a FORMING room has no ProjectBrief name yet

# The transaction-scoped switch the 0012 trigger honours for confirmed todos. Only this module sets it.
_PURGE_ON = text("set local moim.lifecycle_purge = 'on'")


class LifecycleError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def not_found() -> LifecycleError:
    return LifecycleError(404, "NOT_FOUND", MSG_NOT_FOUND)


@dataclass
class Outcome:
    """What one transaction did. `files` are (group_id, file_id) to remove after commit; `purged_groups` too."""

    files: list[tuple[str, str]] = field(default_factory=list)
    purged_groups: list[str] = field(default_factory=list)
    assigned: list[tuple[str, int]] = field(default_factory=list)  # new assignment revisions (coverage to start)
    handed_over: int = 0
    left: int = 0
    tombstone: bool = False
    portfolio_jobs: list[tuple[str, str]] = field(default_factory=list)  # (user_id, job_id) created by a settled vote


class LifecycleService:
    def __init__(self, session_factory: sessionmaker[Session], storage, assignment: AssignmentService,
                 clock: Callable[[], datetime] = utcnow, ledger: DeletionLedger | None = None):
        self._sf = session_factory
        self.storage = storage
        self._assignment = assignment
        self._clock = clock
        self.ledger = ledger or DeletionLedger(None)

    # ---- shared reads -------------------------------------------------------------------------------------------------

    @staticmethod
    def _remaining(s: Session, group_id: str, user_id: str) -> list:
        """The other current members, earliest joined first (ties: user id): the succession order (D6)."""
        return s.execute(
            select(group_members.c.user_id, group_members.c.joined_at, users.c.display_name)
            .join(users, users.c.id == group_members.c.user_id)
            .where(group_members.c.group_id == group_id, group_members.c.user_id != user_id)
            .order_by(group_members.c.joined_at, group_members.c.user_id)
        ).all()

    @staticmethod
    def _group_name(s: Session, g) -> str:
        if g.status == ACTIVE:
            return s.execute(select(project_briefs.c.name).where(
                project_briefs.c.group_id == g.id, project_briefs.c.revision == g.brief_revision)).scalar_one()
        return ROOM_NAME

    @staticmethod
    def _my_role(s: Session, group_id: str, user_id: str) -> str | None:
        return s.execute(select(group_members.c.role).where(
            group_members.c.group_id == group_id, group_members.c.user_id == user_id)).scalar_one_or_none()

    def _impact(self, s: Session, g, user_id: str, role: str) -> dict:
        remaining = self._remaining(s, g.id, user_id)
        kept = s.execute(select(func.count()).select_from(execution_records).where(
            execution_records.c.group_id == g.id, execution_records.c.user_id == user_id)).scalar_one()
        files = s.execute(select(func.count()).select_from(execution_files).join(
            execution_records, execution_records.c.id == execution_files.c.record_id).where(
            execution_records.c.group_id == g.id, execution_records.c.user_id == user_id)).scalar_one()
        open_todos = 0
        if g.status == ACTIVE:
            from ..roles.todos import TodoService

            shown = TodoService.shown_confirmations(s, g.id).get(user_id)
            if shown is not None:
                done = select(execution_records.c.task_id).where(execution_records.c.marks_done,
                                                                execution_records.c.task_id.is_not(None))
                open_todos = s.execute(select(func.count()).select_from(todo_confirmed_items).where(
                    todo_confirmed_items.c.group_id == g.id, todo_confirmed_items.c.user_id == user_id,
                    todo_confirmed_items.c.source_assignment_revision == shown[0],
                    todo_confirmed_items.c.confirmation_revision == shown[1],
                    todo_confirmed_items.c.task_id.not_in(done))).scalar_one()
        return {
            "group_name": self._group_name(s, g),
            "is_leader": role == LEADER,
            # A leader hands over to the earliest-joined remaining member; that name is already on the group page.
            "successor_name": remaining[0].display_name if role == LEADER and remaining else None,
            "deletes_group": not remaining,
            "kept_records": kept,
            "kept_files": files,
            "removed_todos": open_todos,
        }

    # ---- D6: leave one group ------------------------------------------------------------------------------------------

    def leave_preview(self, user_id: str, group_id: str) -> dict:
        with self._sf() as s:
            g, role = self._active_member(s, group_id, user_id, lock=False)
            return self._impact(s, g, user_id, role)

    @staticmethod
    def _active_member(s: Session, group_id: str, user_id: str, lock: bool):
        if not is_group_id(group_id or ""):
            raise not_found()
        q = select(groups).where(groups.c.id == group_id)
        g = s.execute(q.with_for_update() if lock else q).one_or_none()
        role = LifecycleService._my_role(s, group_id, user_id) if g is not None else None
        if g is None or role is None or g.status != ACTIVE:
            raise not_found()  # a stranger, someone who already left and a missing group look the same
        return g, role

    def leave_group(self, user_id: str, group_id: str, confirm: bool, record: bool = True) -> tuple[dict, Outcome]:
        """(impact that was applied, outcome). Without `confirm` nothing changes: 409 with the impact to show.
        `record=False` only for `replay` (the entry is already in the ledger)."""
        out = Outcome()
        entry: list[str | None] = []
        with self._ledgered(entry):
            with self._sf.begin() as s:
                GroupService._lock_user(s, user_id)
                g, role = self._active_member(s, group_id, user_id, lock=True)
                self._assignment.lock_group(s, group_id)
                _execution_lock(s, group_id)
                impact = self._impact(s, g, user_id, role)
                if not confirm:
                    raise LifecycleError(409, "CONFIRM_REQUIRED", MSG_CONFIRM, extra={"impact": impact})
                now = self._clock()
                self._leave(s, g, user_id, role, now, out)
                if record:
                    entry.append(self.ledger.record("LEAVE", user_id, group_id))  # last step before the commit
            out.portfolio_jobs = portfolio_ingest.take_new(s)
        self._after_commit(out)
        log.info("lifecycle_event kind=LEAVE group_id=%s user_id=%s group_deleted=%s handed_over=%d",
                 group_id, user_id, bool(out.purged_groups), out.handed_over)
        return impact, out

    def _leave(self, s: Session, g, user_id: str, role: str, now, out: Outcome) -> None:
        """Inside the caller's transaction, which holds the group's row lock, assignment lock and execution lock."""
        gid = g.id
        self._delete_private(s, gid, user_id, now)
        s.execute(update(role_reassignments).where(
            role_reassignments.c.group_id == gid, role_reassignments.c.state == "REQUESTED",
            (role_reassignments.c.target_user_id == user_id) | (role_reassignments.c.requested_by == user_id),
        ).values(state="SUPERSEDED", decided_at=now))
        s.execute(delete(group_members).where(group_members.c.group_id == gid, group_members.c.user_id == user_id))
        out.left += 1
        _event(s, "LEAVE", user_id, gid, now)
        remaining = self._remaining(s, gid, user_id)
        if not remaining:
            self._purge_group(s, gid, out)
            _event(s, "GROUP_DELETED", None, gid, now, detail="LAST_MEMBER_LEFT")
            return
        settle_group(s, gid, now)  # D-H1: open votes are counted again without the member who left
        if role == LEADER:
            heir = remaining[0].user_id
            s.execute(update(group_members).where(group_members.c.group_id == gid, group_members.c.user_id == heir)
                      .values(role=LEADER))
            s.execute(update(groups).where(groups.c.id == gid).values(owner_id=heir))
            out.handed_over += 1
            _event(s, "LEADER_HANDOFF", heir, gid, now)
        if g.status == ACTIVE and AssignmentService.current(s, gid) is None:
            g_now = s.execute(select(groups).where(groups.c.id == gid)).one()
            rev = self._assignment.assign_if_ready(s, g_now, now)
            if rev is not None:
                out.assigned.append((gid, rev))

    @staticmethod
    def _delete_private(s: Session, gid: str, uid: str, now) -> None:
        """D6: the caller's private conversation, MemberState and interview data in one group."""
        mine = lambda t: and_(t.c.group_id == gid, t.c.user_id == uid)  # noqa: E731
        s.execute(delete(member_states).where(mine(member_states)))
        s.execute(delete(interview_turns).where(mine(interview_turns)))
        s.execute(delete(role_member_states).where(mine(role_member_states)))
        s.execute(delete(role_interview_turns).where(mine(role_interview_turns)))
        s.execute(update(role_interview_snapshots).where(mine(role_interview_snapshots)).values(
            provider_input={}, private_constraints={}, deadline_limits={}, purged_at=now))
        s.execute(delete(todo_ai_steps).where(mine(todo_ai_steps)))
        s.execute(delete(todo_actions).where(mine(todo_actions)))
        confirmed = select(todo_confirmed_items.c.task_id).where(mine(todo_confirmed_items))
        s.execute(delete(todo_items).where(mine(todo_items), todo_items.c.task_id.not_in(confirmed)))
        s.execute(delete(member_progress).where(mine(member_progress)))
        referenced = select(literal(1)).where(
            role_assignment_members.c.group_id == preference_submissions.c.group_id,
            role_assignment_members.c.user_id == preference_submissions.c.user_id,
            role_assignment_members.c.submission_revision == preference_submissions.c.revision)
        s.execute(delete(preference_submissions).where(mine(preference_submissions), ~exists(referenced)))

    @staticmethod
    def _purge_group(s: Session, gid: str, out: Outcome) -> None:
        """Every row of one group, children first. Confirmed todos need the scoped purge switch."""
        s.execute(_PURGE_ON)
        files = s.execute(select(execution_files.c.id).where(execution_files.c.group_id == gid)).scalars().all()
        out.files.extend((gid, f) for f in files)
        by_group = lambda t: t.c.group_id == gid  # noqa: E731
        for t in (execution_records, execution_events, todo_confirmed_items, todo_confirmations, todo_actions,
                  todo_ai_steps, todo_items, todo_drafts, role_interview_snapshots, role_member_states,
                  role_interview_turns, role_interviews, role_reassignments, member_progress, team_coverages,
                  role_assignment_members, role_assignments, preference_submissions, member_states, interview_turns,
                  role_candidate_sets, goal_displays, cohort_members, activation_cohorts, project_briefs, invite_codes,
                  group_members):
            s.execute(delete(t).where(by_group(t)))  # execution files and reviews go with their records (cascade)
        s.execute(delete(groups).where(groups.c.id == gid))
        out.purged_groups.append(gid)

    # ---- D-H1: the leader deletes the whole group -----------------------------------------------------------------

    def _group_impact(self, s: Session, g) -> dict:
        n = lambda q: s.execute(q).scalar_one()  # noqa: E731
        return {
            "group_name": self._group_name(s, g),
            "members": n(select(func.count()).select_from(group_members).where(group_members.c.group_id == g.id)),
            "records": n(select(func.count()).select_from(execution_records).where(execution_records.c.group_id == g.id)),
            "files": n(select(func.count()).select_from(execution_files).where(execution_files.c.group_id == g.id)),
        }

    def _leader(self, s: Session, group_id: str, user_id: str, lock: bool):
        g, role = self._active_member(s, group_id, user_id, lock=lock)
        if role != LEADER:
            raise LifecycleError(403, "LEADER_ONLY", MSG_LEADER_ONLY)
        return g

    def delete_group_preview(self, user_id: str, group_id: str) -> dict:
        with self._sf() as s:
            return self._group_impact(s, self._leader(s, group_id, user_id, lock=False))

    def delete_group(self, user_id: str, group_id: str, confirm: bool, record: bool = True) -> tuple[dict, Outcome]:
        """(impact that was applied, outcome). Leader only. Without `confirm` nothing changes: 409 with the impact."""
        out = Outcome()
        entry: list[str | None] = []
        with self._ledgered(entry):
            with self._sf.begin() as s:
                GroupService._lock_user(s, user_id)
                g = self._leader(s, group_id, user_id, lock=True)
                self._assignment.lock_group(s, group_id)
                _execution_lock(s, group_id)
                impact = self._group_impact(s, g)
                if not confirm:
                    raise LifecycleError(409, "CONFIRM_REQUIRED", MSG_CONFIRM, extra={"impact": impact})
                self._purge_group(s, group_id, out)
                _event(s, "GROUP_DELETED", user_id, group_id, self._clock(), detail="LEADER_DELETED")
                if record:
                    entry.append(self.ledger.record("GROUP_DELETED", user_id, group_id))  # last step before the commit
        self._after_commit(out)
        log.info("lifecycle_event kind=GROUP_DELETED group_id=%s user_id=%s files=%d", group_id, user_id, len(out.files))
        return impact, out

    def _replay_group_delete(self, group_id: str) -> Outcome:
        """D-H1 group deletion again after a restore. The leader already confirmed it; roles are not checked again."""
        out = Outcome()
        with self._sf.begin() as s:
            if s.execute(select(groups.c.id).where(groups.c.id == group_id).with_for_update()).first() is None:
                raise not_found()
            self._assignment.lock_group(s, group_id)
            _execution_lock(s, group_id)
            self._purge_group(s, group_id, out)
            _event(s, "GROUP_DELETED", None, group_id, self._clock(), detail="REPLAYED")
        self._after_commit(out)
        return out

    # ---- D7: delete the account ---------------------------------------------------------------------------------------

    def delete_preview(self, user_id: str) -> dict:
        with self._sf() as s:
            rows = self._memberships(s, user_id)
            handoffs, deleted, n = [], [], 0
            for g, role in rows:
                if g.status == CANCELLED:
                    continue
                n += 1
                imp = self._impact(s, g, user_id, role)
                if imp["deletes_group"]:
                    deleted.append(imp["group_name"])
                elif imp["successor_name"]:
                    handoffs.append({"group_name": imp["group_name"], "successor_name": imp["successor_name"]})
            records = s.execute(select(func.count()).select_from(execution_records).where(
                execution_records.c.user_id == user_id)).scalar_one()
            files = s.execute(select(func.count()).select_from(execution_files).join(
                execution_records, execution_records.c.id == execution_files.c.record_id).where(
                execution_records.c.user_id == user_id)).scalar_one()
        return {"groups": n, "handoffs": handoffs, "deleted_groups": deleted, "records": records, "files": files}

    @staticmethod
    def _memberships(s: Session, user_id: str, lock: bool = False) -> list:
        ids = sorted(set(s.execute(select(group_members.c.group_id).where(group_members.c.user_id == user_id)).scalars())
                     | set(s.execute(select(groups.c.id).where(groups.c.owner_id == user_id)).scalars()))
        if not ids:
            return []
        q = select(groups).where(groups.c.id.in_(ids)).order_by(groups.c.id)
        gs = s.execute(q.with_for_update() if lock else q).all()
        return [(g, LifecycleService._my_role(s, g.id, user_id)) for g in gs]

    def delete_account(self, user_id: str, record: bool = True) -> Outcome:
        """The password was checked by the caller (outside the locks). Everything below is one transaction."""
        out = Outcome()
        entry: list[str | None] = []
        with self._ledgered(entry), self._sf.begin() as s:
            GroupService._lock_user(s, user_id)
            me = s.execute(select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None))).one_or_none()
            if me is None:
                raise not_found()
            now = self._clock()
            memberships = self._memberships(s, user_id, lock=True)
            for g, _ in memberships:
                self._assignment.lock_group(s, g.id)
            for g, _ in memberships:
                _execution_lock(s, g.id)
            for g, role in memberships:
                if g.status == CANCELLED:
                    if g.owner_id == user_id:
                        self._purge_group(s, g.id, out)  # a closed room of mine: nothing in it belongs to anyone else
                        _event(s, "GROUP_DELETED", None, g.id, now, detail="OWNER_DELETED")
                    else:
                        s.execute(delete(group_members).where(group_members.c.group_id == g.id,
                                                              group_members.c.user_id == user_id))
                    continue
                if role is None:  # owner without a member row cannot happen; be strict rather than guess
                    raise LifecycleError(409, "INCONSISTENT", MSG_NOT_FOUND)
                self._leave(s, g, user_id, role, now, out)
            self._delete_own_data(s, user_id, out)
            out.tombstone = self._tombstone(s, user_id, now)
            s.execute(delete(users).where(users.c.id == user_id))  # sessions go with it (ON DELETE CASCADE)
            if record:
                entry.append(self.ledger.record("ACCOUNT_DELETED", user_id))  # last step before the commit
        out.portfolio_jobs = [p for p in portfolio_ingest.take_new(s) if p[0] != user_id]
        self._after_commit(out)
        log.info("lifecycle_event kind=ACCOUNT_DELETED user_id=%s groups_left=%d groups_deleted=%d files=%d",
                 user_id, out.left, len(out.purged_groups), len(out.files))
        return out

    @staticmethod
    def _delete_own_data(s: Session, uid: str, out: Outcome) -> None:
        """D7: my records, files and todos in every group (also groups I left earlier), and what is left of my private
        state. The immutable assignment record is not deleted here: `_tombstone` re-points it."""
        s.execute(_PURGE_ON)
        out.files.extend((r.group_id, r.id) for r in s.execute(
            select(execution_files.c.group_id, execution_files.c.id)
            .join(execution_records, execution_records.c.id == execution_files.c.record_id)
            .where(execution_records.c.user_id == uid)).all())
        s.execute(delete(execution_records).where(execution_records.c.user_id == uid))  # files, reviews: cascade
        portfolio_repo.delete_all(s, user_id=uid)  # D-I1 I4: every portfolio copy (also of groups left or deleted)
        for t in (todo_confirmed_items, todo_confirmations, todo_actions, todo_ai_steps, todo_items, todo_drafts,
                  role_interview_snapshots, role_member_states, role_interview_turns, role_interviews, member_progress,
                  member_states, interview_turns):
            s.execute(delete(t).where(t.c.user_id == uid))
        referenced = select(literal(1)).where(
            role_assignment_members.c.group_id == preference_submissions.c.group_id,
            role_assignment_members.c.user_id == preference_submissions.c.user_id,
            role_assignment_members.c.submission_revision == preference_submissions.c.revision)
        s.execute(delete(preference_submissions).where(preference_submissions.c.user_id == uid, ~exists(referenced)))

    @staticmethod
    def _tombstone(s: Session, uid: str, now) -> bool:
        """Move every kept team record from the account to a fresh "탈퇴한 사용자" row. False when nothing is kept."""
        refs = [
            select(literal(1)).where(role_assignment_members.c.user_id == uid),
            select(literal(1)).where(preference_submissions.c.user_id == uid),
            select(literal(1)).where(cohort_members.c.user_id == uid),
            select(literal(1)).where(activation_cohorts.c.activated_by == uid),
            select(literal(1)).where(project_briefs.c.created_by == uid),
            select(literal(1)).where((role_reassignments.c.target_user_id == uid) | (role_reassignments.c.requested_by == uid)),
            select(literal(1)).where(execution_events.c.user_id == uid),
            select(literal(1)).where(groups.c.owner_id == uid),
            select(literal(1)).where(func.strpos(func.cast(role_assignments.c.draw_audit, Text), uid) > 0),
        ]
        needed = any(s.execute(select(exists(q))).scalar() for q in refs)
        tid = None
        if needed:
            tid = str(uuid.uuid4())
            s.execute(insert(users).values(id=tid, username=None, display_name=TOMBSTONE_NAME, password_hash="!",
                                           notify_opt_in=False, terms_accepted_at=now, created_at=now, deleted_at=now))
            ps = preference_submissions.c
            s.execute(insert(preference_submissions).from_select(
                ["group_id", "user_id", "revision", "source_candidate_revision", "action_id", "rank1", "rank2", "rank3",
                 "created_at"],
                select(ps.group_id, literal(tid), ps.revision, ps.source_candidate_revision, ps.action_id, ps.rank1,
                       ps.rank2, ps.rank3, ps.created_at).where(ps.user_id == uid)))
            s.execute(update(role_assignment_members).where(role_assignment_members.c.user_id == uid).values(user_id=tid))
            s.execute(delete(preference_submissions).where(ps.user_id == uid))
            s.execute(update(cohort_members).where(cohort_members.c.user_id == uid).values(user_id=tid))
            s.execute(update(activation_cohorts).where(activation_cohorts.c.activated_by == uid).values(activated_by=tid))
            s.execute(update(project_briefs).where(project_briefs.c.created_by == uid).values(created_by=tid))
            s.execute(update(role_reassignments).where(role_reassignments.c.target_user_id == uid).values(target_user_id=tid))
            s.execute(update(role_reassignments).where(role_reassignments.c.requested_by == uid).values(requested_by=tid))
            s.execute(update(execution_events).where(execution_events.c.user_id == uid).values(user_id=tid))
            s.execute(update(groups).where(groups.c.owner_id == uid).values(owner_id=tid))
            # The server draw audit (D-B1 B4) names contenders by user id; ids are uuids, so a text replace is exact.
            s.execute(text(
                "update role_assignments set draw_audit = replace(draw_audit::text, :uid, :tid)::jsonb"
                " where strpos(draw_audit::text, :uid) > 0"), {"uid": uid, "tid": tid})
        s.execute(update(lifecycle_events).where(lifecycle_events.c.user_id == uid).values(user_id=tid))
        _event(s, "ACCOUNT_DELETED", tid, None, now)
        return needed

    def _replay_undo(self, user_id: str, group_id: str, record_id: str) -> Outcome:
        """D5 `되돌리기` again: the record, its files and its review go (cascade), as in `ExecutionService.undo`. The
        membership is not checked: the user already confirmed this deletion when it was still a member."""
        out = Outcome()
        with self._sf.begin() as s:
            _execution_lock(s, group_id)
            hit = execution_records.c.id == record_id
            mine = and_(hit, execution_records.c.group_id == group_id, execution_records.c.user_id == user_id)
            if s.execute(select(func.count()).select_from(execution_records).where(mine)).scalar_one() == 0:
                raise not_found()
            out.files.extend((group_id, f) for f in s.execute(
                select(execution_files.c.id).where(execution_files.c.record_id == record_id)).scalars())
            s.execute(delete(execution_records).where(mine))
            portfolio_repo.delete_item_for_record(s, user_id=user_id, record_id=record_id)  # its copy (D-I1)
        self._after_commit(out)
        log.info("lifecycle_event kind=UNDO_REPLAYED group_id=%s user_id=%s files=%d", group_id, user_id, len(out.files))
        return out

    def _replay_portfolio(self, kind: str, user_id: str, target_id: str) -> Outcome:
        """D-I1 I4: the owner's own portfolio deletion again (the item or project came back with a restore)."""
        with self._sf.begin() as s:
            fn = portfolio_repo.delete_item if kind == "PORTFOLIO_ITEM_DELETED" else portfolio_repo.delete_project
            arg = "item_id" if kind == "PORTFOLIO_ITEM_DELETED" else "project_id"
            if not fn(s, user_id=user_id, **{arg: target_id}):
                raise not_found()
        log.info("lifecycle_event kind=%s_REPLAYED user_id=%s", kind, user_id)
        return Outcome()

    @contextmanager
    def _ledgered(self, entry: list):
        """Around the deletion's transaction: a ledger write failure becomes a 503 (the transaction is rolled back by
        then), and a failure after the line was written (the commit) cancels that line."""
        try:
            yield
        except LedgerUnavailable:
            raise LifecycleError(503, "DELETION_UNAVAILABLE", MSG_UNAVAILABLE) from None
        except BaseException:
            for e in entry:
                self.ledger.cancel(e)
            raise

    # ---- D-G1: run ledger deletions again after a backup restore -------------------------------------------------

    def replay(self, entries: list[dict]) -> dict:
        """Run each ledger entry again, oldest first, through the same code as the user's own request. An entry whose
        effect is already in the database (account gone, not a member, group gone) is counted as `already`; that is
        the normal case for entries older than the backup. Any other error stops the replay (nothing is guessed);
        running it again is safe because every applied entry is then `already`."""
        result: dict = {"applied": 0, "already": 0, "assigned": []}
        for e in entries:
            try:
                if e["kind"] == "LEAVE":
                    _, out = self.leave_group(e["user_id"], e["group_id"], confirm=True, record=False)
                elif e["kind"] == "RECORD_UNDONE":
                    out = self._replay_undo(e["user_id"], e["group_id"], e["record_id"])
                elif e["kind"] == "GROUP_DELETED":
                    out = self._replay_group_delete(e["group_id"])
                elif e["kind"] in ("PORTFOLIO_ITEM_DELETED", "PORTFOLIO_PROJECT_DELETED"):
                    out = self._replay_portfolio(e["kind"], e["user_id"], e["target_id"])
                else:
                    out = self.delete_account(e["user_id"], record=False)
            except LifecycleError as exc:
                if exc.status == 404:
                    result["already"] += 1
                    continue
                raise
            result["applied"] += 1
            result["assigned"].extend(out.assigned)
        return result

    # ---- after commit -------------------------------------------------------------------------------------------------

    def _after_commit(self, out: Outcome) -> None:
        failed = 0
        for gid, fid in out.files:
            if not self.storage.delete(gid, fid):
                failed += 1
        for gid in out.purged_groups:
            self.storage.delete_group_dir(gid)
        if failed:  # rows are gone, so the files are unreachable; logged so an operator can sweep them
            log.error("lifecycle_event kind=FILE_DELETE_FAILED count=%d", failed)


def _execution_lock(s: Session, group_id: str) -> None:
    s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"execution:{group_id}"})


def _event(s: Session, kind: str, user_id: str | None, group_id: str | None, now, detail: str | None = None) -> None:
    s.execute(insert(lifecycle_events).values(kind=kind, user_id=user_id, group_id=group_id, detail=detail, created_at=now))
