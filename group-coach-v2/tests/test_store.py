import json, threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import sqlalchemy as sa
import pytest
from conftest import members, proposal, approve
from wemeet_coach.persistence.store import Store, Conflict
from wemeet_coach.persistence import schema as d
from wemeet_coach.m0.runtime import code_result
from wemeet_coach.shared.contracts import (
    GroupReply,
    PersonalReply,
    PersonalState,
    PersonalPlan,
)


@pytest.mark.parametrize("n", [1, 3, 5, 8])
def test_unanimous_all_members_required(store, n):
    ms = members(n)
    g = store.create("만장일치", ms, purpose="VALIDATION")
    gid = g["group_id"]
    store.edit_proposal(
        gid, uuid4().hex, "u0", proposal(ms).model_dump(mode="json"), g["revision"]
    )
    pid = store.view(gid)["proposal"]["id"]
    for i, m in enumerate(ms):
        store.vote(gid, pid, m["member_id"], True, "", "", uuid4().hex)
        v = store.view(gid)
        assert bool(v["approved_proposal_id"]) == (i == n - 1)
        if i < n - 1:
            assert "votes" not in v["proposal"]
    assert len(v["proposal"]["votes"]) == n


def test_rejection_anonymous_then_new_round_resets(store):
    ms = members(3)
    g = store.create("반대 수정", ms, purpose="VALIDATION")
    gid = g["group_id"]
    body = proposal(ms).model_dump(mode="json")
    store.edit_proposal(gid, uuid4().hex, "u0", body, g["revision"])
    pid = store.view(gid)["proposal"]["id"]
    store.vote(
        gid, pid, "u0", False, "일정", "검증구성원0의 기한을 늘려주세요", uuid4().hex
    )
    v = store.view(gid)
    assert not v["approved_proposal_id"] and "votes" not in v["proposal"]
    assert "검증구성원0" not in v["proposal"]["issues"][0]["reason"]
    store.vote(gid, pid, "u1", True, "", "", uuid4().hex)
    store.vote(gid, pid, "u2", True, "", "", uuid4().hex)
    assert store.view(gid)["proposal"]["state"] == "REJECTED"
    body["goal"]["end_date"] = "2026-11-23"
    store.edit_proposal(gid, uuid4().hex, "u0", body, store.view(gid)["revision"])
    v = store.view(gid)
    assert (
        v["proposal"]["participation"] == 0
        and v["proposal"]["version"] == 2
        and not v["approved_proposal_id"]
    )


def test_member_change_and_chat_have_different_effects(store):
    gid, pid, ms = approve(store)
    store.message(gid, "u0", "새 의견입니다", uuid4().hex)
    assert store.view(gid)["approved_proposal_id"] == pid
    store.add_member(gid, {"member_id": "new", "nickname": "새 사람"}, uuid4().hex)
    assert store.view(gid)["approved_proposal_id"] is None


def test_vote_retry_same_action_and_changed_input_conflict(store):
    ms = members(3)
    g = store.create("중복 투표", ms, purpose="VALIDATION")
    gid = g["group_id"]
    store.edit_proposal(
        gid, uuid4().hex, "u0", proposal(ms).model_dump(mode="json"), g["revision"]
    )
    pid = store.view(gid)["proposal"]["id"]
    a = uuid4().hex
    one = store.vote(gid, pid, "u0", True, "", "", a)
    before = store.view(gid)["revision"]
    assert (
        store.vote(gid, pid, "u0", True, "", "", a) == one
        and store.view(gid)["revision"] == before
    )
    with pytest.raises(Conflict):
        store.vote(gid, pid, "u0", False, "일정", "변경", a)


def test_atomic_recovery_and_duplicate_execution(store, monkeypatch):
    g = store.create("실행 복구", members(1), purpose="VALIDATION")
    gid = g["group_id"]
    store.message(gid, "u0", "발표 계획을 도와줘", uuid4().hex)
    original = store.event
    calls = []

    def fail(c, gid, kind, *a, **k):
        if kind == "AI_REPLY":
            raise OSError("synthetic commit failure")
        return original(c, gid, kind, *a, **k)

    monkeypatch.setattr(store, "event", fail)

    def runner(payload):
        calls.append(1)
        return code_result("group.COACH", GroupReply(reply="실행안을 먼저 제안합니다."))

    result = store.execute(
        gid, "recover", "group.COACH", {}, store.view(gid)["revision"], runner=runner
    )
    assert result["persistence"]["state"] == "SAVE_PENDING" and len(calls) == 1
    assert not any(e["kind"] == "AI_REPLY" for e in store.view(gid)["events"])
    monkeypatch.setattr(store, "event", original)
    result = store.execute(
        gid,
        "recover",
        "group.COACH",
        {},
        0,
        runner=lambda p: pytest.fail("no paid retry"),
    )
    assert result["persistence"]["state"] == "COMPLETE" and len(calls) == 1
    assert store.execute(gid, "recover", "group.COACH", {}, 0, runner=runner)["reused"]


