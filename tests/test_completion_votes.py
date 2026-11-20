"""D-H1 deterministic contract: AI-or-team completion, the team vote, leader group deletion and the dashboard data.

- A todo is completed only by an M2 PASS or by more than half of the submitter's other current members agreeing after
  any other M2 outcome. No checkbox completion.
- The voters are current active members other than the submitter; a member who leaves stops counting; with nobody
  else left the attempt completes on its own (SELF). An attempt is rejected once the majority can no longer agree.
- Only the leader deletes a group, after confirming the impact; everything goes for every member; the deletion ledger
  gets a GROUP_DELETED line that a restore replays.
- The dashboard API shows the caller's own groups only.
"""
from __future__ import annotations

import json
import random

import pytest

from moim.account.ledger import DeletionLedger
from moim.config import load_settings
from moim.main import create_app
from test_execution import ExecAI, confirmed_team, ex, hwp, jpost, mine, png, review_for, rv, submit  # noqa: F401
from test_groups import db, fake, grp_db, scalar, user_row  # noqa: F401
from test_role_assignment import ROOT, rows, seed  # noqa: F401

SRC = ROOT / "src" / "moim"


@pytest.fixture()
def ai():
    return ExecAI()


@pytest.fixture()
def uploads(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture()
def ledger_dir(tmp_path):
    d = tmp_path / "ledger"
    d.mkdir()
    return d


@pytest.fixture()
def app(db, fake, ai, seed, uploads, ledger_dir):
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
                             rng_factory=lambda: random.Random(seed["value"]), upload_dir=uploads,
                             deletion_ledger_dir=ledger_dir)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


def hold(ai, msg="화면 3장 중 1장만 보여요"):
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "NOT_MET", msg))


def vote(u, gid, rid, agree):
    return jpost(u, f"/api/groups/{gid}/records/{rid}/vote", {"agree": agree})


def feed_item(u, gid, rid):
    return next(f for f in ex(u, gid)["feed"] if f["id"] == rid)


# ---- the team vote ----------------------------------------------------------------------------------------------------


def test_a_held_attempt_needs_a_majority_of_the_other_members(db, mk, ai):
    gid, (a, b, c, d) = confirmed_team(mk, 4)
    hold(ai)
    r = submit(a, gid, mine(a, gid), files=[("w.png", png())])
    rid = r.json()["id"]
    assert rv(a, gid, r)["completion"] == "VOTING"
    # the voters see the AI's one-line reason, never the submitter's own review block
    f = feed_item(b, gid, rid)
    assert "review" not in f and f["vote"]["reason"] == "AI 확인: 보완하면 좋은 점이 있어요 — 화면 3장 중 1장만 보여요"
    assert (f["vote"]["voters"], f["vote"]["need"], f["vote"]["can_vote"], f["vote"]["my_vote"]) == (3, 2, True, None)
    assert feed_item(a, gid, rid)["vote"]["can_vote"] is False
    assert vote(a, gid, rid, True).status_code == 404  # the submitter does not vote
    assert vote(b, gid, rid, True).json() == {"completion": "VOTING", "agree": 1, "reject": 0, "need": 2, "voters": 3}
    assert vote(b, gid, rid, True).json()["agree"] == 1  # voting again changes, never adds
    assert feed_item(b, gid, rid)["vote"]["my_vote"] is True
    assert mine(a, gid)["done_on"] is None
    assert vote(c, gid, rid, True).json()["completion"] == "DONE"
    assert mine(a, gid)["done_on"]
    assert rows(db, "select completion, completed_by, marks_done from execution_records where id=:r", r=rid)[0] == ("DONE", "TEAM", True)
    v = vote(d, gid, rid, False)
    assert v.status_code == 409 and v.json()["error"]["code"] == "NOT_VOTING"
    done = next(f for f in ex(b, gid)["feed"] if f["title"] == mine(a, gid)["title"] or f.get("vote", {}).get("by") == "TEAM")
    assert done["vote"]["state"] == "DONE" and done["id"] is None  # the record id is only exposed while voting
    kinds = [x.detail for x in rows(db, "select detail from execution_events where kind in ('VOTE','SETTLED') order by id")]
    assert kinds == ["AGREE", "AGREE", "AGREE", "DONE:TEAM"]


