from uuid import uuid4
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from conftest import approve, members
from test_integration import make_plan
from wemeet_coach.persistence import schema as d
from wemeet_coach.persistence import Conflict


def link(store, gid, member="u0", user=None):
    user = user or "test-user:" + str(uuid4())
    store.link_user(gid, member, user, str(uuid4()))
    return {"external_user_id": user}


def test_one_identity_two_groups_keeps_profiles_separate(store):
    g1, _, _ = approve(store)
    g2, _, _ = approve(store)
    actor = link(store, g1)
    link(store, g2, user=actor["external_user_id"])
    store.review_conditions(
        g1, "u0", {"weekly_minutes": {"value": 60, "status": "KNOWN"}}, str(uuid4())
    )
    store.review_conditions(
        g2, "u0", {"weekly_minutes": {"value": 300, "status": "KNOWN"}}, str(uuid4())
    )
    reader = store.reader(actor)
    assert {r["id"] for r in reader.groups()["groups"]} == {g1, g2}
    assert reader.personal(g1)["state"]["fields"]["weekly_minutes"]["value"] == 60
    assert reader.personal(g2)["state"]["fields"]["weekly_minutes"]["value"] == 300
    assert all(r["goal_id"] for r in reader.groups()["groups"])


def test_same_nickname_never_merges_identity(store):
    g1, _, _ = approve(store)
    g2, _, _ = approve(store)
    a = link(store, g1)
    b = link(store, g2)
    with pytest.raises(PermissionError):
        store.reader(a).group(g2)
    with pytest.raises(PermissionError):
        store.reader(b).personal(g1)
    with pytest.raises(Conflict):
        store.link_user(g1, "u0", b["external_user_id"], str(uuid4()))


def test_group_baseline_exact_links_and_history(store):
    gid, _, _ = approve(store)
    actor = link(store, gid)
    reader = store.reader(actor)
    g = reader.group(gid)
    assert g["baseline_id"] and not g["requires_review"]
    p = reader.plan(gid, g["plan_version_id"])
    t = p["tasks"][0]
    assert (
        reader.baseline(gid, g["baseline_id"], p["plan_version_id"], t["task_id"])[
            "scope"
        ]
        == "GROUP"
    )
    with pytest.raises(ValueError):
        reader.baseline(gid, g["baseline_id"], p["plan_version_id"], "wrong-task")
    body = store.view(gid)["proposal"]["body"]
    body["goal"]["end_date"] = "2026-11-24"
    store.edit_proposal(gid, str(uuid4()), "u0", body, store.view(gid)["revision"])
    proposal = store.view(gid)["proposal"]
    for m in members(3):
        store.vote(gid, proposal["id"], m["member_id"], True, "", "", str(uuid4()))
    assert not reader.plan(gid, p["plan_version_id"])["is_current"]
    assert reader.group(gid)["baseline_id"] != g["baseline_id"]


def test_private_plan_access_cannot_be_changed_by_owner_id(store):
    gid, _, _ = approve(store)
    one = link(store, gid)
    two = link(store, gid, "u1")
    p = make_plan(store, gid)
    assert store.reader(one).plan(gid, p.plan_version_id)["scope"] == "PERSONAL"
    with pytest.raises(PermissionError):
        store.reader(two).plan(gid, p.plan_version_id)
    assert not store.reader(two).personal(gid)["plans"]


def test_service_reads_do_not_create_private_state_or_call_model(store):
    gid, _, _ = approve(store)
    actor = link(store, gid)
    with store.tx() as (c, _):
        before = c.scalar(
            sa.select(sa.func.count())
            .select_from(d.private)
            .where(d.private.c.group_id == gid)
        )
    reader = store.reader(actor)
    reader.groups()
    g = reader.group(gid)
    reader.personal(gid)
    reader.plan(gid, g["plan_version_id"])
    with store.tx() as (c, _):
        after = c.scalar(
            sa.select(sa.func.count())
            .select_from(d.private)
            .where(d.private.c.group_id == gid)
        )
    assert before == after == 0


def test_http_query_requires_sw_verified_identity_not_an_id_header(store, monkeypatch):
    from tools import web

    monkeypatch.setattr(web, "store", lambda: store)
    gid, _, _ = approve(store)
    actor = link(store, gid)
    client = TestClient(web.app, raise_server_exceptions=False)
    if hasattr(web.app.state, "identity_resolver"):
        del web.app.state.identity_resolver
    assert (
        client.get(
            "/integration/v1/me/groups",
            headers={"X-User-ID": actor["external_user_id"]},
        ).status_code
        == 403
    )
    web.app.state.identity_resolver = lambda request: actor
    try:
        assert client.get("/integration/v1/groups/" + gid + "/context").json()[
            "baseline_id"
        ]
        other, _, _ = approve(store)
        assert client.get("/integration/v1/groups/" + other + "/me").status_code == 403
    finally:
        del web.app.state.identity_resolver


