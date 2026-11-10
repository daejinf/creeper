"""New database only; injected responses test persistence, not AI quality."""

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from conftest import approve, members
from wemeet_coach.persistence.store import Conflict
from wemeet_coach.m0.runtime import code_result
from wemeet_coach.shared.contracts import (
    PersonalReply,
    PersonalState,
    PersonalPlan,
    GroupReply,
)


def make_plan(store, gid, owner="u0"):
    state = {
        "fields": {
            "level": {"value": "일부 경험", "status": "KNOWN"},
            "weekly_minutes": {"value": 60, "status": "KNOWN"},
            "scope": {"value": "담당 문서", "status": "KNOWN"},
        }
    }
    plan = PersonalPlan(
        contribution="공동 발표의 담당 문서 정리",
        deliverable="검토 문서",
        completion_criteria=["팀이 읽고 검토 가능"],
        tasks=[
            {
                "owner_member_id": owner,
                "title": "문서 작성",
                "deliverable": "초안",
                "due_date": "2026-11-12",
                "estimated_minutes": 20,
                "completion_criteria": ["초안 확인 가능"],
                "task_id": str(uuid4()),
            }
        ],
        plan_version_id=str(uuid4()),
    )
    out = store.execute(
        gid,
        str(uuid4()),
        "personal.PLAN",
        {},
        store.view(gid)["revision"],
        owner=owner,
        runner=lambda p: code_result(
            "personal.PLAN",
            PersonalReply(
                reply="조건을 반영한 초안",
                state=PersonalState.model_validate(state),
                plan=plan,
            ),
        ),
    )
    assert out["status"] == "OK"
    return plan


def test_private_request_carries_only_approved_own_tasks(store):
    gid, _, _ = approve(store)
    captured = []
    from wemeet_coach.shared.contracts import PersonalRequest

    def run(payload):
        request = PersonalRequest.model_validate(payload)
        captured.append(request)
        assert request.assigned_tasks
        assert all(t.owner_member_id == "u0" for t in request.assigned_tasks)
        return code_result(
            "personal.TALK",
            PersonalReply(
                reply="담당 작업을 기준으로 확인합니다.", state=request.state
            ),
        )

    store.execute(
        gid,
        str(uuid4()),
        "personal.TALK",
        {},
        store.view(gid)["revision"],
        owner="u0",
        runner=run,
    )
    assert captured


def test_personal_open_immediately_has_persisted_role_question_without_ai(store):
    gid, _, _ = approve(store)
    first = store.personal_view(gid, "u0")
    question = first["state"]["question"]
    assert question["field"] == "level" and "담당 결과 정리" in question["text"]
    assert len(question["options"]) == 4 and first["last_run"] is None
    assert store.personal_view(gid, "u0")["state"]["question"] == question
    assert (
        store.personal_view(gid, "u1")["state"]["question"]["question_id"]
        != question["question_id"]
    )


def test_private_edit_invalidates_only_own_plan(store):
    gid, pid, _ = approve(store)
    one = make_plan(store, gid)
    two = make_plan(store, gid, "u1")
    store.review_conditions(
        gid, "u0", {"weekly_minutes": {"value": 30, "status": "KNOWN"}}, str(uuid4())
    )
    assert store.personal_view(gid, "u0")["plans"][0]["stale"]
    assert not store.personal_view(gid, "u1")["plans"][0]["stale"]
    assert store.view(gid)["approved_proposal_id"] == pid
    with pytest.raises(Conflict):
        store.publish_personal(gid, "u0", one.plan_version_id, str(uuid4()))
    body = one.model_dump(mode="json")
    body["tasks"][0]["estimated_minutes"] = 15
    saved = store.edit_personal(gid, "u0", one.plan_version_id, body, str(uuid4()))
    assert saved["plan_version_id"] != one.plan_version_id
    new = store.personal_view(gid, "u0")["plans"][0]
    assert (
        not new["stale"] and new["body"]["tasks"][0]["task_id"] != one.tasks[0].task_id
    )
    assert store.publish_personal(gid, "u0", new["id"], str(uuid4()))["baseline_id"]


def test_invalid_result_is_completed_failure_not_infinite_save_pending(store):
    gid, _, _ = approve(store)

    def run(p):
        plan = PersonalPlan(
            contribution="잘못된 초안",
            deliverable="문서",
            completion_criteria=["문서"],
            tasks=[
                {
                    "owner_member_id": "other",
                    "title": "문서",
                    "deliverable": "문서",
                    "due_date": "2026-11-12",
                    "estimated_minutes": 20,
                    "completion_criteria": ["문서"],
                }
            ],
        )
        return code_result(
            "personal.PLAN",
            PersonalReply(reply="초안", state=PersonalState(), plan=plan),
        )

    result = store.execute(
        gid,
        "invalid-result",
        "personal.PLAN",
        {},
        store.view(gid)["revision"],
        owner="u0",
        runner=run,
    )
    assert (
        result["status"] == "FAILED"
        and result["error"]["code"] == "RESULT_VALIDATION_FAILED"
    )
    assert result["persistence"]["state"] == "COMPLETE"
    assert not store.personal_view(gid, "u0")["plans"]
    assert store.execute(
        gid,
        "invalid-result",
        "personal.PLAN",
        {},
        0,
        owner="u0",
        runner=lambda p: pytest.fail("no retry"),
    )["reused"]


def test_api_live_only_scenarios_no_calls_and_scoped_history(store, monkeypatch):
    from tools import web

    monkeypatch.setattr(web, "store", lambda: store)
    client = TestClient(web.app, raise_server_exceptions=False)
    cfg = client.get("/api/config").json()
    assert "key" not in str(cfg).lower()
    cases = client.get("/api/scenarios").json()
    assert len(cases) == 200
    group = client.post("/api/scenarios/" + cases[0]["case_id"] + "/load").json()
    gid = group["group_id"]
    assert group["events"] and group["last_run"] is None
    assert len(group["members"]) > 0
    bad = client.post(
        "/api/groups/" + gid + "/proposals/missing/vote", json={"agree": "yes"}
    )
    assert bad.status_code == 422
    gid, _, _ = approve(store)
    make_plan(store, gid)
    assert client.get("/api/groups/" + gid).json()["last_run"] is None
    assert client.get("/api/groups/" + gid + "/personal/u1").json()["last_run"] is None
    own = client.get("/api/groups/" + gid + "/personal/u0").json()
    assert own["last_run"] and own["plans"]
    assert (
        client.get("/api/groups/" + gid + "/personal/not-a-member").status_code == 422
    )
