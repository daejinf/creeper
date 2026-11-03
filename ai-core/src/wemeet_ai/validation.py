from datetime import timedelta

from .contracts import GroupPlan, GoalSpec, GroupGoalSpec, Issue, MemberPlanDraft, ValidationReport


def validate_plan(goal_spec: GoalSpec | GroupGoalSpec, group_plan: GroupPlan, member_plans: list[MemberPlanDraft], *, require_all_members=True, member_profiles=None) -> ValidationReport:
    issues = []
    def error(code, path, message, member=None):
        issues.append(Issue(code=code, field_path=path, message=message, member_id=member))

    if group_plan.spec_id != goal_spec.spec_id: error("SPEC_MISMATCH", "group_plan.spec_id", "Plan belongs to another goal specification")
    for field in ("goal", "expected_outcome", "completion_criteria", "start_date", "end_date", "frequency_per_week", "metric"):
        if getattr(goal_spec, field) != getattr(group_plan, field): error("CONFIRMED_VALUE_CHANGED", "group_plan." + field, "Confirmed value changed")
    from .calendar_context import weekday_errors
    for path,message in weekday_errors(group_plan.model_dump(mode='json'),goal_spec.start_date):error('CALENDAR_WEEKDAY_MISMATCH',path,message)
    refs = set(goal_spec.evidence_ids)
    def check_refs(values, path, member=None):
        if not values or not set(values) <= refs: error("INVALID_EVIDENCE", path, "Missing or unknown evidence references", member)
    check_refs(group_plan.evidence_refs, "group_plan.evidence_refs")
    for index, milestone in enumerate(group_plan.milestones):
        if not goal_spec.start_date <= milestone.due_date <= goal_spec.end_date: error("DATE_OUTSIDE_PERIOD", f"group_plan.milestones.{index}.due_date", "Milestone outside confirmed dates")
        check_refs(milestone.evidence_refs, f"group_plan.milestones.{index}.evidence_refs")
    for milestone in goal_spec.milestones:
        due = str(milestone.get("due_date", ""))
        title = milestone.get("title")
        if due and title and not any(m.due_date.isoformat() == due and (title in m.title or m.title in title) for m in group_plan.milestones):
            error("CONFIRMED_MILESTONE_CHANGED", "group_plan.milestones", f"Confirmed milestone must be preserved: {title} / {due}")
    weeks = (goal_spec.end_date - goal_spec.start_date).days // 7 + 1
    if sorted(w.week for w in group_plan.weekly_outline) != list(range(1, weeks + 1)):
        error("WEEK_COVERAGE", "group_plan.weekly_outline", f"weekly_outline.week must be {list(range(1, weeks + 1))}; received {[w.week for w in group_plan.weekly_outline]}. Weeks start at {goal_spec.start_date} in 7-day blocks, not calendar Mondays.")
    source_profiles = goal_spec.member_profiles if isinstance(goal_spec, GoalSpec) else (member_profiles or [])
    profiles = {p.member_id: p for p in source_profiles}
    expected_ids = set(profiles) if isinstance(goal_spec, GoalSpec) else {m.member_id for m in goal_spec.members}
    if len(profiles) != len(source_profiles) or set(profiles) - expected_ids:
        error("INVALID_PROFILES", "member_profiles", "Profiles must be unique and belong to the group")
    if require_all_members and set(profiles) != expected_ids:
        error("PROFILE_COVERAGE", "member_profiles", "구성원별 확인된 개인 조건이 필요합니다.")
    actual_ids = [m.public_plan.member_id for m in member_plans]
    if len(actual_ids) != len(set(actual_ids)): error("DUPLICATE_MEMBER", "member_plans", "Duplicate member plan")
    if require_all_members and set(actual_ids) != expected_ids: error("MEMBER_COVERAGE", "member_plans", "Member coverage does not match specification")
    for index, draft in enumerate(member_plans):
        plan = draft.public_plan
        member_id = plan.member_id
        path = f"member_plans.{index}.public_plan"
        profile = profiles.get(member_id)
        contribution = draft.contribution_goal
        if contribution:
            if contribution.owner_member_id != member_id or contribution.group_plan_id != group_plan.plan_id or contribution.review_status not in {"ACCEPTED", "EDITED"}:
                error("CONTRIBUTION_NOT_REVIEWED", path + ".contribution_goal", "기여 목표의 소유자·공동 계획·검토 상태를 확인해주세요.", member_id)
            check_refs(contribution.evidence_refs, path + ".contribution_goal.evidence_refs", member_id)
        if profile is None:
            error("UNKNOWN_MEMBER", path + ".member_id", "Unknown member", member_id); continue
        if plan.group_plan_id != group_plan.plan_id: error("GROUP_PLAN_MISMATCH", path + ".group_plan_id", "Member plan belongs to another group draft", member_id)
        check_refs(plan.evidence_refs, path + ".evidence_refs", member_id)
        if sorted(w.week for w in plan.weekly_targets) != list(range(1, weeks + 1)): error("WEEK_COVERAGE", path + ".weekly_targets", "Member targets must cover every week once", member_id)
        for target in plan.weekly_targets:
            if target.estimated_minutes > profile.weekly_available_minutes: error("TIME_BUDGET_EXCEEDED", path + ".weekly_targets", "Weekly target exceeds available minutes", member_id)
        week_one = next((w for w in plan.weekly_targets if w.week == 1), None)
        minutes = sum(t.estimated_minutes for t in plan.first_week_todos)
        task_keys = [(t.scheduled_date, t.action.strip(), t.amount_or_deliverable.strip()) for t in plan.first_week_todos]
        task_ids=[t.task_id for t in plan.first_week_todos if t.task_id]
        if len(task_ids)!=len(set(task_ids)):error('DUPLICATE_TASK_ID',path+'.first_week_todos','Task IDs must be unique',member_id)
        if len(task_keys) != len(set(task_keys)): error("DUPLICATE_TASK", path + ".first_week_todos", "Identical tasks were repeated on the same date", member_id)
        if minutes > profile.weekly_available_minutes or (week_one and minutes > week_one.estimated_minutes): error("TIME_BUDGET_EXCEEDED", path + ".first_week_todos", "First-week tasks exceed declared weekly budget", member_id)
        for todo_index, todo in enumerate(plan.first_week_todos):
            first_end = min(goal_spec.end_date, goal_spec.start_date + timedelta(days=6))
            if not goal_spec.start_date <= todo.scheduled_date <= first_end: error("INVALID_FIRST_WEEK_DATE", f"{path}.first_week_todos.{todo_index}.scheduled_date", f"scheduled_date={todo.scheduled_date}; allowed dates are {goal_spec.start_date} through {first_end}. Remove second-week tasks from first_week_todos.", member_id)
            check_refs(todo.evidence_refs, f"{path}.first_week_todos.{todo_index}.evidence_refs", member_id)
        if goal_spec.frequency_per_week is not None:
            expected = min(goal_spec.frequency_per_week, (goal_spec.end_date - goal_spec.start_date).days + 1)
            actual_days = len({t.scheduled_date for t in plan.first_week_todos})
            if actual_days != expected: error("FREQUENCY_MISMATCH", path + ".first_week_todos", f"Exactly {expected} distinct first-week activity dates are required; received {actual_days}.", member_id)
        if goal_spec.metric:
            amounts = [w.target_amount for w in plan.weekly_targets]
            if any(v is None for v in amounts): error("METRIC_MISSING", path + ".weekly_targets", "Numeric goals require numeric weekly targets", member_id)
            elif goal_spec.metric.allocation == "PER_MEMBER" and abs(sum(amounts) - goal_spec.metric.target) > 1e-6: error("METRIC_TOTAL_MISMATCH", path + ".weekly_targets", "Member target total differs from confirmed target", member_id)
    if require_all_members and goal_spec.metric and goal_spec.metric.allocation == "GROUP_TOTAL" and set(actual_ids) == expected_ids:
        total = sum(w.target_amount or 0 for d in member_plans for w in d.public_plan.weekly_targets)
        if abs(total - goal_spec.metric.target) > 1e-6: error("METRIC_TOTAL_MISMATCH", "member_plans", "Group member target sum differs from confirmed target")
    # Defense in depth: actual private strings may not appear in any public plan.
    public_text = group_plan.model_dump_json() + " ".join(p.public_plan.model_dump_json() + (p.contribution_goal.model_dump_json() if p.contribution_goal else "") for p in member_plans)
    for profile in profiles.values():
        for constraint in profile.constraints:
            if len(constraint.strip()) >= 4 and constraint in public_text: error("PRIVATE_CONTENT_EXPOSED", "public_output", "Private constraint appeared in public output", profile.member_id)
    return ValidationReport(valid=not issues, issues=issues)
