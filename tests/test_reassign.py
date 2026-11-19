"""Stage 4 session C deterministic contract: the reassignment operation (leader request + target consent, D-B1 B4) and
STALE propagation for a new assignment revision and a ProjectBrief change (D-B1 B8, D-B4). Server API only, no screen.

The provider is the FIXTURE client of the stage 4 suites (never reported as LIVE); the assignment RNG is a seeded
random.Random injected by the `app` fixture.
"""
from __future__ import annotations

import random
import threading
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config

from moim.roles.reassign import ReassignError, free_roles, pick_new_role
from test_groups import brief_body, col, db, fake, grp_db, one, post, scalar, today  # noqa: F401
from test_role_assignment import ROOT, app, assigned_team, forbidden_in, mk, reconnect, rows, screen, seed  # noqa: F401
from test_todos import TodoAI, confirm, edit, finish_interview, group_todos, td, tstart

DERIVED = ("team_coverages", "role_interviews", "role_interview_snapshots", "todo_drafts", "todo_confirmations")


@pytest.fixture()
def ai():
    return TodoAI()


def M(uid, role, slot="s1", rank=1):
    return SimpleNamespace(user_id=uid, role_id=role, slot_id=slot, assigned_rank=rank)


def ask(u, gid, target, rev=None, action=None, **extra):
    rev = rev or screen(u, gid)["role"]["coverage"]["assignment_revision"]
    return post(u, f"/api/groups/{gid}/reassignments",
                {"action_id": action or uuid.uuid4().hex, "assignment_revision": rev, "target_user_id": target, **extra})


def respond(u, gid, rid, accept=True, action=None):
    return post(u, f"/api/groups/{gid}/reassignments/{rid}/respond", {"action_id": action or uuid.uuid4().hex, "accept": accept})


def current_rev(eng, gid):
    return scalar(eng, "select revision from role_assignments where group_id=:g and state='CURRENT'", g=gid)


def role_of(eng, gid, uid, rev):
    return one(eng, "select role_id, slot_id, assigned_rank, role_name from role_assignment_members "
                    "where group_id=:g and assignment_revision=:r and user_id=:u", g=gid, r=rev, u=uid)


def confirmed_team(mk, rankings=None):
    """Leader and second member finished the interview and confirmed todos; the third has done nothing."""
    gid, people = assigned_team(mk, 3, rankings=rankings)
    for u in people[:2]:
        finish_interview(u, gid)
        assert tstart(u, gid).status_code == 202 and td(u, gid)["state"] == "READY"
        assert confirm(u, gid).status_code in (201, 202)
        assert td(u, gid)["mode"] == "CONFIRMED"
    return gid, people


# ---- the pure rule ------------------------------------------------------------------------------------


def test_the_new_role_follows_the_targets_own_ranking_and_never_moves_anyone_else():
    members = [M("a", "r1"), M("b", "r2", "s2"), M("c", "r3", "s3")]
    roles = ["r1", "r2", "r3", "r4", "r5"]
    assert free_roles(members, "b", roles) == ["r4", "r5"]  # not the role b leaves, not a taken one
    assert pick_new_role(members, "b", ("r2", "r4", "r1"), roles, random.Random(1))[:2] == ("r4", 2)
    assert pick_new_role(members, "b", ("r5", "r4", "r1"), roles, random.Random(1))[:2] == ("r5", 1)
    # ranking exhausted (the leaving role and taken roles only): the server RNG picks among the free roles
    role, rank, audit = pick_new_role(members, "b", ("r2", "r1", "r3"), roles, random.Random(3))
    assert rank is None and role in ("r4", "r5") and audit["round"] == "reassign_remaining"
    assert {pick_new_role(members, "b", ("r2", "r1", "r3"), roles, random.Random(s))[0] for s in range(40)} == {"r4", "r5"}
    # same seed, same result (fixed RNG in tests; the product uses SystemRandom through AssignmentService)
    assert pick_new_role(members, "b", ("r2", "r1", "r3"), roles, random.Random(9)) == pick_new_role(
        members, "b", ("r2", "r1", "r3"), roles, random.Random(9))


