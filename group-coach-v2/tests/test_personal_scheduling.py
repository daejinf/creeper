"""A deadline is not necessarily the day on which the person works."""

from datetime import date
from conftest import proposal, members
from wemeet_coach.validation import validate_plan, time_allocation
from wemeet_coach.shared.contracts import PersonalPlan


def draft():
    g = proposal(members(1)).goal
    task = proposal(members(1)).tasks[0]
    task.scheduled_date = date(2026, 11, 8)  # Sunday
    task.due_date = date(2026, 11, 12)  # Thursday deadline
    task.estimated_minutes = 45
    p = PersonalPlan(
        contribution="초안",
        deliverable="검토 기록",
        completion_criteria=["검토 가능"],
        tasks=[task],
    )
    return g, p


def test_explicit_availability_checks_work_day_not_deadline():
    g, p = draft()
    assert validate_plan(p, g, owner="u0", weekly_minutes=60, allowed_weekdays={6})[
        "valid"
    ]
    p.tasks[0].scheduled_date = date(2026, 11, 9)
    assert not validate_plan(p, g, owner="u0", weekly_minutes=60, allowed_weekdays={6})[
        "valid"
    ]


def test_work_cannot_be_scheduled_after_its_deadline():
    g, p = draft()
    p.tasks[0].scheduled_date = date(2026, 11, 13)
    assert not validate_plan(p, g)["valid"]


def test_old_plan_without_work_date_still_validates():
    g, p = draft()
    p.tasks[0].scheduled_date = None
    assert validate_plan(p, g, owner="u0", weekly_minutes=60, allowed_weekdays={3})[
        "valid"
    ]


def test_weekly_budget_and_authoritative_totals_use_work_dates():
    g, p = draft()
    second = p.tasks[0].model_copy(deep=True)
    second.task_id = "second"
    second.scheduled_date = date(2026, 11, 10)
    second.due_date = date(2026, 11, 20)
    p.tasks.append(second)
    assert not validate_plan(p, g, weekly_minutes=60)["valid"]
    assert time_allocation(p, g, 60) == {
        "total_minutes": 90,
        "weekly_limit_minutes": 60,
        "weeks": [{"week": 1, "planned_minutes": 90}],
    }


def test_button_and_free_input_minutes_share_normalization():
    from wemeet_coach.personal import normalize

    assert (
        normalize("weekly_minutes", "2시간")
        == normalize("weekly_minutes", {"minutes": 120})
        == 120
    )