def test_cross_group_current_pointer_and_private_proposal_rejected_by_database(store):
    one, p1, _ = approve(store)
    two, p2, _ = approve(store)
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(one) as (c, _):
            c.execute(
                d.rooms.update()
                .where(d.rooms.c.id == one)
                .values(approved_proposal_id=p2)
            )
    store.personal_view(one, "u0")
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(one) as (c, _):
            c.execute(
                d.private.update()
                .where(d.private.c.group_id == one)
                .values(proposal_id=p2)
            )


def test_duplicate_global_member_is_a_clear_input_error(store):
    user = "test:" + str(uuid4())
    with pytest.raises(ValueError):
        store.create(
            "duplicate",
            [
                {"member_id": "a", "nickname": "동명", "external_user_id": user},
                {"member_id": "b", "nickname": "동명", "external_user_id": user},
            ],
            purpose="VALIDATION",
        )


def test_explicit_personal_pointer_wins_over_later_creation_time(store):
    from wemeet_coach.shared.contracts import PersonalPlan
    from wemeet_coach.validation import assign_ids

    gid, _, _ = approve(store)
    actor = link(store, gid)
    one = make_plan(store, gid)
    edited = store.edit_personal(
        gid, "u0", one.plan_version_id, one.model_dump(mode="json"), str(uuid4())
    )["plan_version_id"]
    with store.tx(gid) as (c, _):
        current = (
            c.execute(sa.select(d.plans).where(d.plans.c.id == edited)).mappings().one()
        )
        future = PersonalPlan.model_validate(current["body"])
        assign_ids(future)
        c.execute(
            d.plans.insert().values(
                id=future.plan_version_id,
                group_id=gid,
                goal_id=current["goal_id"],
                owner="u0",
                personal_goal_id=current["personal_goal_id"],
                body=future.model_dump(mode="json"),
                created_at=sa.func.now() + sa.text("interval '1 year'"),
            )
        )
        for t in future.tasks:
            c.execute(
                d.tasks.insert().values(
                    id=t.task_id,
                    group_id=gid,
                    plan_id=future.plan_version_id,
                    owner="u0",
                    body=t.model_dump(mode="json"),
                )
            )
    view = store.personal_view(gid, "u0")
    assert view["current_plan_id"] == edited
    assert next(p for p in view["plans"] if p["id"] == one.plan_version_id)["stale"]
    assert store.reader(actor).plan(gid, edited)["is_current"]
    assert not store.reader(actor).plan(gid, future.plan_version_id)["is_current"]


def test_personal_pointer_cannot_reference_another_member_plan(store):
    gid, _, _ = approve(store)
    one = make_plan(store, gid)
    two = make_plan(store, gid, "u1")
    with pytest.raises(sa.exc.DBAPIError):
        with store.tx(gid) as (c, _):
            c.execute(
                d.private.update()
                .where(d.private.c.group_id == gid, d.private.c.owner == "u0")
                .values(current_plan_id=two.plan_version_id)
            )


def test_public_contribution_returns_only_latest_shared_version_without_private_values(
    store,
):
    gid, _, _ = approve(store)
    actor = link(store, gid)
    p = make_plan(store, gid)
    store.publish_personal(gid, "u0", p.plan_version_id, str(uuid4()))
    new = store.edit_personal(
        gid, "u0", p.plan_version_id, p.model_dump(mode="json"), str(uuid4())
    )["plan_version_id"]
    store.publish_personal(gid, "u0", new, str(uuid4()))
    shares = store.reader(actor).group(gid)["public_contributions"]
    assert (
        len(shares) == 1
        and shares[0]["plan_version_id"] == new
        and shares[0]["is_current"]
    )
    assert "private_notes" not in str(shares) and "weekly_minutes" not in str(shares)


def test_personal_read_explicit_current_and_review_status(store):
    gid, _, _ = approve(store)
    actor = link(store, gid)
    reader = store.reader(actor)
    empty = reader.personal(gid)
    assert (
        empty["scope"] == "PERSONAL"
        and empty["requires_review"]
        and not empty["is_current"]
    )
    p = make_plan(store, gid)
    current = reader.personal(gid)
    assert (
        current["plan_version_id"] == p.plan_version_id and current["personal_goal_id"]
    )
    assert current["is_current"] and not current["requires_review"]
    store.review_conditions(
        gid, "u0", {"weekly_minutes": {"value": 120, "status": "KNOWN"}}, str(uuid4())
    )
    old = reader.personal(gid)
    assert (
        old["plan_version_id"] == p.plan_version_id
        and old["requires_review"]
        and not old["is_current"]
    )
