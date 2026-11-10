"""Relational guards and save-only retry, without any provider call."""

from uuid import uuid4
import pytest
import sqlalchemy as sa
from conftest import approve
from test_integration import make_plan
from wemeet_coach.persistence import schema as d
from wemeet_coach.persistence import Conflict
from wemeet_coach.m0.runtime import code_result
from wemeet_coach.shared.contracts import GroupReply


def test_shared_plan_is_unique_per_goal(store):
    gid, _, _ = approve(store)
    with pytest.raises(sa.exc.IntegrityError):
        with store.tx(gid) as (c, row):
            p = dict(
                c.execute(
                    sa.select(d.plans).where(
                        d.plans.c.group_id == gid, d.plans.c.owner.is_(None)
                    )
                )
                .mappings()
                .one()
            )
            p["id"] = str(uuid4())
            c.execute(d.plans.insert().values(**p))


def test_wrong_personal_goal_reference_is_rejected_by_database(store):
    gid, _, _ = approve(store)
    p = make_plan(store, gid)
    body = store.view(gid)["proposal"]["body"]
    body["goal"]["end_date"] = "2026-11-25"
    store.edit_proposal(gid, str(uuid4()), "u0", body, store.view(gid)["revision"])
    pid = store.view(gid)["proposal"]["id"]
    for i in range(3):
        store.vote(gid, pid, "u" + str(i), True, "", "", str(uuid4()))
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(gid) as (c, row):
            old = dict(
                c.execute(sa.select(d.plans).where(d.plans.c.id == p.plan_version_id))
                .mappings()
                .one()
            )
            old["id"] = str(uuid4())
            old["body"] = {**old["body"], "plan_version_id": old["id"]}
            old["goal_id"] = c.scalar(
                sa.select(d.goals.c.id).where(d.goals.c.proposal_id == pid)
            )
            c.execute(d.plans.insert().values(**old))


def test_existing_task_owner_trigger_rejects_other_member(store):
    gid, _, _ = approve(store)
    p = make_plan(store, gid)
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(gid) as (c, row):
            task = p.tasks[0].model_dump(mode="json")
            task.update(task_id=str(uuid4()), owner_member_id="u1")
            c.execute(
                d.tasks.insert().values(
                    id=task["task_id"],
                    group_id=gid,
                    plan_id=p.plan_version_id,
                    owner="u1",
                    body=task,
                )
            )


def test_save_only_retry_does_not_need_model_runner(store):
    gid, _, _ = approve(store)
    action = str(uuid4())
    calls = []
    out = store.execute(
        gid,
        action,
        "group.COACH",
        {},
        store.view(gid)["revision"],
        runner=lambda p: (
            calls.append(1)
            or code_result("group.coach", GroupReply(reply="읽고 정리했습니다."))
        ),
    )
    retry = store.retry_save(gid, action)
    assert (
        len(calls) == 1
        and retry["new_http"] == 0
        and retry["meta"]["run_id"] == out["meta"]["run_id"]
    )


def test_recovery_packet_cannot_change_module_or_owner(store):
    gid, _, _ = approve(store)
    action = str(uuid4())
    store.execute(
        gid,
        action,
        "group.COACH",
        {},
        store.view(gid)["revision"],
        runner=lambda p: code_result("group.coach", GroupReply(reply="정리")),
    )
    with store.read(gid) as (c, row):
        op = dict(
            c.execute(
                sa.select(d.operations).where(
                    d.operations.c.group_id == gid, d.operations.c.action_id == action
                )
            )
            .mappings()
            .one()
        )
    bad = {**op, "module": "group.PROPOSE"}
    with pytest.raises(Conflict):
        store.finish({"operation": bad, "result": op["result"]})


def test_bad_vote_input_is_an_input_error_not_storage_failure(store):
    gid, pid, _ = approve(store)
    with pytest.raises(ValueError, match="문자열"):
        store.vote(gid, pid, "u0", False, "", None, str(uuid4()))
