"""Scoped revisions cannot silently rewrite unrelated confirmed conditions."""

from datetime import timedelta
from conftest import members, proposal
from wemeet_coach import group_coach
from wemeet_coach.group import DeadlineRevision
from wemeet_coach.m0.runtime import code_result


class PatchRuntime:
    def run(self, task, request, output_type, instructions, check=None, **kwargs):
        assert output_type is DeadlineRevision
        out = DeadlineRevision(
            reply="마감 연장 수정 제안입니다.",
            end_date=request.previous.goal.end_date + timedelta(days=3),
        )
        check(out)
        return code_result(task, out)


def test_deadline_only_preserves_every_unrelated_value_and_id():
    old = proposal(members(3))
    out = group_coach(
        {
            "action": "REVISE",
            "members": members(3),
            "previous": old.model_dump(mode="json"),
            "objections": ["최종 마감만 3일 연장해주세요. 나머지는 그대로 유지합니다."],
        },
        runtime=PatchRuntime(),
    )
    assert out.status == "OK"
    new = out.data.proposal
    a, b = old.goal.model_dump(), new.goal.model_dump()
    a.pop("end_date")
    b.pop("end_date")
    assert a == b
    assert old.tasks == new.tasks and old.milestones == new.milestones
    assert old.evidence_refs == new.evidence_refs


def test_explicit_field_scope_works_without_korean_keyword():
    old = proposal(members(1))
    out = group_coach(
        {
            "action": "REVISE",
            "members": members(1),
            "previous": old,
            "revision_fields": ["end_date"],
            "objections": ["Please extend by three days"],
        },
        runtime=PatchRuntime(),
    )
    assert out.status == "OK" and out.data.proposal.goal.end_date > old.goal.end_date


def test_broad_revision_is_not_silently_restricted_to_a_date():
    from wemeet_coach.group import deadline_only
    from wemeet_coach.shared.contracts import GroupRequest

    req = GroupRequest(
        action="REVISE",
        members=members(1),
        previous=proposal(members(1)),
        objections=["마감과 역할을 다시 나눠주세요."],
    )
    assert not deadline_only(req)


def test_auth_preflight_is_not_a_live_call(monkeypatch):
    from wemeet_coach.m0.runtime import Runtime
    from wemeet_coach.shared.contracts import GroupRequest

    monkeypatch.delenv("TIMELY_API_KEY", raising=False)
    out = group_coach(
        GroupRequest(
            members=members(1),
            conversation=[
                {"message_id": "m", "speaker_id": "u0", "text": "발표 계획을 세우자"}
            ],
        ),
        runtime=Runtime(),
    )
    assert out.status == "FAILED" and out.error.code == "AUTH_FAILED"
    assert out.meta.execution_source == "CODE" and out.meta.transport_attempts == 0
