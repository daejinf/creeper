"""Same-group personalized inputs, one read-only snapshot, no provider."""

from uuid import uuid4
import pytest
import sqlalchemy as sa
from conftest import approve
from test_identity_reader import link
from test_integration import make_plan
from wemeet_coach.persistence import schema as d


def test_scope_is_required_and_private_identity_is_not_overridable(store):
    gid, _, _ = approve(store)
    reader = store.reader(link(store, gid))
    with pytest.raises(TypeError):
        reader.context(gid)
    with pytest.raises(ValueError):
        reader.context(gid, scope="ALL")
    with pytest.raises(TypeError):
        reader.context(gid, scope="PERSONAL", owner="u1")


def test_group_ready_context_excludes_conversations_and_debug_logs(store):
    gid, _, _ = approve(store)
    reader = store.reader(link(store, gid))
    store.message(gid, "u0", "PRIVATE_CANARY", str(uuid4()), private=True)
    ctx = reader.context(gid, scope="GROUP")
    assert ctx["readiness"] == "READY" and ctx["baseline_id"] and ctx["tasks"]
    assert (
        "PRIVATE_CANARY" not in str(ctx)
        and "events" not in ctx
        and "last_run" not in ctx
    )
    assert not ctx["confirmed_conditions"]


def test_personal_confirmation_and_history_readiness(store):
    gid, _, _ = approve(store)
    reader = store.reader(link(store, gid))
    empty = reader.context(gid, scope="PERSONAL")
    assert empty["readiness"] == "NEEDS_INPUT"
    plan = make_plan(store, gid)
    draft = reader.context(gid, scope="PERSONAL")
    assert (
        draft["plan_version_id"] == plan.plan_version_id
        and draft["readiness"] == "NEEDS_INPUT"
    )
    store.publish_personal(gid, "u0", plan.plan_version_id, str(uuid4()))
    ready = reader.context(gid, scope="PERSONAL")
    assert ready["readiness"] == "READY" and ready["baseline_id"]
    assert "private_notes" not in str(ready)
    store.review_conditions(
        gid, "u0", {"weekly_minutes": {"value": 120, "status": "KNOWN"}}, str(uuid4())
    )
    old = reader.context(gid, scope="PERSONAL", plan_version_id=plan.plan_version_id)
    assert old["readiness"] == "REVIEW_REQUIRED"
    assert old["confirmed_conditions"]["weekly_minutes"]["value"] == 60
    with pytest.raises(ValueError):
        reader.context(gid, scope="GROUP", plan_version_id=plan.plan_version_id)


def test_context_does_not_share_same_user_conditions_across_meetings(store):
    one, _, _ = approve(store)
    two, _, _ = approve(store)
    actor = link(store, one)
    link(store, two, user=actor["external_user_id"])
    store.review_conditions(
        one, "u0", {"weekly_minutes": {"value": 17, "status": "KNOWN"}}, str(uuid4())
    )
    a = store.reader(actor).context(one, scope="PERSONAL")
    b = store.reader(actor).context(two, scope="PERSONAL")
    assert a["confirmed_conditions"]["weekly_minutes"]["value"] == 17
    assert "weekly_minutes" not in b["confirmed_conditions"]


def test_read_only_snapshot_cannot_write_and_sees_one_revision(store):
    gid, _, _ = approve(store)
    with store.read(gid) as (c, row):
        assert c.scalar(sa.text("SHOW transaction_isolation")) == "repeatable read"
        assert c.scalar(sa.text("SHOW transaction_read_only")) == "on"
        before = c.scalar(sa.select(d.rooms.c.revision).where(d.rooms.c.id == gid))
        store.message(gid, "u0", "동시 추가", str(uuid4()))
        assert (
            c.scalar(sa.select(d.rooms.c.revision).where(d.rooms.c.id == gid)) == before
        )
    assert store.view(gid)["revision"] > before
