"""Deterministic date/owner/budget checks, adapted from original M1-F."""

from collections import defaultdict
from .shared.contracts import PersonalPlan, GroupProposal, Goal
from pydantic import ValidationError


def confirmed_weekdays(values):
    """Explicit availability only. Preferences are never scheduling prohibitions."""
    if not isinstance(values, list) or any(
        type(v) != int or v not in range(7) for v in values
    ):
        raise ValueError("가능 요일은 월0~일6의 정수 목록이어야 합니다.")
    return set(values)


def validate_plan(
    plan, goal, *, owner=None, weekly_minutes=None, members=None, allowed_weekdays=None
):
    try:
        goal = Goal.model_validate(
            goal.model_dump(mode="python") if isinstance(goal, Goal) else goal
        )
        raw = (
            plan.model_dump(mode="python")
            if isinstance(plan, (GroupProposal, PersonalPlan))
            else plan
        )
        schema = (
            GroupProposal if isinstance(raw, dict) and "goal" in raw else PersonalPlan
        )
        plan = schema.model_validate(raw)
    except ValidationError as exc:
        return {
            "valid": False,
            "issues": [
                ".".join(map(str, e["loc"])) + ": " + e["msg"]
                for e in exc.errors(include_input=False, include_url=False)
            ],
            "not_checked": ["계획 형식 오류로 실행 조건 미검사"],
        }
    issues = []
    ids = {t.task_id for t in plan.tasks if t.task_id}
    minutes = defaultdict(int)
    task_dates = {
        t.task_id: (t.scheduled_date or t.due_date) for t in plan.tasks if t.task_id
    }
    if weekly_minutes is not None and (
        type(weekly_minutes) != int or weekly_minutes <= 0
    ):
        issues.append("주간 시간 예산은 양의 정수여야 합니다.")
    for i, m in enumerate(getattr(plan, "milestones", [])):
        if not goal.start_date <= m.due_date <= goal.end_date:
            issues.append(f"milestones.{i}: 기간 밖 일정")
    if len(ids) != sum(bool(t.task_id) for t in plan.tasks):
        issues.append("중복 과제 ID")
    for i, t in enumerate(plan.tasks):
        work_date = t.scheduled_date or t.due_date
        if not goal.start_date <= work_date <= t.due_date:
            issues.append(f"tasks.{i}.scheduled_date: 기간 밖이거나 기한 이후인 수행일")
        if not goal.start_date <= t.due_date <= goal.end_date:
            issues.append(f"tasks.{i}: 기간 밖 일정")
        if allowed_weekdays is not None and work_date.weekday() not in allowed_weekdays:
            issues.append(f"tasks.{i}: 확인된 작업 요일과 다릅니다 ({work_date})")
        if owner and t.owner_member_id != owner:
            issues.append(f"tasks.{i}: 담당자 불일치")
        if members is not None and t.owner_member_id not in members:
            issues.append(f"tasks.{i}: 담당자를 구성원 중에서 지정해야 합니다.")
        if any(d not in ids or d == t.task_id for d in t.dependencies):
            issues.append(
                f"tasks.{i}: 잘못된 의존 관계. 각 tasks에 고유 task_id를 채우고 dependencies는 같은 응답의 task_id만 사용하세요."
            )
        if any(d in task_dates and task_dates[d] > work_date for d in t.dependencies):
            issues.append(f"tasks.{i}: 선행 작업 완료일보다 이른 의존 작업 일정")
        minutes[(work_date - goal.start_date).days // 7] += t.estimated_minutes
    if (
        type(weekly_minutes) == int
        and weekly_minutes > 0
        and any(v > weekly_minutes for v in minutes.values())
    ):
        issues.append("주간 시간 예산 초과")
    graph = {t.task_id: t.dependencies for t in plan.tasks if t.task_id}
    active = set()
    done = set()

    def cycle(n):
        if n in active:
            return True
        if n in done:
            return False
        active.add(n)
        if any(cycle(v) for v in graph.get(n, [])):
            return True
        active.remove(n)
        done.add(n)
        return False

    if any(cycle(n) for n in graph):
        issues.append("의존 관계 순환")
    return {
        "valid": not issues,
        "issues": issues,
        "not_checked": ["의미적 품질·서비스 인증"],
    }


def assign_ids(plan):
    from uuid import uuid4

    ids = {t.task_id: str(uuid4()) for t in plan.tasks if t.task_id}
    for task in plan.tasks:
        task.task_id = ids.get(task.task_id, str(uuid4()))
        task.dependencies = [ids.get(d, d) for d in task.dependencies]
    if isinstance(plan, PersonalPlan):
        plan.plan_version_id = str(uuid4())


def time_allocation(plan, goal, weekly_minutes):
    """Authoritative arithmetic for adapters; model prose is not an accounting source."""
    weeks = defaultdict(int)
    for task in plan.tasks:
        day = task.scheduled_date or task.due_date
        weeks[(day - goal.start_date).days // 7 + 1] += task.estimated_minutes
    return {
        "total_minutes": sum(weeks.values()),
        "weekly_limit_minutes": weekly_minutes,
        "weeks": [
            {"week": week, "planned_minutes": count}
            for week, count in sorted(weeks.items())
        ],
    }