def test_no_free_role_is_refused_and_duplicate_roles_use_the_same_capacity_rule():
    full = [M("a", "r1"), M("b", "r2", "s2"), M("c", "r3", "s3")]
    with pytest.raises(ReassignError) as e:
        pick_new_role(full, "b", ("r1", "r3", "r2"), ["r1", "r2", "r3"], random.Random(1))
    assert e.value.code == "NO_FREE_ROLE"
    # fewer roles than members: ceil(4 / 2) = 2 places per role (fixture only, like the first assignment)
    dup = [M("a", "r1"), M("b", "r1", "s2"), M("c", "r2", "s3"), M("d", "r2", "s4")]
    assert free_roles(dup, "a", ["r1", "r2"]) == []  # r2 is full, r1 is the role a leaves
    three = [M("a", "r1"), M("b", "r1", "s2"), M("c", "r2", "s3")]
    assert pick_new_role(three, "a", ("r1", "r2", "r3"), ["r1", "r2"], random.Random(1))[:2] == ("r2", 2)


# ---- request and consent ------------------------------------------------------------------------------


def test_reassignment_needs_the_leaders_request_and_the_targets_own_consent(db, mk, ai):
    gid, (lead, b, c) = confirmed_team(mk, [["r1", "r2", "r3"], ["r2", "r4", "r1"], ["r3", "r1", "r2"]])
    assert current_rev(db, gid) == 1
    # a member who is not the leader cannot ask; an outsider gets 404
    assert ask(b, gid, c.id).status_code == 403
    outsider = mk("outsider9")
    assert post(outsider, f"/api/groups/{gid}/reassignments",
                {"action_id": uuid.uuid4().hex, "assignment_revision": 1, "target_user_id": b.id}).status_code == 404
    # the target must be in the CURRENT revision, and the revision must be the current one
    assert ask(lead, gid, outsider.id).json()["error"]["code"] == "INVALID_TARGET"
    assert ask(lead, gid, b.id, rev=7).json()["error"]["code"] == "STALE_ASSIGNMENT"
    # a role in the body is refused: neither the leader nor the member picks a role
    assert ask(lead, gid, b.id, role_id="r5").status_code == 400

    r = ask(lead, gid, b.id, action="reqaction001")
    assert r.status_code == 201, r.text
    req = r.json()["request"]
    assert set(req) == {"request_id", "state", "assignment_revision"} and req["state"] == "REQUESTED"
    assert ask(lead, gid, b.id, action="reqaction001").json()["request"] == req  # replay: same request, nothing new
    assert ask(lead, gid, c.id).json()["error"]["code"] == "REQUEST_OPEN"  # one open request per group
    assert current_rev(db, gid) == 1  # nothing changes before the consent

    # only the target sees it as theirs; the leader sees it as their own request; the third member sees nothing
    assert b.c.get(f"/api/groups/{gid}/reassignments").json()["incoming"] == [req]
    assert lead.c.get(f"/api/groups/{gid}/reassignments").json() == {"incoming": [], "outgoing": [req]}
    assert c.c.get(f"/api/groups/{gid}/reassignments").json() == {"incoming": [], "outgoing": []}
    # nobody can consent for the target (the leader and the others get 404, like a missing request)
    for u in (lead, c):
        assert respond(u, gid, req["request_id"]).status_code == 404
    assert respond(b, gid, str(uuid.uuid4())).status_code == 404
    assert respond(b, gid, "not-a-uuid").status_code == 404

    r = respond(b, gid, req["request_id"], action="consent0001")
    assert r.status_code == 201, r.text
    assert r.json()["outcome"] == "accepted"
    assert respond(b, gid, req["request_id"], action="consent0001").json()["outcome"] == "replay"  # replay: no new draw
    assert respond(b, gid, req["request_id"]).json()["error"]["code"] == "NOT_PENDING"
    assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 2

    new = one(db, "select * from role_assignments where group_id=:g and revision=2", g=gid)
    old = one(db, "select * from role_assignments where group_id=:g and revision=1", g=gid)
    assert (new.state, new.operation, new.source_cohort_revision, new.source_candidate_revision) == ("CURRENT", "REASSIGN", 1, 1)
    assert (old.state, old.stale_reason) == ("STALE", "REASSIGNED") and old.stale_at is not None
    # the target moved by their own ranking (2nd choice r4, as 1st is the role they leave); the others kept role and slot
    assert role_of(db, gid, b.id, 2)[:3] == ("r4", role_of(db, gid, b.id, 1).slot_id, 2)
    for u in (lead, c):
        assert role_of(db, gid, u.id, 2) == role_of(db, gid, u.id, 1)
    # the same inputs: the submissions the first assignment used, unchanged
    assert [r[0] for r in rows(db, "select submission_revision from role_assignment_members where group_id=:g "
                                   "and assignment_revision=2 order by user_id", g=gid)] == \
           [r[0] for r in rows(db, "select submission_revision from role_assignment_members where group_id=:g "
                                   "and assignment_revision=1 order by user_id", g=gid)]
    assert scalar(db, "select count(*) from preference_submissions where group_id=:g", g=gid) == 3
    assert {r.status for r in rows(db, "select status from member_progress where group_id=:g", g=gid)} == {"ROLE_TODOS"}
    assert scalar(db, "select count(*) from member_progress where group_id=:g and assignment_revision=2", g=gid) == 3
    assert one(db, "select state from role_reassignments where id=:i", i=req["request_id"])[0] == "ACCEPTED"


