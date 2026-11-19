"""Stage 4 session C: reassignment operation and STALE propagation (D-B1 B4/B8, D-B4). Server API only, no screen.

Reassignment (B4): the group leader asks to reassign one member of the CURRENT assignment revision; only that member's
own consent makes the server write a new assignment revision (operation REASSIGN) from the same inputs: the same
cohort, candidate revision and preference submissions. Neither request nor consent carries a role, and no AI is
involved. The target's new role follows their own ranking (their 1st, 2nd, 3rd choice that still has a free place,
other than the role they leave); only when none is free does the server RNG pick among the free roles. Every other
member keeps their role and slot: they did not consent to a change. When no other role has a free place the request
is refused (NO_FREE_ROLE) rather than moving someone who did not agree.

STALE (B8): `mark_assignment_stale` marks an assignment revision and everything made from it (team coverage,
post-assignment interviews, interview snapshots, todo drafts, confirmed todo revisions with their hidden criteria)
STALE. Nothing is deleted or edited otherwise; every service refuses STALE rows as current input. Two reasons:
- REASSIGNED: a reassignment replaced the revision. Rankings and MemberState are kept (B8). A member whose role did
  not change keeps their confirmed interview input: it is copied to the new revision (the role-independent and
  role-specific answers both still describe the same role). The reassigned member answers a new interview.
- BRIEF_CHANGED: a new ProjectBrief revision (`GroupService.update_brief`). Members go back to ranking against the new
  candidate set. Their last confirmed todos stay read-only in the group read until they confirm new ones (B8).
"""
from __future__ import annotations

import logging
import random
import uuid
from typing import Callable

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..auth.errors import AuthError
from ..interview.ranking import RankingService, _action, not_found
from ..models import (
    group_members,
    member_progress,
    preference_submissions,
    role_assignment_members,
    role_assignments,
    role_candidate_sets,
    role_interview_snapshots,
    role_interviews,
    role_member_states,
    role_reassignments,
    team_coverages,
    todo_confirmations,
    todo_drafts,
)
from ..timeutil import utcnow
from .assignment import CURRENT, ROLE_TODOS, STALE, AssignmentService, capacities

log = logging.getLogger("moim.reassign")

REASSIGNED, BRIEF_CHANGED = "REASSIGNED", "BRIEF_CHANGED"
REQUESTED, ACCEPTED, DECLINED, SUPERSEDED = "REQUESTED", "ACCEPTED", "DECLINED", "SUPERSEDED"
DONE = "DONE"
MSG_NO_FREE_ROLE = "다른 팀원의 역할을 바꾸지 않고 옮길 수 있는 역할이 없어요."


class ReassignError(AuthError):
    """Same JSON shape as AuthError: {"error": {"code", "message"}}."""


def mark_assignment_stale(s: Session, group_id: str, revision: int, reason: str, now) -> None:
    """Inside the caller's transaction (holding `AssignmentService.lock_group`): STALE for the revision and all its
    derived rows. Open reassignment requests on it are SUPERSEDED. Rows already STALE keep their first stale time."""
    s.execute(
        update(role_assignments)
        .where(role_assignments.c.group_id == group_id, role_assignments.c.revision == revision,
               role_assignments.c.state == CURRENT)
        .values(state=STALE, stale_reason=reason, stale_at=now)
    )
    for table, col in (
        (team_coverages, team_coverages.c.source_assignment_revision),
        (role_interviews, role_interviews.c.source_assignment_revision),
        (role_interview_snapshots, role_interview_snapshots.c.source_assignment_revision),
        (todo_drafts, todo_drafts.c.source_assignment_revision),
        (todo_confirmations, todo_confirmations.c.source_assignment_revision),  # the trigger allows only stale_at
    ):
        s.execute(update(table).where(table.c.group_id == group_id, col == revision, table.c.stale_at.is_(None))
                  .values(stale_at=now))
    s.execute(
        update(role_reassignments)
        .where(role_reassignments.c.group_id == group_id, role_reassignments.c.source_assignment_revision == revision,
               role_reassignments.c.state == REQUESTED)
        .values(state=SUPERSEDED, decided_at=now)
    )
    log.info("stale_event group_id=%s assignment_revision=%d reason=%s", group_id, revision, reason)


