"""New information used by PLAN must survive for the next module."""

from conftest import proposal, members
from wemeet_coach import personal_coach
from wemeet_coach.m0.runtime import code_result
from wemeet_coach.shared.contracts import PersonalPlan, PersonalField


def run_plan(updates):
    group = proposal(members(1))

    class Runtime:
        def run(self, task, request, schema, instructions, check=None, **kwargs):
            plan = PersonalPlan(
                contribution="담당 초안",
                deliverable="문서",
                completion_criteria=["검토 가능"],
                tasks=group.tasks,
            )
            out = schema(
                reply="초안을 정리했습니다.", plan=plan, condition_updates=updates
            )
            check(out)
            return code_result(task, out)

    return personal_coach(
        {
            "action": "PLAN",
            "goal": group.goal,
            "owner_member_id": "u0",
            "state": {
                "fields": {
                    "level": {"value": "일부 경험", "status": "KNOWN"},
                    "weekly_minutes": {"value": 60, "status": "KNOWN"},
                    "scope": {"value": "담당 문서", "status": "KNOWN"},
                }
            },
            "conversation": [
                {
                    "message_id": "new",
                    "speaker_id": "u0",
                    "text": "이 작업은 Python으로 할게요. 주간 시간은 30분으로 바꿔주세요.",
                }
            ],
        },
        runtime=Runtime(),
    )


def test_new_optional_condition_is_saved_in_returned_state():
    out = run_plan(
        {
            "tools": PersonalField(
                value=["Python"], status="KNOWN", evidence_refs=["new"]
            )
        }
    )
    assert out.status == "OK" and out.data.state.fields["tools"].value == ["Python"]
    assert "new" in out.data.state.processed_message_ids


def test_changed_confirmed_budget_requests_review_and_keeps_source():
    out = run_plan(
        {
            "weekly_minutes": PersonalField(
                value=30, status="KNOWN", evidence_refs=["new"]
            )
        }
    )
    assert out.status == "NEEDS_INPUT" and out.data.plan is None
    assert out.data.state.fields["weekly_minutes"].value == 30 and out.needed_inputs