def test_a_new_revision_marks_everything_made_from_the_old_one_stale(db, mk, ai):
    gid, (lead, b, c) = confirmed_team(mk, [["r1", "r2", "r3"], ["r2", "r4", "r1"], ["r3", "r1", "r2"]])
    assert [x["role_name"] for x in group_todos(c, gid)["assignments"]] and len(group_todos(c, gid)["assignments"]) == 2
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    assert respond(b, gid, rid).status_code == 201
    for t in DERIVED:
        assert scalar(db, f"select count(*) from {t} where group_id=:g and source_assignment_revision=1 and stale_at is null", g=gid) == 0, t
        assert scalar(db, f"select count(*) from {t} where group_id=:g and source_assignment_revision=1", g=gid) > 0, t
    # nothing deleted: the confirmed revisions and their hidden criteria are still there, unchanged
    assert scalar(db, "select count(*) from todo_confirmed_items where group_id=:g and source_assignment_revision=1", g=gid) > 0
    # the old confirmations are no longer the team's: none is shown until each member confirms again
    assert group_todos(c, gid) == {"assignments": []}
    # the team coverage is made again for the new table (one call), linked to the new revision
    assert one(db, "select state from team_coverages where group_id=:g and source_assignment_revision=2", g=gid)[0] == "SUCCEEDED"
    assert len(ai.payloads("CoverageOutput")) == 2
    # the reassigned member answers a new interview; the others keep their confirmed answers (B8 keeps the MemberState)
    assert screen(b, gid)["role"]["interview"]["state"] == "ASKING"
    assert one(db, "select state from role_interviews where group_id=:g and user_id=:u and source_assignment_revision=2",
               g=gid, u=lead.id)[0] == "DONE"
    old_in = one(db, "select provider_input from role_interview_snapshots where group_id=:g and user_id=:u "
                     "and source_assignment_revision=1", g=gid, u=lead.id)[0]
    new_in = one(db, "select provider_input, stale_at from role_interview_snapshots where group_id=:g and user_id=:u "
                     "and source_assignment_revision=2", g=gid, u=lead.id)
    assert new_in.provider_input == old_in and new_in.stale_at is None
    # the third member never finished the interview: it starts again (nothing half-carried)
    assert one(db, "select state from role_interviews where group_id=:g and user_id=:u and source_assignment_revision=2",
               g=gid, u=c.id)[0] == "ASKING"
    # the leader gets a new draft from the new coverage without a new interview, and can confirm it again
    assert tstart(lead, gid).status_code == 202
    assert td(lead, gid)["state"] == "READY"
    drafts = ai.payloads("DraftOutput")
    assert drafts[-1]["team"] and any(t["is_me"] for t in drafts[-1]["team"])
    assert confirm(lead, gid).status_code in (201, 202)
    shown = group_todos(c, gid)["assignments"]
    assert len(shown) == 1 and shown[0]["role_name"] == role_of(db, gid, lead.id, 2).role_name
    assert set(shown[0]) == {"role_name", "todos"} and all(set(t) == {"title", "due_date"} for t in shown[0]["todos"])


