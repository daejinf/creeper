"""Read-only module contracts, scoped to an SW-authenticated stable user."""

import sqlalchemy as sa
from . import schema as d
from ..shared.contracts import ActorContext


class GoalReader:
    def __init__(self, store, actor):
        self.store = store
        self.actor = ActorContext.model_validate(actor)

    def member(self, c, gid):
        m = (
            c.execute(
                sa.select(d.members).where(
                    d.members.c.group_id == gid,
                    d.members.c.external_user_id == self.actor.external_user_id,
                )
            )
            .mappings()
            .first()
        )
        if not m:
            raise PermissionError("이 모임의 인증된 구성원이 아닙니다.")
        return m["id"]

    def groups(self):
        with self.store.read() as (c, _):
            rows = c.execute(
                sa.select(
                    d.rooms.c.id,
                    d.rooms.c.title,
                    d.rooms.c.revision,
                    d.members.c.id.label("member_id"),
                    d.goals.c.id.label("goal_id"),
                    d.goals.c.body["title"].astext.label("goal_title"),
                )
                .join(d.members, d.members.c.group_id == d.rooms.c.id)
                .outerjoin(
                    d.goals,
                    (d.goals.c.group_id == d.rooms.c.id)
                    & (d.goals.c.proposal_id == d.rooms.c.approved_proposal_id),
                )
                .where(d.members.c.external_user_id == self.actor.external_user_id)
            ).mappings()
            return {"scope": "USER", "groups": [dict(r) for r in rows]}

    def group(self, gid):
        with self.store.read(gid) as (c, row):
            return self._group(c, row, gid)

    def _group(self, c, row, gid):
        self.member(c, gid)
        goal = (
            c.execute(
                sa.select(d.goals).where(
                    d.goals.c.group_id == gid,
                    d.goals.c.proposal_id == row["approved_proposal_id"],
                )
            )
            .mappings()
            .first()
        )
        plan = (
            c.execute(
                sa.select(d.plans).where(
                    d.plans.c.group_id == gid,
                    d.plans.c.goal_id == goal["id"],
                    d.plans.c.owner.is_(None),
                )
            )
            .mappings()
            .first()
            if goal
            else None
        )
        baseline = (
            c.execute(
                sa.select(d.baselines).where(
                    d.baselines.c.plan_id == plan["id"],
                    d.baselines.c.scope == "GROUP",
                )
            )
            .mappings()
            .first()
            if plan
            else None
        )
        shares = []
        seen = set()
        if goal:
            q = (
                sa.select(
                    d.baselines.c.owner,
                    d.baselines.c.plan_id,
                    d.baselines.c.public_summary,
                    d.private.c.current_plan_id,
                    d.private.c.revision,
                    d.personal_goals.c.private_conditions[
                        "_source_private_revision"
                    ].astext.label("source_revision"),
                )
                .join(d.plans, d.plans.c.id == d.baselines.c.plan_id)
                .join(
                    d.personal_goals,
                    d.personal_goals.c.id == d.plans.c.personal_goal_id,
                )
                .outerjoin(
                    d.private,
                    (d.private.c.group_id == gid)
                    & (d.private.c.owner == d.baselines.c.owner),
                )
                .where(
                    d.baselines.c.group_id == gid,
                    d.baselines.c.scope == "PERSONAL",
                    d.plans.c.goal_id == goal["id"],
                )
                .order_by(d.baselines.c.created_at.desc())
            )
            for r in c.execute(q).mappings():
                if r["owner"] in seen:
                    continue
                seen.add(r["owner"])
                current = r["plan_id"] == r["current_plan_id"] and r[
                    "source_revision"
                ] == str(r["revision"])
                shares.append(
                    {
                        **r["public_summary"],
                        "is_current": current,
                        "requires_review": not current,
                    }
                )
        return {
            "scope": "GROUP",
            "group_id": gid,
            "revision": row["revision"],
            "goal_id": goal["id"] if goal else None,
            "goal": goal["body"] if goal else None,
            "plan_version_id": plan["id"] if plan else None,
            "plan": plan["body"] if plan else None,
            "baseline_id": baseline["id"] if baseline else None,
            "requires_review": not bool(goal and plan and baseline),
            "public_contributions": shares,
        }

    def personal(self, gid):
        with self.store.read(gid) as (c, row):
            owner = self.member(c, gid)
            view = self.store._personal_view(c, row, gid, owner, initialize=False)
            current = next(
                (p for p in view["plans"] if p["id"] == view["current_plan_id"]), None
            )
            baseline = (
                c.scalar(
                    sa.select(d.baselines.c.id).where(
                        d.baselines.c.group_id == gid,
                        d.baselines.c.plan_id == view["current_plan_id"],
                        d.baselines.c.owner == owner,
                    )
                )
                if current
                else None
            )
            return {
                "scope": "PERSONAL",
                **view,
                "plan_version_id": view["current_plan_id"],
                "personal_goal_id": current["personal_goal_id"] if current else None,
                "shared_goal_id": current["goal_id"] if current else None,
                "baseline_id": baseline,
                "is_current": bool(current and not current["stale"]),
                "requires_review": not current or bool(current["stale"]),
            }

    def plan(self, gid, pid):
        with self.store.read(gid) as (c, row):
            return self._plan(c, row, gid, pid, self.member(c, gid))

    def _plan(self, c, row, gid, pid, owner):
        p = (
            c.execute(
                sa.select(d.plans).where(d.plans.c.group_id == gid, d.plans.c.id == pid)
            )
            .mappings()
            .first()
        )
        if not p or (p["owner"] is not None and p["owner"] != owner):
            raise PermissionError("본인 또는 공동 계획만 조회할 수 있습니다.")
        goal = (
            c.execute(
                sa.select(d.goals).where(
                    d.goals.c.id == p["goal_id"], d.goals.c.group_id == gid
                )
            )
            .mappings()
            .one()
        )
        current = goal["proposal_id"] == row["approved_proposal_id"]
        if p["owner"]:
            profile = c.scalar(
                sa.select(d.personal_goals.c.private_conditions).where(
                    d.personal_goals.c.id == p["personal_goal_id"]
                )
            )
            revision = c.scalar(
                sa.select(d.private.c.revision).where(
                    d.private.c.group_id == gid, d.private.c.owner == owner
                )
            )
            current = current and profile.get("_source_private_revision") == revision
            latest = c.scalar(
                sa.select(d.private.c.current_plan_id).where(
                    d.private.c.group_id == gid, d.private.c.owner == owner
                )
            )
            current = current and latest == pid
        tasks = [
            {"task_id": r["id"], **r["body"]}
            for r in c.execute(
                sa.select(d.tasks).where(
                    d.tasks.c.group_id == gid, d.tasks.c.plan_id == pid
                )
            ).mappings()
        ]
        baseline = c.scalar(
            sa.select(d.baselines.c.id).where(
                d.baselines.c.group_id == gid, d.baselines.c.plan_id == pid
            )
        )
        return {
            "scope": "PERSONAL" if p["owner"] else "GROUP",
            "group_id": gid,
            "owner_member_id": p["owner"],
            "goal_id": p["goal_id"],
            "plan_version_id": pid,
            "baseline_id": baseline,
            "is_current": bool(current),
            "requires_review": not current,
            "created_at": p["created_at"].isoformat(),
            "plan": p["body"],
            "tasks": tasks,
        }

    def baseline(self, gid, bid, pid, tid):
        with self.store.read(gid) as (c, row):
            plan = self._plan(c, row, gid, pid, self.member(c, gid))
            baseline = (
                c.execute(
                    sa.select(d.baselines).where(
                        d.baselines.c.id == bid,
                        d.baselines.c.group_id == gid,
                        d.baselines.c.plan_id == pid,
                    )
                )
                .mappings()
                .first()
            )
            task = (
                c.execute(
                    sa.select(d.tasks).where(
                        d.tasks.c.id == tid,
                        d.tasks.c.group_id == gid,
                        d.tasks.c.plan_id == pid,
                    )
                )
                .mappings()
                .first()
            )
            if not baseline or not task:
                raise ValueError("기준·계획·과제 ID 관계가 일치하지 않습니다.")
            return {
                "scope": baseline["scope"],
                "group_id": gid,
                "owner_member_id": baseline["owner"],
                "baseline_id": bid,
                "plan_version_id": pid,
                "task_id": tid,
                "is_current": plan["is_current"],
                "requires_review": plan["requires_review"],
                "task": task["body"],
                "baseline": baseline["public_summary"],
            }

    def context(self, group_id, *, scope, plan_version_id=None):
        """Minimal downstream input, never a raw conversation or developer log.

        scope is mandatory. PERSONAL always resolves to the authenticated caller.
        An explicit historical version is returned with REVIEW_REQUIRED.
        """
        if scope not in {"GROUP", "PERSONAL"}:
            raise ValueError("scope는 GROUP 또는 PERSONAL로 명시해주세요.")
        with self.store.read(group_id) as (c, room):
            owner = self.member(c, group_id)
            result = {
                "schema_version": "1.0",
                "scope": scope,
                "group_id": group_id,
                "owner_member_id": owner if scope == "PERSONAL" else None,
                "record_revision": room["revision"],
                "goal_id": None,
                "personal_goal_id": None,
                "plan_version_id": None,
                "baseline_id": None,
                "goal": None,
                "contribution": None,
                "confirmed_conditions": {},
                "tasks": [],
                "input_versions": {},
                "is_current": False,
                "requires_review": False,
                "readiness": "NEEDS_INPUT",
                "needed_inputs": [],
            }
            private = (
                c.execute(
                    sa.select(d.private).where(
                        d.private.c.group_id == group_id, d.private.c.owner == owner
                    )
                )
                .mappings()
                .first()
                if scope == "PERSONAL"
                else None
            )
            if plan_version_id is None:
                if scope == "PERSONAL":
                    plan_version_id = private["current_plan_id"] if private else None
                else:
                    goal_id = c.scalar(
                        sa.select(d.goals.c.id).where(
                            d.goals.c.group_id == group_id,
                            d.goals.c.proposal_id == room["approved_proposal_id"],
                        )
                    )
                    plan_version_id = (
                        c.scalar(
                            sa.select(d.plans.c.id).where(
                                d.plans.c.group_id == group_id,
                                d.plans.c.goal_id == goal_id,
                                d.plans.c.owner.is_(None),
                            )
                        )
                        if goal_id
                        else None
                    )
            if plan_version_id is None:
                current_goal = (
                    c.execute(
                        sa.select(d.goals).where(
                            d.goals.c.group_id == group_id,
                            d.goals.c.proposal_id == room["approved_proposal_id"],
                        )
                    )
                    .mappings()
                    .first()
                )
                if current_goal is not None:
                    result["goal_id"] = current_goal["id"]
                    result["goal"] = current_goal["body"]
                    result["input_versions"]["proposal_id"] = current_goal[
                        "proposal_id"
                    ]
                if scope == "PERSONAL" and private:
                    result["confirmed_conditions"] = {
                        k: v
                        for k, v in private["state"].get("fields", {}).items()
                        if v.get("status") == "KNOWN"
                    }
                    result["input_versions"]["private_revision"] = private["revision"]
                result["needed_inputs"] = [
                    (
                        "확인된 공동 계획"
                        if scope == "GROUP"
                        else "본인이 검토·확인할 개인 계획"
                    )
                ]
                return result
            plan = self._plan(c, room, group_id, plan_version_id, owner)
            if plan["scope"] != scope:
                raise ValueError("요청한 scope와 계획 버전의 소유 범위가 다릅니다.")
            result.update(
                {
                    key: plan[key]
                    for key in (
                        "goal_id",
                        "plan_version_id",
                        "baseline_id",
                        "is_current",
                        "requires_review",
                    )
                }
            )
            goal = (
                c.execute(
                    sa.select(d.goals).where(
                        d.goals.c.group_id == group_id, d.goals.c.id == plan["goal_id"]
                    )
                )
                .mappings()
                .one()
            )
            result["goal"] = goal["body"]
            result["input_versions"]["proposal_id"] = goal["proposal_id"]
            result["tasks"] = plan["tasks"]
            if scope == "PERSONAL":
                personal_goal = (
                    c.execute(
                        sa.select(d.personal_goals)
                        .join(
                            d.plans, d.plans.c.personal_goal_id == d.personal_goals.c.id
                        )
                        .where(
                            d.plans.c.id == plan_version_id,
                            d.personal_goals.c.group_id == group_id,
                            d.personal_goals.c.owner == owner,
                        )
                    )
                    .mappings()
                    .one()
                )
                result["personal_goal_id"] = personal_goal["id"]
                result["contribution"] = personal_goal["body"]
                profile = personal_goal["private_conditions"]
                result["confirmed_conditions"] = {
                    k: v
                    for k, v in profile.get("fields", {}).items()
                    if v.get("status") == "KNOWN"
                }
                result["input_versions"]["private_revision"] = profile.get(
                    "_source_private_revision"
                )
            malformed_owner = any(
                task.get("owner_member_id") not in result["goal"]["roles"]
                or (scope == "PERSONAL" and task.get("owner_member_id") != owner)
                for task in result["tasks"]
            )
            from ..validation import validate_plan, confirmed_weekdays

            fields = result["confirmed_conditions"]
            try:
                days = (
                    confirmed_weekdays(fields["available_weekdays"]["value"])
                    if "available_weekdays" in fields
                    else None
                )
                validation = validate_plan(
                    plan["plan"],
                    result["goal"],
                    owner=owner if scope == "PERSONAL" else None,
                    members=set(result["goal"].get("roles", {})),
                    weekly_minutes=(
                        fields.get("weekly_minutes", {}).get("value")
                        if scope == "PERSONAL"
                        else None
                    ),
                    allowed_weekdays=days,
                )
                expected_tasks = {
                    task["task_id"]: task for task in plan["plan"].get("tasks", [])
                }
                actual_tasks = {task["task_id"]: task for task in result["tasks"]}
                if expected_tasks != actual_tasks:
                    validation["issues"].append(
                        "저장된 계획과 과제 행의 내용이 다릅니다."
                    )
                if validation["issues"]:
                    result["requires_review"] = True
                    result["needed_inputs"].extend(validation["issues"])
            except (ValueError, TypeError, KeyError):
                result["requires_review"] = True
                result["needed_inputs"].append("저장된 계획·조건의 형식 확인")
            if malformed_owner:
                result["requires_review"] = True
                result["tasks"] = []
                result["needed_inputs"].append("계획과 과제 담당자 연결 재검토")
            if not result["baseline_id"]:
                result["needed_inputs"].append(
                    "계획 내용과 공개 요약에 대한 본인 확인"
                    if scope == "PERSONAL"
                    else "공동 평가 기준 확인"
                )
            result["readiness"] = (
                "REVIEW_REQUIRED"
                if result["requires_review"]
                else "NEEDS_INPUT" if result["needed_inputs"] else "READY"
            )
            return result
