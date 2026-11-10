"""Readable coaching and immutable confirmed profile regressions."""

import json
import pytest
from conftest import members, proposal
from wemeet_coach import personal_coach, group_coach, PersonalRequest, GroupRequest
from wemeet_coach.shared.contracts import PersonalState, PersonalField, Message
from wemeet_coach.m0.runtime import code_result, Runtime
from wemeet_coach.m0.client import TimelyClient
from test_core import Session, Response


def confirmed():
    values = {
        "level": "처음 해봄",
        "weekly_minutes": 180,
        "scope": "통계 분석·핵심 결과 정리",
        "tools": ["Python"],
        "preferred_times": ["주말"],
        "progress": "자료 확보, 분석 전",
        "constraints": [],
        "support": [],
    }
    state = PersonalState(
        fields={
            k: PersonalField(value=v, status="KNOWN", evidence_refs=["m" + str(i)])
            for i, (k, v) in enumerate(values.items())
        }
    )
    messages = [
        Message(message_id="m" + str(i), speaker_id="u0", text=str(v))
        for i, v in enumerate(values.values())
    ]
    return state, messages


def test_empty_free_input_button_preserves_valid_talk(monkeypatch):
    monkeypatch.setenv("TIMELY_API_KEY", "test-credential")
    out = {
        "reply": "무료 도구를 선택하거나 직접 입력해주세요.",
        "state": {
            "fields": {"tools": {"value": None, "status": "UNKNOWN"}},
            "question": {
                "field": "tools",
                "text": "어떤 무료 도구를 쓰실까요?",
                "reason": "쿼리를 실행할 환경을 정합니다.",
                "options": [
                    {"option_id": "sqlite", "label": "SQLite", "value": "SQLite"},
                    {"option_id": "custom", "label": "직접 지정", "value": None},
                    {"option_id": "unknown", "label": "모르겠음", "action": "UNKNOWN"},
                ],
            },
        },
    }
    session = Session([Response(content=json.dumps(out))])
    result = personal_coach(
        PersonalRequest(
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            conversation=[
                Message(message_id="p0", speaker_id="u0", text="무료 도구가 필요해요.")
            ],
        ),
        runtime=Runtime(TimelyClient(session)),
    )
    assert result.status == "OK" and len(session.calls) == 1
    question = result.data.state.question
    assert [o.option_id for o in question.options] == ["sqlite", "unknown"]
    assert question.options[0].value == ["SQLite"]
    assert result.data.state.fields["tools"].status == "UNKNOWN"


def test_plan_does_not_ask_model_to_rewrite_eight_confirmed_fields():
    state, messages = confirmed()
    state.processed_message_ids = [m.message_id for m in messages] + ["extra"]
    original = state.model_dump(mode="json")
    request = PersonalRequest(
        action="PLAN",
        goal=proposal(members(1)).goal,
        owner_member_id="u0",
        state=state,
        conversation=[
            *messages,
            Message(message_id="ai", role="assistant", text="옛 반복 설명"),
            Message(message_id="extra", speaker_id="u0", text="이전의 다른 이야기"),
        ],
    )

    class Model:
        def run(self, task, request, output_type, instructions, check, **kwargs):
            assert "state" not in output_type.model_json_schema()["properties"]
            assert len(request.conversation) == 8 and all(
                m.role == "user" for m in request.conversation
            )
            plan = {
                "reply": "주말 기준의 실행 초안입니다.",
                "plan": {
                    "contribution": "통계 요약 분석",
                    "deliverable": "분석 기록",
                    "completion_criteria": ["결측과 한계 기록"],
                    "tasks": [
                        {
                            "owner_member_id": "u0",
                            "title": "분석 기준 작성",
                            "deliverable": "기준표",
                            "due_date": "2026-11-14",
                            "estimated_minutes": 60,
                            "completion_criteria": ["기준 확인 가능"],
                        }
                    ],
                },
            }
            out = output_type.model_validate(plan)
            check(out)
            return code_result(task, out)

    result = personal_coach(request, runtime=Model())
    assert result.status == "OK" and result.data.plan
    assert result.data.state.model_dump(mode="json") == original
    assert state.model_dump(mode="json") == original