def test_a_declined_request_changes_nothing_and_another_can_follow(db, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3)
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    r = respond(b, gid, rid, accept=False)
    assert r.status_code == 201 and r.json()["outcome"] == "declined"
    assert current_rev(db, gid) == 1 and scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 1
    assert scalar(db, "select count(*) from team_coverages where group_id=:g and stale_at is not null", g=gid) == 0
    assert respond(b, gid, rid).json()["error"]["code"] == "NOT_PENDING"
    assert ask(lead, gid, c.id).status_code == 201  # the group may ask again


def test_the_leader_may_be_the_target_but_still_consents_as_themself(db, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3)
    rid = ask(lead, gid, lead.id).json()["request"]["request_id"]
    assert respond(b, gid, rid).status_code == 404
    assert respond(lead, gid, rid).status_code == 201
    assert current_rev(db, gid) == 2 and role_of(db, gid, lead.id, 2).role_id != role_of(db, gid, lead.id, 1).role_id


def test_no_free_role_is_refused_before_anyone_is_asked(db, mk, ai):
    from test_role_assignment import CANDIDATES

    ai.scripts["CandidateOutput"].append(CANDIDATES[:3])  # three roles for three members: every place is taken
    gid, (lead, b, c) = assigned_team(mk, 3)
    r = ask(lead, gid, b.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_FREE_ROLE"
    assert scalar(db, "select count(*) from role_reassignments where group_id=:g", g=gid) == 0


def test_concurrent_consents_make_one_revision(db, app, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3)
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    svc = app.state.reassign
    out = []

    def go():
        try:
            out.append(svc.respond(b.id, gid, rid, uuid.uuid4().hex, True)[0])
        except ReassignError as exc:
            out.append(exc.code)

    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == ["NOT_PENDING", "NOT_PENDING", "accepted"]
    assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 2


def test_running_ai_work_of_a_stale_revision_is_dropped(db, app, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3)
    # the old coverage is made PENDING again by hand, then the revision goes STALE: running it spends no call
    with app.state.engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("update team_coverages set state='PENDING', parts=null where group_id=:g"), {"g": gid})
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    n = len(ai.payloads("CoverageOutput"))
    assert respond(b, gid, rid).status_code == 201
    assert len(ai.payloads("CoverageOutput")) == n + 1  # only the new revision's coverage
    app.state.coverage.run(gid, 1)
    assert len(ai.payloads("CoverageOutput")) == n + 1
    assert one(db, "select state from team_coverages where group_id=:g and source_assignment_revision=1", g=gid)[0] == "PENDING"
    # the old revision's interview and todo endpoints are gone for good: everything works on the CURRENT revision only
    assert post(lead, f"/api/groups/{gid}/coverage/retry",
                {"action_id": uuid.uuid4().hex, "assignment_revision": 1, "expected_attempt": 1}).status_code == 409