def stale_for_new_brief(s: Session, group_id: str, now) -> int:
    """ProjectBrief changed (B8): the CURRENT assignment (if any) and its derived rows become STALE and every assigned
    member goes back to ranking. Returns how many members were reset. Caller holds the group's assignment lock."""
    current = AssignmentService.current(s, group_id)
    if current is not None:
        mark_assignment_stale(s, group_id, current.revision, BRIEF_CHANGED, now)
    return s.execute(
        update(member_progress)
        .where(member_progress.c.group_id == group_id, member_progress.c.status == ROLE_TODOS)
        .values(status="INTERVIEW", submission_revision=None, assignment_revision=None, updated_at=now)
    ).rowcount


def free_roles(members: list, target_user_id: str, role_ids: list[str]) -> list[str]:
    """Roles the target could move to without moving anyone else: a free place, and not the role they leave.
    Capacity is the same rule as the first assignment (one place, or ceil(members / roles) with fewer roles)."""
    cap = capacities(role_ids, len(members))
    leaving = next(m.role_id for m in members if m.user_id == target_user_id)
    used: dict[str, int] = {}
    for m in members:
        if m.user_id != target_user_id:
            used[m.role_id] = used.get(m.role_id, 0) + 1
    return [r for r in role_ids if r != leaving and cap[r] - used.get(r, 0) > 0]


def pick_new_role(
    members: list, target_user_id: str, ranking: tuple[str, str, str], role_ids: list[str], rng: random.Random
) -> tuple[str, int | None, dict]:
    """(role_id, rank or None, audit entry) for the target. `members` are the current revision's member rows."""
    free = free_roles(members, target_user_id, role_ids)
    for k, choice in enumerate(ranking):
        if choice in free:
            return choice, k + 1, {"round": "reassign", "role_id": choice, "rank": k + 1, "member": target_user_id}
    if not free:
        raise ReassignError(409, "NO_FREE_ROLE", MSG_NO_FREE_ROLE)
    role = rng.choice(free)
    return role, None, {"round": "reassign_remaining", "role_id": role, "member": target_user_id, "open_roles": free}