def test_plan_accepts_explicit_sw_conditions_without_running_coaching():
    state, _ = confirmed()

    class NoCall:
        def run(self, *args, **kwargs):
            raise AssertionError("missing conditions must not call AI")

    state.fields.pop("weekly_minutes")
    result = personal_coach(
        PersonalRequest(
            action="PLAN",
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            state=state,
        ),
        runtime=NoCall(),
    )
    assert result.status == "NEEDS_INPUT" and "weekly_minutes" in result.needed_inputs


def test_unchanged_fact_with_hallucinated_model_reference_is_restored_before_validation(
    monkeypatch,
):
    monkeypatch.setenv("TIMELY_API_KEY", "test-credential")
    state, messages = confirmed()
    state.processed_message_ids = [m.message_id for m in messages]
    out = {
        "reply": "조건을 유지합니다.",
        "state": {
            "fields": {
                "level": {
                    "value": "처음 해봄",
                    "status": "KNOWN",
                    "evidence_refs": ["made-up-id"],
                }
            }
        },
    }
    session = Session([Response(content=json.dumps(out))])
    result = personal_coach(
        PersonalRequest(
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            state=state,
            conversation=messages,
        ),
        runtime=Runtime(TimelyClient(session)),
    )
    assert result.status == "OK" and len(session.calls) == 1
    assert result.data.state.fields["level"].evidence_refs == ["m0"]


def test_new_fact_with_unknown_evidence_is_still_rejected():
    class Model:
        def run(self, task, request, output_type, instructions, check, **kwargs):
            out = output_type.model_validate(
                {
                    "reply": "확인",
                    "state": {
                        "fields": {
                            "tools": {
                                "value": ["Python"],
                                "status": "KNOWN",
                                "evidence_refs": ["invented"],
                            }
                        }
                    },
                }
            )
            with pytest.raises(ValueError, match="실제 본인"):
                check(out)
            return code_result(task)

    personal_coach(
        PersonalRequest(
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            conversation=[Message(message_id="m", speaker_id="u0", text="안녕")],
        ),
        runtime=Model(),
    )


def test_group_coaching_has_four_short_sections_and_explicit_approval_context():
    class Model:
        def run(self, task, request, output_type, instructions, **kwargs):
            assert "summary" in instructions and "details" in instructions
            assert "800자" not in instructions and "12~16줄" not in instructions
            assert request.approval_status == "APPROVED" and "text" not in kwargs
            return code_result(
                task,
                output_type(
                    summary={"established": ["기존 목표 승인됨"]},
                    details="상세 판단 근거",
                ),
            )

    group_coach(
        GroupRequest(
            members=members(1),
            conversation=[
                Message(message_id="m", speaker_id="u0", text="추천대로 진행하자")
            ],
            approval_status="APPROVED",
        ),
        runtime=Model(),
    )


def test_unambiguous_weekend_preference_checks_actual_task_dates():
    from wemeet_coach.validation import validate_plan, confirmed_weekdays

    p = proposal(members(1))
    assert not validate_plan(p, p.goal, allowed_weekdays=confirmed_weekdays([5, 6]))[
        "valid"
    ]
    with pytest.raises(ValueError):
        confirmed_weekdays(["주말"])
    p.tasks[0].due_date = __import__("datetime").date(2026, 11, 14)
    assert validate_plan(p, p.goal, allowed_weekdays=confirmed_weekdays([5, 6]))[
        "valid"
    ]


def test_plan_preserves_new_user_update_and_reports_scope_conflict():
    state, messages = confirmed()
    state.processed_message_ids = [m.message_id for m in messages]

    class Model:
        def run(self, task, request, output_type, instructions, check, **kw):
            assert any(
                m.text == "추가 유료 도구는 쓸 수 없어요" for m in request.conversation
            )
            out = output_type(
                reply="공동 담당 범위를 먼저 확인해주세요.",
                review_required=["승인된 담당 범위와 개인 요청이 다릅니다."],
            )
            check(out)
            return code_result(task, out)

    out = personal_coach(
        PersonalRequest(
            action="PLAN",
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            state=state,
            conversation=messages
            + [
                Message(
                    message_id="new",
                    speaker_id="u0",
                    text="추가 유료 도구는 쓸 수 없어요",
                )
            ],
        ),
        runtime=Model(),
    )
    assert out.status == "NEEDS_INPUT" and out.data.plan is None
    assert out.data.state.fields["weekly_minutes"].value == 180