def test_privacy_login_csrf_and_logs(db, app, mk, ai, caplog):
    gid, (lead, b, c) = confirmed_team(mk, [["r1", "r2", "r3"], ["r2", "r4", "r1"], ["r3", "r1", "r2"]])
    caplog.set_level("INFO")
    req = ask(lead, gid, b.id).json()["request"]
    for path, body in ((f"/api/groups/{gid}/reassignments", {"action_id": uuid.uuid4().hex, "assignment_revision": 1, "target_user_id": c.id}),
                       (f"/api/groups/{gid}/reassignments/{req['request_id']}/respond", {"action_id": uuid.uuid4().hex, "accept": True})):
        assert post(lead if "respond" not in path else b, path, body, token=None).status_code == 403  # CSRF
        anon = SimpleNamespace(c=type(lead.c)(lead.c.app, follow_redirects=False))
        assert post(anon, path, body, token=None).status_code in (401, 403)
    assert anon.c.get(f"/api/groups/{gid}/reassignments").status_code == 401
    r = respond(b, gid, req["request_id"])
    blob = r.text
    # the target's own payload: never another member's name, user id or role choice
    forbidden_in(blob, [lead, c])
    for u in (lead, c):
        forbidden_in(u.c.get(f"/api/groups/{gid}/reassignments").text, [b] if u is c else [])
    logs = "\n".join(m.getMessage() for m in caplog.records if m.name.startswith("moim"))
    forbidden_in(logs, [lead, b, c], extra=[role_of(db, gid, b.id, 2).role_name, "draw_audit"])


def test_refresh_and_restart_recover_the_new_revision(db, app, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3, rankings=[["r1", "r2", "r3"], ["r2", "r4", "r1"], ["r3", "r1", "r2"]])
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    respond(b, gid, rid)
    want = screen(b, gid)["role"]
    assert want["role_name"] == "발표" and want["rank_text"] == "2순위로 배정"
    assert screen(reconnect(b), gid)["role"]["role_name"] == "발표"
    from fastapi.testclient import TestClient

    from moim.config import load_settings
    from moim.main import create_app

    other = create_app(load_settings(), ai_client_factory=lambda: ai)
    try:
        with TestClient(other, follow_redirects=False) as tc:
            tc.cookies.set("moim_session", b.c.cookies.get("moim_session"), domain="testserver.local")
            v = tc.get(f"/api/groups/{gid}/roles").json()["role"]
            assert (v["role_name"], v["rank_text"], v["coverage"]["assignment_revision"]) == ("발표", "2순위로 배정", 2)
    finally:
        other.state.engine.dispose()
    assert current_rev(db, gid) == 2


# ---- ProjectBrief change (B8) --------------------------------------------------------------------------


def update_brief(u, gid, **over):
    base = u.c.get(f"/api/groups/{gid}/brief").json()["revision"]
    return post(u, f"/api/groups/{gid}/brief", {"base_revision": base, **brief_body(**over)})