class ReassignmentService:
    def __init__(self, session_factory: sessionmaker[Session], assignment: AssignmentService, clock: Callable = utcnow):
        self._sf = session_factory
        self._assignment = assignment
        self._clock = clock

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _is_leader(s: Session, group_id: str, user_id: str) -> bool:
        return s.execute(
            select(group_members.c.role).where(group_members.c.group_id == group_id, group_members.c.user_id == user_id)
        ).scalar_one_or_none() == "LEADER"

    @staticmethod
    def _members(s: Session, group_id: str, revision: int) -> list:
        return s.execute(
            select(role_assignment_members).where(
                role_assignment_members.c.group_id == group_id, role_assignment_members.c.assignment_revision == revision
            )
        ).all()

    @staticmethod
    def _view(row) -> dict:
        # No role, no rank, no user id: the leader knows whom they asked about; the target knows it is them.
        return {"request_id": row.id, "state": row.state, "assignment_revision": row.source_assignment_revision}

    # ---- leader: request ----------------------------------------------------------------------

    def request(self, user_id: str, group_id: str, action_id: str, assignment_revision: int, target_user_id: str) -> tuple[bool, dict]:
        """(stored now, request view). Leader of the ACTIVE cohort only; the target must be in the CURRENT revision."""
        _action(action_id)
        with self._sf.begin() as s:
            RankingService._cohort_group(s, group_id, user_id)  # 404 for outsiders and malformed ids
            self._assignment.lock_group(s, group_id)
            if not self._is_leader(s, group_id, user_id):
                raise ReassignError(403, "FORBIDDEN", "그룹장만 재배정을 요청할 수 있어요.")
            replay = s.execute(
                select(role_reassignments).where(
                    role_reassignments.c.group_id == group_id, role_reassignments.c.requested_by == user_id,
                    role_reassignments.c.request_action_id == action_id,
                )
            ).one_or_none()
            if replay is not None:
                return False, self._view(replay)
            current = AssignmentService.current(s, group_id)
            if current is None or current.revision != assignment_revision:
                raise ReassignError(409, "STALE_ASSIGNMENT", "역할 배정이 바뀌었어요. 새로고침해서 다시 확인해 주세요.")
            members = self._members(s, group_id, current.revision)
            if target_user_id not in {m.user_id for m in members}:
                raise ReassignError(422, "INVALID_TARGET", "재배정할 팀원을 다시 골라 주세요.")
            open_req = s.execute(
                select(role_reassignments.c.id).where(
                    role_reassignments.c.group_id == group_id, role_reassignments.c.state == REQUESTED
                )
            ).first()
            if open_req is not None:
                raise ReassignError(409, "REQUEST_OPEN", "이미 답을 기다리는 재배정 요청이 있어요.")
            cand = s.execute(
                select(role_candidate_sets.c.candidates).where(
                    role_candidate_sets.c.group_id == group_id,
                    role_candidate_sets.c.source_project_brief_revision == current.source_candidate_revision,
                )
            ).scalar_one()
            # Refuse now what consent could never complete (no free role without moving someone else).
            if not free_roles(members, target_user_id, [c["id"] for c in cand]):
                raise ReassignError(409, "NO_FREE_ROLE", MSG_NO_FREE_ROLE)
            rid = str(uuid.uuid4())
            s.execute(
                insert(role_reassignments).values(
                    id=rid, group_id=group_id, source_assignment_revision=current.revision, target_user_id=target_user_id,
                    requested_by=user_id, request_action_id=action_id, state=REQUESTED, created_at=self._clock(),
                )
            )
            row = s.execute(select(role_reassignments).where(role_reassignments.c.id == rid)).one()
        log.info("reassign_event group_id=%s revision=%d state=REQUESTED", group_id, assignment_revision)
        return True, self._view(row)

    # ---- both: what concerns me ----------------------------------------------------------------

    def mine(self, user_id: str, group_id: str) -> dict:
        """Requests I must answer (as target) and requests I made (as leader). Nothing about anyone else."""
        with self._sf() as s:
            RankingService._cohort_group(s, group_id, user_id)
            incoming = s.execute(
                select(role_reassignments).where(
                    role_reassignments.c.group_id == group_id, role_reassignments.c.target_user_id == user_id,
                    role_reassignments.c.state == REQUESTED,
                )
            ).all()
            outgoing = s.execute(
                select(role_reassignments).where(
                    role_reassignments.c.group_id == group_id, role_reassignments.c.requested_by == user_id
                ).order_by(role_reassignments.c.created_at.desc()).limit(10)
            ).all()
        return {"incoming": [self._view(r) for r in incoming], "outgoing": [self._view(r) for r in outgoing]}

    # ---- target: consent ------------------------------------------------------------------------

    def respond(self, user_id: str, group_id: str, request_id: str, action_id: str, accept: bool) -> tuple[str, int | None]:
        """("accepted", new revision) | ("declined", None) | ("replay", revision or None). Target only (404 otherwise)."""
        _action(action_id)
        try:
            uuid.UUID(request_id)
        except (ValueError, TypeError):
            raise not_found() from None
        with self._sf.begin() as s:
            g = RankingService._cohort_group(s, group_id, user_id)
            self._assignment.lock_group(s, group_id)
            req = s.execute(
                select(role_reassignments).where(role_reassignments.c.id == request_id,
                                                 role_reassignments.c.group_id == group_id).with_for_update()
            ).one_or_none()
            if req is None or req.target_user_id != user_id:
                raise not_found()  # the leader cannot consent for the target, and nobody learns about others' requests
            if req.response_action_id == action_id:
                return "replay", req.result_assignment_revision
            if req.state != REQUESTED:
                raise ReassignError(409, "NOT_PENDING", "이미 처리된 재배정 요청이에요.")
            now = self._clock()
            if not accept:
                s.execute(update(role_reassignments).where(role_reassignments.c.id == request_id)
                          .values(state=DECLINED, response_action_id=action_id, decided_at=now))
                log.info("reassign_event group_id=%s state=DECLINED", group_id)
                return "declined", None
            current = AssignmentService.current(s, group_id)
            if current is None or current.revision != req.source_assignment_revision:  # cannot happen: STALE supersedes
                raise ReassignError(409, "NOT_PENDING", "이미 처리된 재배정 요청이에요.")
            new_rev = self._reassign(s, g, current, user_id, now)
            s.execute(update(role_reassignments).where(role_reassignments.c.id == request_id)
                      .values(state=ACCEPTED, response_action_id=action_id, decided_at=now,
                              result_assignment_revision=new_rev))
        log.info("reassign_event group_id=%s revision=%d state=ACCEPTED", group_id, new_rev)
        return "accepted", new_rev

    def _reassign(self, s: Session, g, current, target_user_id: str, now) -> int:
        group_id = g.id
        old = current.revision
        members = self._members(s, group_id, old)
        target = next(m for m in members if m.user_id == target_user_id)
        sub = s.execute(
            select(preference_submissions).where(
                preference_submissions.c.group_id == group_id, preference_submissions.c.user_id == target_user_id,
                preference_submissions.c.revision == target.submission_revision,
            )
        ).one()
        cand = s.execute(
            select(role_candidate_sets.c.candidates).where(
                role_candidate_sets.c.group_id == group_id,
                role_candidate_sets.c.source_project_brief_revision == current.source_candidate_revision,
            )
        ).scalar_one()
        by_id = {c["id"]: c for c in cand}
        role_id, rank, audit = pick_new_role(
            members, target_user_id, (sub.rank1, sub.rank2, sub.rank3), [c["id"] for c in cand],
            self._assignment._rng_factory(),
        )
        mark_assignment_stale(s, group_id, old, REASSIGNED, now)
        new = s.execute(
            select(func.coalesce(func.max(role_assignments.c.revision), 0)).where(role_assignments.c.group_id == group_id)
        ).scalar_one() + 1
        s.execute(
            insert(role_assignments).values(
                group_id=group_id, revision=new, source_cohort_revision=current.source_cohort_revision,
                source_candidate_revision=current.source_candidate_revision, operation="REASSIGN", state=CURRENT,
                draw_audit=[audit], created_at=now,
            )
        )
        for m in members:
            moved = m.user_id == target_user_id
            role = by_id[role_id] if moved else None
            s.execute(
                insert(role_assignment_members).values(
                    group_id=group_id, assignment_revision=new, user_id=m.user_id, submission_revision=m.submission_revision,
                    slot_id=m.slot_id, role_id=role_id if moved else m.role_id,
                    role_name=role["name"] if moved else m.role_name,
                    role_description=role.get("description", "") if moved else m.role_description,
                    assigned_rank=rank if moved else m.assigned_rank,
                )
            )
            if moved or not self._carry_interview(s, group_id, m.user_id, old, new, now):
                s.execute(insert(role_interviews).values(
                    group_id=group_id, user_id=m.user_id, source_assignment_revision=new, state="ASKING",
                    created_at=now, updated_at=now,
                ))
            s.execute(
                update(member_progress)
                .where(member_progress.c.group_id == group_id, member_progress.c.user_id == m.user_id)
                .values(status=ROLE_TODOS, assignment_revision=new, updated_at=now)
            )
        s.execute(insert(team_coverages).values(
            group_id=group_id, source_assignment_revision=new, state="PENDING", created_at=now, updated_at=now
        ))
        return new

    @staticmethod
    def _carry_interview(s: Session, group_id: str, user_id: str, old: int, new: int, now) -> bool:
        """A member whose role did not change keeps their confirmed interview input on the new revision (B8 keeps the
        MemberState). Only a finished interview is carried; an unfinished one starts again. The old rows stay STALE."""
        iv = s.execute(
            select(role_interviews).where(role_interviews.c.group_id == group_id, role_interviews.c.user_id == user_id,
                                          role_interviews.c.source_assignment_revision == old)
        ).one_or_none()
        snap = s.execute(
            select(role_interview_snapshots).where(
                role_interview_snapshots.c.group_id == group_id, role_interview_snapshots.c.user_id == user_id,
                role_interview_snapshots.c.source_assignment_revision == old)
        ).one_or_none()
        if iv is None or iv.state != DONE or snap is None:
            return False
        state = s.execute(
            select(role_member_states).where(
                role_member_states.c.group_id == group_id, role_member_states.c.user_id == user_id,
                role_member_states.c.source_assignment_revision == old,
                role_member_states.c.revision == snap.member_state_revision)
        ).one()
        s.execute(insert(role_interviews).values(
            group_id=group_id, user_id=user_id, source_assignment_revision=new, state=DONE,
            questions_asked=iv.questions_asked, state_revision=1, created_at=now, updated_at=now,
        ))
        s.execute(insert(role_member_states).values(
            group_id=group_id, user_id=user_id, source_assignment_revision=new, revision=1, state=state.state,
            source_turn_seq=state.source_turn_seq, created_at=now,
        ))
        s.execute(insert(role_interview_snapshots).values(
            group_id=group_id, user_id=user_id, source_assignment_revision=new, member_state_revision=1,
            provider_input=snap.provider_input, private_constraints=snap.private_constraints,
            deadline_limits=snap.deadline_limits, created_at=now,
        ))
        return True
