"""Four-round module audit regressions. No paid calls."""

import pytest
from conftest import members, proposal
from wemeet_coach.validation import validate_plan
from wemeet_coach.shared.contracts import (
    PersonalRequest,
    PersonalState,
    PersonalField,
    Message,
    Question,
    Option,
)
from wemeet_coach.personal import personal_coach
from wemeet_coach.m0.runtime import code_result


def test_milestones_share_the_plan_validation_boundary():
    p = proposal(members(1))
    p.milestones = [
        __import__("wemeet_coach.shared.contracts", fromlist=["Milestone"]).Milestone(
            title="기한 밖", due_date="2026-12-01"
        )
    ]
    check = validate_plan(p, p.goal)
    assert not check["valid"] and "milestones.0" in str(check["issues"])


@pytest.mark.parametrize("budget", [0, -1, True, "60"])
def test_invalid_budget_does_not_disable_validation(budget):
    p = proposal(members(1))
    assert not validate_plan(p, p.goal, weekly_minutes=budget)["valid"]


def test_private_duplicate_evidence_ids_are_rejected():
    with pytest.raises(ValueError, match="중복"):
        PersonalRequest(
            goal=proposal(members(1)).goal,
            owner_member_id="u0",
            conversation=[
                Message(message_id="m", speaker_id="u0", text="한번"),
                Message(message_id="m", speaker_id="u0", text="다른 답"),
            ],
        )


def test_model_cannot_reinterpret_old_evidence_as_a_new_edit():
    state = PersonalState(
        fields={
            "weekly_minutes": PersonalField(
                value=120, status="KNOWN", evidence_refs=["m1"]
            )
        },
        processed_message_ids=["m1", "m2"],
    )
    request = PersonalRequest(
        goal=proposal(members(1)).goal,
        owner_member_id="u0",
        state=state,
        conversation=[
            Message(message_id="m1", speaker_id="u0", text="2시간"),
            Message(message_id="m2", speaker_id="u0", text="지난 대화"),
        ],
    )

    class Runtime:
        def run(self, task, request, output_type, *args, check, **kwargs):
            out = output_type(
                reply="확인",
                state=PersonalState(
                    fields={
                        "weekly_minutes": PersonalField(
                            value=300, status="KNOWN", evidence_refs=["m2"]
                        )
                    }
                ),
            )
            check(out)
            return code_result(task, out)

    assert (
        personal_coach(request, runtime=Runtime())
        .data.state.fields["weekly_minutes"]
        .value
        == 120
    )


def test_trace_jsonl_still_scrubs_credentials(tmp_path):
    import json
    from wemeet_coach.m0.tracing import JsonlTraceSink, scope, emit

    p = tmp_path / "trace.jsonl"
    with scope(JsonlTraceSink(p), "t", "r", None, "group"):
        emit("test", {"Authorization": "do-not-log", "text": "hello"})
    event = json.loads(p.read_text(encoding="utf8"))
    assert event["sanitized_payload"] == {"text": "hello"}


def test_button_fact_survives_model_failure(store):
    from conftest import approve
    from uuid import uuid4
    from wemeet_coach.shared.contracts import Error

    gid, _, _ = approve(store)
    q = store.personal_view(gid, "u0")["state"]["question"]
    result = store.execute(
        gid,
        str(uuid4()),
        "personal.TALK",
        {
            "message_id": str(uuid4()),
            "answer": {"question_id": q["question_id"], "option_id": "some"},
        },
        store.view(gid)["revision"],
        owner="u0",
        runner=lambda p: code_result(
            "personal", error=Error(code="TIMEOUT", message="synthetic timeout")
        ),
    )
    assert result["status"] == "FAILED"
    state = store.personal_view(gid, "u0")["state"]
    assert state["fields"]["level"]["value"] == "일부 경험"
    assert q["question_id"] in state["answered_questions"]


def test_python_vote_requires_real_boolean(store):
    from conftest import approve
    from uuid import uuid4

    gid, pid, _ = approve(store)
    with pytest.raises(ValueError, match="bool"):
        store.vote(gid, pid, "u0", "false", "", "", str(uuid4()))


def test_manual_plan_edit_requires_all_confirmed_core_conditions(store):
    from conftest import approve
    from test_integration import make_plan
    from uuid import uuid4

    gid, _, _ = approve(store)
    p = make_plan(store, gid)
    store.review_conditions(
        gid, "u0", {"level": {"status": "UNKNOWN", "value": None}}, str(uuid4())
    )
    with pytest.raises(ValueError, match="수준|필수"):
        store.edit_personal(
            gid, "u0", p.plan_version_id, p.model_dump(mode="json"), str(uuid4())
        )


def test_unrelated_private_edit_does_not_stale_public_coaching(store):
    from conftest import approve
    from uuid import uuid4
    from wemeet_coach.shared.contracts import GroupReply

    gid, _, _ = approve(store)

    def run(payload):
        store.review_conditions(
            gid,
            "u0",
            {"weekly_minutes": {"status": "KNOWN", "value": 120}},
            str(uuid4()),
        )
        return code_result(
            "group.COACH", GroupReply(reply="공동 조건은 그대로 유지됩니다.")
        )

    out = store.execute(
        gid, str(uuid4()), "group.COACH", {}, store.view(gid)["revision"], runner=run
    )
    assert out["persistence"]["state"] == "COMPLETE"