def test_a_brief_change_makes_the_assignment_stale_and_keeps_confirmed_todos_read_only(db, mk, ai):
    gid, (lead, b, c) = confirmed_team(mk)
    before = group_todos(c, gid)
    assert len(before["assignments"]) == 2
    pending = ask(lead, gid, c.id).json()["request"]["request_id"]
    r = update_brief(lead, gid, end_date=(today() + timedelta(days=20)).isoformat())
    assert r.status_code == 200 and r.json()["changed"], r.text

    a = one(db, "select state, stale_reason from role_assignments where group_id=:g and revision=1", g=gid)
    assert tuple(a) == ("STALE", "BRIEF_CHANGED")
    for t in DERIVED:
        assert scalar(db, f"select count(*) from {t} where group_id=:g and stale_at is null", g=gid) == 0, t
    assert one(db, "select state from role_reassignments where id=:i", i=pending)[0] == "SUPERSEDED"
    # everyone ranks again against the new candidate revision; rankings and MemberState rows are kept, not deleted
    for u in (lead, b, c):
        v = screen(u, gid)
        assert v["phase"] == "INTERVIEW" and v["role"] is None and v["ranking_reset"] is True
    assert scalar(db, "select count(*) from preference_submissions where group_id=:g", g=gid) == 3
    assert scalar(db, "select count(*) from role_member_states where group_id=:g", g=gid) > 0
    # the stale todos cannot be changed any more
    assert edit(lead, gid, "add", title="새 투두 하나 더", rev=1).status_code == 409
    assert post(lead, f"/api/groups/{gid}/todos/unlock", {"action_id": uuid.uuid4().hex}).status_code == 409
    # B8: the confirmed todos stay as they were, read-only, until each member confirms new ones
    assert group_todos(c, gid) == before

    # a new assignment from the new candidates (INITIAL) is possible because the old one is no longer CURRENT
    from test_role_assignment import rank

    for u, ids in zip((lead, b, c), (["r1", "r2", "r3"], ["r2", "r1", "r3"], ["r3", "r1", "r2"])):
        assert screen(u, gid)["candidates"]["candidate_revision"] == 2
        assert rank(u, gid, ids).status_code == 201
    new = one(db, "select revision, operation, source_candidate_revision from role_assignments where group_id=:g and state='CURRENT'", g=gid)
    assert tuple(new) == (2, "INITIAL", 2)
    assert group_todos(c, gid) == before  # still the read-only ones
    finish_interview(lead, gid)
    assert tstart(lead, gid).status_code == 202 and confirm(lead, gid).status_code in (201, 202)
    after = group_todos(c, gid)["assignments"]
    assert len(after) == 2 and after[0] != before["assignments"][0]  # the leader's new set replaced the old one
    assert before["assignments"][1] in after  # b has not confirmed again: b's old set is still shown read-only


def test_a_submission_cannot_change_after_assignment(db, mk, ai):
    from test_role_assignment import rank

    gid, (lead, b, c) = assigned_team(mk, 3)
    r = rank(b, gid, ["r4", "r5", "r1"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_SUBMITTED"
    assert current_rev(db, gid) == 1


def test_migration_0010_down_and_up_keeps_reassigned_revisions(db, grp_db, mk, ai):
    gid, (lead, b, c) = assigned_team(mk, 3)
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    respond(b, gid, rid)
    cfg = Config(str(ROOT / "alembic.ini"))
    try:
        command.downgrade(cfg, "0009")
        assert scalar(db, "select to_regclass('public.role_reassignments')") is None
        assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 2
        command.upgrade(cfg, "head")
        assert scalar(db, "select to_regclass('public.role_reassignments')") is not None
        assert scalar(db, "select count(*) from role_assignments where group_id=:g and operation='REASSIGN'", g=gid) == 1
    finally:
        command.upgrade(cfg, "head")


def test_a_page_left_open_across_a_reassignment_cannot_reuse_its_start_ids(db, mk, ai):
    """Found in the stage 4 browser run: the chat page kept one start id per page load and resent it for the new
    revision, which hit the unique action key (500). Now the server refuses it cleanly and the page makes a new id."""
    gid, (lead, b, c) = assigned_team(mk, 3)
    sid = "pagestart01"
    assert post(b, f"/api/groups/{gid}/role-interview/start", {"action_id": sid}).status_code == 202
    finish_interview(lead, gid)
    tid = "todostart01"
    assert post(lead, f"/api/groups/{gid}/todos/start", {"action_id": tid}).status_code == 202
    rid = ask(lead, gid, b.id).json()["request"]["request_id"]
    assert respond(b, gid, rid).status_code == 201
    r = post(b, f"/api/groups/{gid}/role-interview/start", {"action_id": sid})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_REUSED"
    assert post(b, f"/api/groups/{gid}/role-interview/start", {"action_id": uuid.uuid4().hex}).status_code == 202
    r = post(lead, f"/api/groups/{gid}/todos/start", {"action_id": tid})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTION_REUSED"
    assert post(lead, f"/api/groups/{gid}/todos/start", {"action_id": uuid.uuid4().hex}).status_code == 202
    js = (ROOT / "src" / "moim" / "static" / "chat.js").read_text(encoding="utf-8")
    assert "startRev !== rev" in js and "todoStartRev !== trev" in js