def test_concurrent_ai_claim_and_no_network_transaction_lock(store):
    g = store.create("동시 실행", members(1), purpose="VALIDATION")
    gid = g["group_id"]
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def run(p):
        calls.append(1)
        entered.set()
        assert release.wait(15)
        return code_result("group", GroupReply(reply="이전 대화를 보고 제안합니다."))

    rev = g["revision"]
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            store.execute, gid, "same", "group.COACH", {}, rev, runner=run
        )
        assert entered.wait(10)
        second = pool.submit(
            store.execute, gid, "same", "group.COACH", {}, rev, runner=run
        )
        assert second.result(10)["persistence"]["state"] == "RUNNING"
        store.message(gid, "u0", "추가 대화", uuid4().hex)
        release.set()
        out = first.result(15)
    assert calls == [1] and out["persistence"]["state"] == "STALE"


def test_personal_publish_is_whitelist_and_exact_baseline(store):
    gid, pid, ms = approve(store)
    owner = "u0"
    state = {
        "fields": {
            "level": {"value": "일부 경험", "status": "KNOWN"},
            "weekly_minutes": {"value": 60, "status": "KNOWN"},
            "scope": {"value": "담당 문서", "status": "KNOWN"},
            "constraints": {"value": ["PRIVATE_CANARY_병원일정"], "status": "KNOWN"},
        }
    }
    plan = {
        "plan_version_id": str(uuid4()),
        "contribution": "담당 문서 정리",
        "deliverable": "공유 가능한 문서",
        "completion_criteria": ["문서 확인 가능"],
        "tasks": [
            {
                "task_id": str(uuid4()),
                "owner_member_id": owner,
                "title": "문서 초안",
                "deliverable": "초안",
                "due_date": "2026-11-12",
                "estimated_minutes": 20,
                "completion_criteria": ["초안 공유"],
            }
        ],
        "private_notes": ["PRIVATE_CANARY_개인조언"],
    }

    def runner(payload):
        return code_result(
            "personal.PLAN",
            PersonalReply(
                reply="본인의 조건을 반영했습니다.",
                state=PersonalState.model_validate(state),
                plan=PersonalPlan.model_validate(plan),
            ),
        )

    result = store.execute(
        gid,
        "personal-plan",
        "personal.PLAN",
        {"text": "PRIVATE_CANARY_내조건", "message_id": uuid4().hex},
        store.view(gid)["revision"],
        owner=owner,
        runner=runner,
    )
    assert result["persistence"]["state"] == "COMPLETE"
    assert "PRIVATE_CANARY" not in json.dumps(store.view(gid), ensure_ascii=False)
    assert "PRIVATE_CANARY" not in json.dumps(
        store.personal_view(gid, "u1"), ensure_ascii=False
    )
    baseline = store.publish_personal(
        gid, owner, plan["plan_version_id"], "publish-once"
    )
    assert baseline == store.publish_personal(
        gid, owner, plan["plan_version_id"], "publish-once"
    )
    assert "PRIVATE_CANARY" not in json.dumps(store.view(gid), ensure_ascii=False)
    store.evaluation_context(
        gid,
        owner,
        baseline["baseline_id"],
        plan["plan_version_id"],
        plan["tasks"][0]["task_id"],
    )
    with pytest.raises(ValueError):
        store.evaluation_context(
            gid,
            "u1",
            baseline["baseline_id"],
            plan["plan_version_id"],
            plan["tasks"][0]["task_id"],
        )


def test_permissions_and_database_unanimity_guard(store):
    g = store.create("DB 권한", members(3), purpose="VALIDATION")
    gid = g["group_id"]
    store.edit_proposal(
        gid,
        uuid4().hex,
        "u0",
        proposal(members(3)).model_dump(mode="json"),
        g["revision"],
    )
    pid = store.view(gid)["proposal"]["id"]
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(gid) as (c, _):
            c.execute(
                d.proposals.update()
                .where(d.proposals.c.id == pid)
                .values(state="APPROVED")
            )
    for sql in [
        "CREATE TABLE wemeet_coach.forbidden(x int)",
        "DELETE FROM wemeet_coach.goals WHERE false",
    ]:
        with pytest.raises(sa.exc.DBAPIError):
            with store.tx() as (c, _):
                c.execute(sa.text(sql))
