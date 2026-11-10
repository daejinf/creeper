import json
import pytest
from conftest import members, proposal
from test_core import Session, Response, Shape
from wemeet_coach.m0.client import TimelyClient, RunStats
from wemeet_coach.personal import normalize
from wemeet_coach.validation import confirmed_weekdays
from wemeet_coach.shared.contracts import PersonalState, PersonalField, PersonalRequest


def test_truncated_json_expands_once_and_preserves_usage(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", "test-credential")
    s = Session([Response(finish="length"), Response()])
    stats = RunStats()
    result = TimelyClient(s).complete_json([], Shape, {"max_tokens": 1000}, stats=stats)
    assert (
        result.parsed_output.answer == "yes"
        and stats.transport_attempts == 2
        and stats.output_expansions == 1
    )
    assert s.calls[1]["json"]["max_tokens"] == 2000 and stats.usage.total_tokens == 20


def test_explicit_available_days_are_distinct_from_soft_preferences():
    assert normalize("available_weekdays", [6, 5, 5]) == [5, 6]
    assert confirmed_weekdays([]) == set()
    with pytest.raises(ValueError):
        normalize("available_weekdays", [True])
    assert normalize("preferred_times", ["주말"]) == ["주말"]


def test_personal_plan_does_not_apply_preference_as_a_prohibition():
    from wemeet_coach import personal_coach
    from wemeet_coach.m0.runtime import code_result

    state = PersonalState(
        fields={
            k: PersonalField(status="KNOWN", value=v)
            for k, v in {
                "level": "처음",
                "weekly_minutes": 60,
                "scope": "담당 문서",
                "preferred_times": ["주말"],
            }.items()
        }
    )

    class Model:
        def run(self, task, request, output_type, instructions, check, **kw):
            out = output_type.model_validate(
                {
                    "reply": "주말을 우선하되 초안 확인 마감은 평일로 제안합니다.",
                    "plan": {
                        "contribution": "문서 정리",
                        "deliverable": "문서",
                        "completion_criteria": ["문서 확인"],
                        "tasks": [
                            {
                                "owner_member_id": "u0",
                                "title": "초안 확인",
                                "deliverable": "문서",
                                "due_date": "2026-11-12",
                                "estimated_minutes": 30,
                                "completion_criteria": ["문서 확인"],
                            }
                        ],
                    },
                }
            )
            check(out)
            return code_result(task, out)

    assert (
        personal_coach(
            PersonalRequest(
                action="PLAN",
                goal=proposal(members(1)).goal,
                owner_member_id="u0",
                state=state,
            ),
            runtime=Model(),
        ).status
        == "OK"
    )


def test_model_inferred_availability_requires_confirmation():
    from wemeet_coach import personal_coach
    from wemeet_coach.shared.contracts import Message
    from wemeet_coach.m0.runtime import code_result

    class Model:
        def run(self, task, request, output_type, instructions, check, **kw):
            out = output_type(
                reply="가능 요일은 확인 전 추천입니다.",
                state={
                    "fields": {
                        "available_weekdays": {
                            "value": [5, 6],
                            "status": "KNOWN",
                            "evidence_refs": ["m"],
                        }
                    }
                },
            )
            check(out)
            return code_result(task, out)

    result = personal_coach(
        PersonalRequest(
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            conversation=[
                Message(message_id="m", speaker_id="u0", text="주말을 선호해요")
            ],
        ),
        runtime=Model(),
    )
    assert result.data.state.fields["available_weekdays"].status == "SUGGESTED"


def test_sw_global_identity_is_not_sent_to_provider(monkeypatch):
    from wemeet_coach import group_coach, GroupRequest
    from wemeet_coach.m0.runtime import Runtime

    monkeypatch.setenv("TIMELY_API_KEY", "test-credential")
    session = Session(
        [
            Response(
                content=json.dumps(
                    {
                        "summary": {"established": ["목표 제안됨"]},
                        "details": "실행 대안의 이유",
                    }
                )
            )
        ]
    )
    request = GroupRequest(
        members=[
            {
                "member_id": "u0",
                "nickname": "가람",
                "external_user_id": "SW-PRIVATE-STABLE-ID",
            }
        ],
        conversation=[
            {"message_id": "m", "speaker_id": "u0", "text": "학습 계획을 제안해줘"}
        ],
    )
    assert group_coach(request, runtime=Runtime(TimelyClient(session))).status == "OK"
    assert "SW-PRIVATE-STABLE-ID" not in json.dumps(session.calls[0]["json"])


def test_dependency_cannot_finish_before_prerequisite():
    from conftest import proposal, members
    from wemeet_coach.validation import validate_plan

    p = proposal(members(1))
    first = p.tasks[0]
    first.task_id = "first"
    first.due_date = p.goal.end_date
    second = first.model_copy(deep=True)
    second.task_id = "second"
    second.dependencies = ["first"]
    second.due_date = p.goal.start_date
    p.tasks = [first, second]
    out = validate_plan(p, p.goal, members={"u0"})
    assert not out["valid"] and any("선행 작업" in i for i in out["issues"])


def test_shared_task_needs_named_responsible_member():
    from conftest import proposal, members
    from wemeet_coach.validation import validate_plan

    p = proposal(members(1))
    p.tasks[0].owner_member_id = None
    assert not validate_plan(p, p.goal, members={"u0"})["valid"]


def test_other_members_allocated_tasks_rejected_before_model_call():
    import pytest
    from conftest import proposal, members
    from wemeet_coach import PersonalRequest

    p = proposal(members(2))
    task = p.tasks[0].model_copy(update={"owner_member_id": "u1"})
    with pytest.raises(ValueError, match="본인에게 배정"):
        PersonalRequest(goal=p.goal, owner_member_id="u0", assigned_tasks=[task])