def test_an_attempt_is_rejected_once_a_majority_can_no_longer_agree_and_can_be_resubmitted(db, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    hold(ai)
    t = mine(a, gid)
    rid = submit(a, gid, t, files=[("w.png", png())]).json()["id"]
    assert vote(b, gid, rid, False).json()["completion"] == "REJECTED"  # 2 voters, 2 needed: one no decides it
    t = mine(a, gid)
    assert t["done_on"] is None and t["attempt"]["completion"] == "REJECTED"
    assert vote(c, gid, rid, True).status_code == 409
    r = submit(a, gid, t, files=[("w2.png", png())])  # a new attempt: this one passes
    assert r.status_code == 201 and rv(a, gid, r)["completion"] == "DONE"


def test_a_tie_is_not_a_majority(db, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "UNKNOWN", "자료가 더 필요해요"))
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    assert vote(b, gid, rid, True).json()["completion"] == "VOTING"
    assert vote(c, gid, rid, False).json()["completion"] == "REJECTED"


def test_votes_of_a_member_who_left_stop_counting_and_a_lone_member_completes_alone(db, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    hold(ai)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    assert vote(b, gid, rid, True).json() == {"completion": "VOTING", "agree": 1, "reject": 0, "need": 2, "voters": 2}
    # c leaves: one voter is left, b's agreement is now the majority
    assert jpost(c, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200
    assert mine(a, gid)["done_on"]
    # b leaves too: a is alone, nothing they attach needs a vote (SELF)
    assert jpost(b, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200
    r = submit(a, gid, mine(a, gid, 1), files=[("a.hwp", hwp())])
    assert r.json()["completion"] == "DONE"
    assert rows(db, "select completed_by from execution_records where id=:r", r=r.json()["id"])[0].completed_by == "SELF"


def test_a_submitter_who_leaves_withdraws_their_open_vote(db, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    hold(ai)
    rid = submit(b, gid, mine(b, gid), files=[("w.png", png())]).json()["id"]
    assert jpost(b, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200
    assert rows(db, "select completion from execution_records where id=:r", r=rid)[0].completion == "WITHDRAWN"
    f = next(x for x in ex(a, gid)["feed"] if x["who"] == "left")
    assert "vote" not in f and f["id"] is None


def test_outsiders_cannot_vote_and_ids_are_checked(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    gid2, (c, d) = confirmed_team(mk, 2, leader="other")
    hold(ai)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    assert vote(c, gid, rid, True).status_code == 404
    assert vote(c, gid2, rid, True).status_code == 404
    assert vote(b, gid, "not-an-id", True).status_code == 404
    assert jpost(b, f"/api/groups/{gid}/records/{rid}/vote", {"agree": "yes"}).status_code == 400
    assert mine(a, gid)["done_on"] is None


def test_a_note_never_becomes_a_completion(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    r = submit(a, gid, mine(a, gid), memo="진행 상황만 공유해요")
    assert r.status_code == 201 and r.json()["completion"] is None and not ai.m2()
    assert vote(b, gid, r.json()["id"], True).status_code == 404
    assert mine(a, gid)["done_on"] is None


# ---- the leader deletes the group -----------------------------------------------------------------------------------


def test_only_the_leader_deletes_the_group_after_confirming(db, app, mk, ai, uploads, ledger_dir):
    gid, (lead, b) = confirmed_team(mk, 2)
    submit(b, gid, mine(b, gid), files=[("w.png", png())])
    assert jpost(b, f"/api/groups/{gid}/delete", {"confirm": True}).status_code == 403
    assert b.c.get(f"/api/groups/{gid}/delete").status_code == 403
    impact = lead.c.get(f"/api/groups/{gid}/delete").json()["impact"]
    assert impact["members"] == 2 and impact["records"] == 1 and impact["files"] == 1
    r = jpost(lead, f"/api/groups/{gid}/delete", {"confirm": False})
    assert r.status_code == 409 and r.json()["error"]["impact"] == impact
    assert lead.c.post(f"/api/groups/{gid}/delete", json={"confirm": True}).status_code == 403  # CSRF
    assert jpost(lead, f"/api/groups/{gid}/delete", {"confirm": True}).json()["deleted"] is True
    for u in (lead, b):
        assert u.c.get(f"/api/groups/{gid}/execution").status_code == 404
        assert u.c.get(f"/groups/{gid}", follow_redirects=False).status_code in (303, 404)
    for table in ("groups", "execution_records", "execution_files", "group_members", "todo_confirmed_items"):
        col = "id" if table == "groups" else "group_id"
        assert scalar(db, f"select count(*) from {table} where {col}=:g", g=gid) == 0
    assert not [p for p in uploads.rglob("*") if p.is_file()]
    kinds = [json.loads(x)["kind"] for p in ledger_dir.iterdir() for x in p.read_text(encoding="utf-8").splitlines()]
    assert kinds == ["GROUP_DELETED"]
    assert rows(db, "select detail from lifecycle_events where kind='GROUP_DELETED'")[0].detail == "LEADER_DELETED"


def test_a_group_deletion_is_replayed_after_a_restore(db, app, mk, ai):
    gid, (lead, b) = confirmed_team(mk, 2)
    led = DeletionLedger(None)
    entry = {"kind": "GROUP_DELETED", "user_id": lead.id, "group_id": gid, "record_id": None}
    out = app.state.lifecycle.replay([entry])
    assert out["applied"] == 1 and scalar(db, "select count(*) from groups where id=:g", g=gid) == 0
    assert app.state.lifecycle.replay([entry])["already"] == 1
    assert led.enabled is False


# ---- dashboard ----------------------------------------------------------------------------------------------------------


def test_dashboard_api_shows_my_groups_todos_votes_and_news_only(db, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    gid2, (c, d) = confirmed_team(mk, 2, leader="other")
    hold(ai)
    submit(b, gid, mine(b, gid), files=[("w.png", png())])
    submit(c, gid2, mine(c, gid2), files=[("w.png", png())])
    data = a.c.get("/api/dashboard").json()
    assert [g["id"] for g in data["groups"]] == [gid]
    g = data["groups"][0]
    assert g["href"] == f"/groups/{gid}" and g["total"] >= 4 and g["done"] == 0
    assert {t["state"] for t in g["todos"]} == {"OPEN"} and len(g["todos"]) == len(ex(a, gid)["my_todos"])
    assert len(g["requests"]) == 1 and g["requests"][0]["need"] == 1
    assert [n["kind"] for n in data["news"]] == ["VOTING"] and all(n["group_id"] == gid for n in data["news"])
    blob = json.dumps(data, ensure_ascii=False)
    assert gid2 not in blob and "deliverable" not in blob and "completion_criteria" not in blob
    bd = b.c.get("/api/dashboard").json()["groups"][0]
    assert [t["state"] for t in bd["todos"]].count("VOTING") == 1 and bd["requests"] == []
    a.c.cookies.delete("moim_session")
    assert a.c.get("/api/dashboard").status_code == 401


def test_dashboard_and_group_pages_link_the_logo_to_the_dashboard_and_have_no_checkbox_completion():
    for name in ("dashboard.html", "group.html", "invite.html"):
        tpl = (SRC / "templates" / name).read_text(encoding="utf-8")
        assert '<a class="logo" href="/dashboard">' in tpl, name
    group = (SRC / "templates" / "group.html").read_text(encoding="utf-8")
    assert 'id="upDone"' not in group and "완료로 표시" not in group
    dash = (SRC / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for part in ('id="newsList"', 'id="week"', 'id="dayList"', 'id="deadlines"'):
        assert part in dash
    for js in ("dashboard.js", "group-exec.js", "group-leave.js"):
        src = (SRC / "static" / js).read_text(encoding="utf-8")
        assert "innerHTML" not in src and "localStorage" not in src and "marks_done" not in src
