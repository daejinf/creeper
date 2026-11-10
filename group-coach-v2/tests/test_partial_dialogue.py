"""A bad optional model question must not erase a valid private answer."""

from conftest import proposal, members
from wemeet_coach import personal_coach
from wemeet_coach.m0.runtime import code_result


def test_repeated_question_is_omitted_not_a_failed_dialogue():
    g = proposal(members(1))

    class Runtime:
        def run(self, task, request, schema, instructions, check=None, **kwargs):
            out = schema(
                reply="확인한 시간으로 담당 작업을 정리하겠습니다.",
                state=request.state.model_copy(deep=True),
            )
            from wemeet_coach.shared.contracts import Question

            out.state.question = Question.model_validate(
                {
                    "field": "weekly_minutes",
                    "text": "몇 분 가능하나요?",
                    "reason": "시간 배분",
                    "options": [],
                }
            )
            out = schema.model_validate(out.model_dump())
            check(out)
            return code_result(task, out)

    out = personal_coach(
        {
            "goal": g.goal,
            "owner_member_id": "u0",
            "conversation": [
                {"message_id": "m1", "speaker_id": "u0", "text": "주 60분이야"}
            ],
            "state": {
                "fields": {
                    "weekly_minutes": {
                        "value": 60,
                        "status": "KNOWN",
                        "evidence_refs": ["m1"],
                    }
                },
                "processed_message_ids": ["m1"],
            },
        },
        runtime=Runtime(),
    )
    assert out.status == "OK" and out.data.state.question is None
    assert out.data.state.fields["weekly_minutes"].value == 60 and out.warnings


def test_unknown_extra_field_is_not_stored_but_valid_conditions_survive():
    g = proposal(members(1))

    class Runtime:
        def run(self, task, request, schema, instructions, check=None, **kwargs):
            out = schema(
                reply="확인한 조건은 유지합니다.",
                state={
                    "fields": {
                        "unexpected_personal_field": {
                            "value": "추정",
                            "status": "SUGGESTED",
                        }
                    }
                },
            )
            check(out)
            return code_result(task, out)

    out = personal_coach(
        {
            "goal": g.goal,
            "owner_member_id": "u0",
            "conversation": [
                {"message_id": "m1", "speaker_id": "u0", "text": "주 60분"}
            ],
            "state": {
                "fields": {
                    "weekly_minutes": {
                        "value": 60,
                        "status": "KNOWN",
                        "evidence_refs": ["m1"],
                    }
                }
            },
        },
        runtime=Runtime(),
    )
    assert (
        out.status == "OK" and "unexpected_personal_field" not in out.data.state.fields
    )
    assert out.data.state.fields["weekly_minutes"].value == 60 and out.warnings


def test_public_dependency_closure_contains_no_other_private_data():
    from wemeet_coach.persistence import Store

    tasks = [
        {"task_id": "a", "owner_member_id": "u0", "dependencies": ["b"]},
        {"task_id": "b", "owner_member_id": "u1", "dependencies": ["c"]},
        {"task_id": "c", "owner_member_id": "u2", "dependencies": []},
        {"task_id": "unrelated", "owner_member_id": "u1", "dependencies": []},
    ]
    assert [t["task_id"] for t in Store.public_dependencies(tasks, "u0")] == ["b", "c"]
