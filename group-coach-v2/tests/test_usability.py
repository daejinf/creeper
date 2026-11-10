"""Public functions accept JSON and explain errors without running the model."""

import pytest
from conftest import members, proposal
from wemeet_coach import group_coach, personal_coach, validate_plan


class NoCall:
    def run(self, *args, **kwargs):
        raise AssertionError("Invalid input must not reach the provider")


@pytest.mark.parametrize(
    "function,payload",
    [
        (group_coach, {}),
        (group_coach, {"members": []}),
        (personal_coach, {}),
        (personal_coach, {"owner_member_id": "other"}),
    ],
)
def test_invalid_json_request_is_a_structured_result(function, payload):
    out = function(payload, runtime=NoCall())
    assert out.status == "FAILED" and out.error.code == "INPUT_INVALID"
    assert out.needed_inputs and out.meta.transport_attempts == 0


def test_external_json_plan_validation_needs_no_ai_history():
    p = proposal(members(1))
    assert validate_plan(
        p.model_dump(mode="json"), p.goal.model_dump(mode="json"), members={"u0"}
    )["valid"]
    out = validate_plan({"tasks": []}, p.goal.model_dump(mode="json"))
    assert not out["valid"] and out["issues"] and out["not_checked"]


def test_empty_allowed_members_does_not_skip_the_check():
    p = proposal(members(1))
    assert not validate_plan(p, p.goal, members=set())["valid"]


def test_missing_private_conditions_have_stable_order():
    p = proposal(members(1))
    out = personal_coach(
        {
            "action": "PLAN",
            "goal": p.goal.model_dump(mode="json"),
            "owner_member_id": "u0",
        },
        runtime=NoCall(),
    )
    assert out.needed_inputs == ["level", "weekly_minutes", "scope"]


def test_explicit_postgres_names_are_not_hard_coded(tmp_path, monkeypatch):
    import sqlalchemy as sa
    from wemeet_coach.persistence import Store
    from cryptography.fernet import Fernet

    captured = []
    monkeypatch.setattr(sa, "create_engine", lambda url, **kwargs: captured.append(url))
    Store(
        "postgresql+psycopg://localhost/sw_team_development",
        tmp_path,
        Fernet.generate_key(),
    )
    assert captured and captured[0].endswith("sw_team_development")
    with pytest.raises(ValueError, match="PostgreSQL"):
        Store("sqlite://", tmp_path, Fernet.generate_key())


def test_false_runtime_does_not_silently_make_paid_calls():
    from wemeet_coach.m0.runtime import code_result

    class FalseRuntime:
        def __bool__(self):
            return False

        def run(self, task, request, output_type, *args, **kwargs):
            return code_result(
                task, output_type(summary={"next_actions": ["역할을 검토해주세요."]})
            )

    out = group_coach(
        {
            "members": members(1),
            "conversation": [
                {"message_id": "m", "speaker_id": "u0", "text": "발표 계획을 준비하자."}
            ],
        },
        runtime=FalseRuntime(),
    )
    assert out.status == "OK" and out.meta.transport_attempts == 0


def test_validator_checks_mutated_models_and_unknown_objects():
    p = proposal(members(1))
    p.tasks[0].estimated_minutes = -10
    assert not validate_plan(p, p.goal)["valid"]
    assert not validate_plan(None, p.goal)["valid"]


def test_shared_context_can_be_coached_without_fabricating_chat_history():
    from wemeet_coach.m0.runtime import code_result

    p = proposal(members(1))

    class Model:
        def run(self, task, request, output_type, *args, **kwargs):
            assert (
                not request.conversation and request.previous.goal.title == p.goal.title
            )
            return code_result(
                task, output_type(summary={"next_actions": ["담당 결과를 검토하세요."]})
            )

    out = group_coach(
        {
            "members": members(1),
            "previous": p.model_dump(mode="json"),
            "approval_status": "APPROVED",
        },
        runtime=Model(),
    )
    assert out.status == "OK"
