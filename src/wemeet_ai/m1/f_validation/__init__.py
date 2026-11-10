"""M1-F: scope-specific deterministic validation, including externally authored plans."""
from datetime import timedelta
from pydantic import Field
from ...shared.contracts import Contract, GoalContext, Plan, Issue, ValidationReport
from ...m0 import Runtime
from ...m0.runtime import code_result
from ...shared.plans import assign_ids

class ValidationRequest(Contract):
    scope: str = "GROUP"
    group_plan: Plan | None = None
    member_plans: list[Plan] = Field(default_factory=list)
    context: GoalContext | None = None
    weekly_budgets: dict[str, int] = Field(default_factory=dict)
    expected_members: list[str] = Field(default_factory=list)

def inspect_plan(plan, context=None, budgets=None):
    issues = []
    def fail(code, path, message): issues.append(Issue(code=code, field_path=path, message=message))
    if plan.start_date >= plan.end_date: fail("INVALID_DATES", "end_date", "시작일보다 늦은 마감일이 필요합니다.")
    import math
    weeks = math.ceil(((plan.end_date-plan.start_date).days+1)/7)
    if len(plan.weekly_outcomes)>weeks: fail("WEEK_OUTSIDE_PERIOD", "weekly_outcomes", "계획 기간보다 주차가 많습니다.")
    if context:
        for field in ("goal", "start_date", "end_date"):
            expected = getattr(context, field)
            if expected and expected != getattr(plan, field): fail("CONFIRMED_VALUE_CHANGED", field, "확인된 공동 조건이 달라졌습니다.")
        for i, expected in enumerate(context.milestones):
            due, title = expected.get("due_date"), expected.get("title")
            if due and title and not any(m.due_date.isoformat() == str(due) and (title in m.title or m.title in title) for m in plan.milestones):
                fail("MISSING_CONFIRMED_MILESTONE", f"milestones.{i}", "확인된 중간 일정이 보존되지 않았습니다.")
    ids = [t.task_id for t in plan.tasks if t.task_id]
    if len(set(ids)) != len(ids): fail("DUPLICATE_TASK", "tasks", "중복 과제 ID입니다.")
    minutes = {}
    for i, task in enumerate(plan.tasks):
        if not plan.start_date <= task.due_date <= plan.end_date: fail("DATE_OUTSIDE_PERIOD", f"tasks.{i}.due_date", "기간 밖 과제입니다.")
        if plan.owner_member_id and task.owner_member_id != plan.owner_member_id: fail("OWNER_MISMATCH", f"tasks.{i}.owner_member_id", "다른 사람의 과제가 포함됐습니다.")
        if any(d not in ids or d == task.task_id for d in task.dependencies): fail("INVALID_DEPENDENCY", f"tasks.{i}.dependencies", "의존 과제 ID를 확인해주세요.")
        if context and context.completion_criteria and not task.completion_criteria: fail("MISSING_CRITERIA", f"tasks.{i}", "완료 기준이 필요합니다.")
        owner = task.owner_member_id or plan.owner_member_id
        week = (task.due_date - plan.start_date).days // 7
        minutes[owner, week] = minutes.get((owner, week), 0) + task.estimated_minutes
    for (owner, week), total in minutes.items():
        if budgets and owner in budgets and total > budgets[owner]: fail("TIME_BUDGET_EXCEEDED", f"week.{week+1}", "주간 시간 예산을 초과했습니다.")
    for i, milestone in enumerate(plan.milestones):
        due = milestone.due_date
        if due:
            from datetime import date
            try:
                if not plan.start_date <= date.fromisoformat(str(due)) <= plan.end_date: raise ValueError()
            except ValueError: fail("INVALID_MILESTONE", f"milestones.{i}", "마일스톤 날짜를 확인해주세요.")
    graph = {t.task_id: t.dependencies for t in plan.tasks if t.task_id}
    visited, active = set(), set()
    def cycle(node):
        if node in active: return True
        if node in visited: return False
        active.add(node)
        if any(cycle(child) for child in graph.get(node, []) if child in graph): return True
        active.remove(node)
        visited.add(node)
        return False
    if any(cycle(node) for node in graph): fail("DEPENDENCY_CYCLE", "tasks.dependencies", "과제 의존 관계가 순환합니다.")
    return issues

def validate_plan(request: ValidationRequest):
    if request.scope not in {"GROUP", "MEMBER", "BUNDLE"}: return code_result("F", needed=["scope: GROUP/MEMBER/BUNDLE"])
    plans = ([request.group_plan] if request.group_plan else []) if request.scope == "GROUP" else request.member_plans if request.scope == "MEMBER" else ([request.group_plan] if request.group_plan else []) + request.member_plans
    if not plans: return code_result("F", needed=["검사할 계획을 입력해주세요."])
    issues = []
    for plan in plans: issues.extend(inspect_plan(plan, request.context, request.weekly_budgets))
    owners = [p.owner_member_id for p in request.member_plans]
    if len(owners) != len(set(owners)): issues.append(Issue(code="DUPLICATE_MEMBER", field_path="member_plans", message="같은 사람의 계획이 중복됐습니다."))
    unchecked = []
    if not request.context: unchecked.append("확인된 목표·기간과의 비교")
    if not request.weekly_budgets and request.member_plans: unchecked.append("개인 시간 예산")
    if request.scope != "BUNDLE": unchecked.append("전체 구성원 계획 완성 여부")
    elif not request.expected_members: unchecked.append("전체 구성원 목록")
    else:
        if not request.group_plan: issues.append(Issue(code="MISSING_GROUP", field_path="group_plan", message="공동 계획이 없습니다."))
        if set(owners) != set(request.expected_members): issues.append(Issue(code="MEMBER_COVERAGE", field_path="member_plans", message="전체 구성원 계획을 확인해주세요."))
    unchecked.append("서비스 권한·최종 승인 및 의미적 품질 검토")
    report = ValidationReport(valid=not issues, scope=request.scope, issues=issues, not_checked=unchecked, integration_ready=False)
    return code_result("F", report)

class RevisionRequest(Contract):
    plan: Plan
    context: GoalContext
    feedback: str = Field(min_length=1)
    weekly_budget: int | None = Field(default=None, gt=0)

def revise_plan(request: RevisionRequest, *, runtime: Runtime | None = None, fresh=False):
    from uuid import uuid4
    from ...m0 import AIError
    def check(plan):
        if plan.owner_member_id != request.plan.owner_member_id:
            raise AIError("OWNER_MISMATCH", "수정 시 계획 소유자를 바꾸지 마세요.")
        issues = inspect_plan(plan, request.context, {plan.owner_member_id: request.weekly_budget} if request.weekly_budget else None)
        if issues: raise AIError("PLAN_INVALID", str([i.model_dump() for i in issues]))
        assign_ids(plan, request.plan.plan_version_id)
    result = (runtime or Runtime()).run("F.revise", request, Plan, "수정 요청을 반영하되 확인된 목표·기간·시간 예산을 지키세요. 공개 과제와 실행 방법만 작성하고 개인 사유는 설명하지 마세요.", check=check, fresh=fresh)
    return result

__all__ = ["ValidationRequest", "RevisionRequest", "validate_plan", "revise_plan"]
