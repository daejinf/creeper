"""Server role assignment (D-A5, D-B1 B4/B6): preferences first, server random only for ties and exhaustion.

The algorithm is pure and deterministic given its RNG. Rounds 1, 2, 3 offer each still-unassigned member their k-th
choice; a role that has more contenders than free places picks winners with the server RNG and the losers move to
their next choice. Whoever has no role after round 3 gets a random role among those with free places. No AI is
involved, and no caller can choose the winner. The product RNG is `random.SystemRandom()` (no seed); tests inject a
seeded `random.Random`.

A role normally has one place. Only when there are fewer roles than members (the candidate generator guarantees at
least as many candidates as members, so this happens with fixtures only) each role gets ceil(members / roles) places
and the members of one role are told apart by their slot.

The result is stored once as an immutable assignment revision inside the transaction that stores the last ranking
submission, under a per-group lock, so a duplicate request, a refresh or a concurrent submit never draws again.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from ..models import (
    cohort_members,
    member_progress,
    preference_submissions,
    role_assignment_members,
    role_assignments,
    role_candidate_sets,
    role_interviews,
    team_coverages,
)

log = logging.getLogger("moim.assignment")

CURRENT, STALE = "CURRENT", "STALE"
WAITING_ASSIGNMENT, ROLE_TODOS = "WAITING_ASSIGNMENT", "ROLE_TODOS"


@dataclass(frozen=True)
class Placement:
    user_id: str
    role_id: str
    rank: int | None  # 1..3, or None for a random role after the three choices ran out
    slot_id: str


def capacities(role_ids: list[str], n_members: int) -> dict[str, int]:
    per_role = 1 if len(role_ids) >= n_members else math.ceil(n_members / len(role_ids))
    return {r: per_role for r in role_ids}


def assign_roles(
    rankings: dict[str, tuple[str, str, str]], role_ids: list[str], rng: random.Random
) -> tuple[list[Placement], list[dict]]:
    """(placements, draw audit). `rankings` maps user id -> (1st, 2nd, 3rd) candidate ids; `role_ids` in candidate order."""
    if not rankings or not role_ids:
        raise ValueError("nothing to assign")
    members = sorted(rankings)  # a fixed order, so the same RNG state gives the same result
    free = capacities(role_ids, len(members))
    taken: dict[str, tuple[str, int | None]] = {}
    audit: list[dict] = []
    for k in range(3):
        contenders: dict[str, list[str]] = {}
        for uid in members:
            if uid in taken:
                continue
            choice = rankings[uid][k]
            if choice in free:
                contenders.setdefault(choice, []).append(uid)
        for role in role_ids:  # candidate order, so the draw order does not depend on dict order
            who = contenders.get(role)
            if not who or free[role] == 0:
                continue
            if len(who) <= free[role]:
                winners = who
            else:
                winners = sorted(rng.sample(who, free[role]))
                audit.append({"round": k + 1, "role_id": role, "contenders": who, "winners": winners})
            for uid in winners:
                taken[uid] = (role, k + 1)
            free[role] -= len(winners)
    left = [uid for uid in members if uid not in taken]
    if left:
        rng.shuffle(left)
        for uid in left:
            open_roles = [r for r in role_ids if free[r] > 0]
            role = rng.choice(open_roles)
            taken[uid] = (role, None)
            free[role] -= 1
            audit.append({"round": "remaining", "role_id": role, "member": uid, "open_roles": open_roles})
    # Slots: role order, then member order inside a role. Ids are anonymous ("s1"...) and only the server maps them.
    placements = []
    n = 0
    for role in role_ids:
        for uid in members:
            if taken[uid][0] == role:
                n += 1
                placements.append(Placement(uid, role, taken[uid][1], f"s{n}"))
    return placements, audit


class AssignmentService:
    def __init__(self, rng_factory: Callable[[], random.Random] = random.SystemRandom, clock=None):
        self._rng_factory = rng_factory
        self._clock = clock

    @staticmethod
    def lock_group(s: Session, group_id: str) -> None:
        s.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"assignment:{group_id}"})

    @staticmethod
    def current(s: Session, group_id: str):
        return s.execute(
            select(role_assignments).where(role_assignments.c.group_id == group_id, role_assignments.c.state == CURRENT)
        ).one_or_none()

    def assign_if_ready(self, s: Session, g, now) -> int | None:
        """Inside the caller's transaction (which holds `lock_group`): make the assignment revision if every member of
        the ACTIVE cohort has a WAITING_ASSIGNMENT submission for the current candidate revision. Returns the new
        revision, or None when it is not time yet or one already exists."""
        group_id = g.id
        if self.current(s, group_id) is not None:
            return None
        # The current active members only: someone who left the group (D-D1 D6) is never waited for.
        from ..groups.service import active_cohort_query  # local: groups.service imports this module lazily too

        cohort = [r[0] for r in s.execute(active_cohort_query(group_id, g.cohort_revision))]
        rows = s.execute(
            select(member_progress.c.user_id, member_progress.c.submission_revision, preference_submissions)
            .join(
                preference_submissions,
                (preference_submissions.c.group_id == member_progress.c.group_id)
                & (preference_submissions.c.user_id == member_progress.c.user_id)
                & (preference_submissions.c.revision == member_progress.c.submission_revision),
            )
            .where(member_progress.c.group_id == group_id, member_progress.c.status == WAITING_ASSIGNMENT)
        ).all()
        subs = {r.user_id: r for r in rows if r.user_id in cohort and r.source_candidate_revision == g.brief_revision}
        if not cohort or set(subs) != set(cohort):
            return None
        cand = s.execute(
            select(role_candidate_sets.c.state, role_candidate_sets.c.candidates).where(
                role_candidate_sets.c.group_id == group_id,
                role_candidate_sets.c.source_project_brief_revision == g.brief_revision,
            )
        ).one_or_none()
        if cand is None or cand.state != "SUCCEEDED" or not cand.candidates:
            return None
        by_id = {c["id"]: c for c in cand.candidates}
        placements, audit = assign_roles(
            {uid: (r.rank1, r.rank2, r.rank3) for uid, r in subs.items()}, [c["id"] for c in cand.candidates], self._rng_factory()
        )
        revision = (
            s.execute(
                select(func.coalesce(func.max(role_assignments.c.revision), 0)).where(role_assignments.c.group_id == group_id)
            ).scalar_one()
            + 1
        )
        s.execute(
            insert(role_assignments).values(
                group_id=group_id, revision=revision, source_cohort_revision=g.cohort_revision,
                source_candidate_revision=g.brief_revision, operation="INITIAL", state=CURRENT, draw_audit=audit, created_at=now,
            )
        )
        for p in placements:
            role = by_id[p.role_id]
            s.execute(
                insert(role_assignment_members).values(
                    group_id=group_id, assignment_revision=revision, user_id=p.user_id,
                    submission_revision=subs[p.user_id].revision, slot_id=p.slot_id, role_id=p.role_id,
                    role_name=role["name"], role_description=role.get("description", ""), assigned_rank=p.rank,
                )
            )
            s.execute(
                insert(role_interviews).values(
                    group_id=group_id, user_id=p.user_id, source_assignment_revision=revision, state="ASKING",
                    created_at=now, updated_at=now,
                )
            )
            s.execute(
                update(member_progress)
                .where(member_progress.c.group_id == group_id, member_progress.c.user_id == p.user_id)
                .values(status=ROLE_TODOS, assignment_revision=revision, updated_at=now)
            )
        s.execute(
            insert(team_coverages).values(
                group_id=group_id, source_assignment_revision=revision, state="PENDING", created_at=now, updated_at=now
            )
        )
        # Metadata only: never who got which role, never a ranking.
        log.info(
            "assignment_event group_id=%s revision=%d members=%d random_draws=%d state=CURRENT",
            group_id, revision, len(placements), len(audit),
        )
        return revision

    @staticmethod
    def mine(s: Session, group_id: str, user_id: str, revision: int):
        return s.execute(
            select(role_assignment_members).where(
                role_assignment_members.c.group_id == group_id,
                role_assignment_members.c.assignment_revision == revision,
                role_assignment_members.c.user_id == user_id,
            )
        ).one_or_none()

    @staticmethod
    def team_table(s: Session, group_id: str, revision: int) -> list[dict]:
        """The nameless final assignment table: slot, role name and description. No user id, name or rank."""
        rows = s.execute(
            select(role_assignment_members.c.slot_id, role_assignment_members.c.role_name, role_assignment_members.c.role_description)
            .where(role_assignment_members.c.group_id == group_id, role_assignment_members.c.assignment_revision == revision)
        ).all()
        rows = sorted(rows, key=lambda r: int(r.slot_id[1:]))
        return [{"slot_id": r.slot_id, "role": r.role_name, "description": r.role_description} for r in rows]
