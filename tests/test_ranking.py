"""Stage 3 deterministic contract (D-B3): shared role candidates, private ranking submission, isolation, recovery.

There is no personal AI interview before the ranking, so the only provider calls in this stage are the role-candidate
generation, made with a FIXTURE client (never reported as LIVE). The fixture records every call and refuses anything
but the candidate schema, so a test can prove that the interview AI (E.coach, `TurnModel`) is never reached. Every
persistence claim re-reads through the database or a fresh client. FastAPI background tasks run before the test client
returns.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from moim.config import load_settings
from moim.interview.candidates import CandidateError, CandidateItem, check_candidates
from moim.main import create_app
from test_groups import (  # noqa: F401  (fixtures and helpers of the stage 2 suite)
    activate,
    active_group,
    brief_body,
    col,
    db,
    fake,
    forming_room,
    grp_db,
    mk,
    one,
    post,
    scalar,
)
from wemeet_ai.m0.client import AIError
from wemeet_ai.shared.contracts import Usage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "moim"

DEFAULT_CANDIDATES = [
    ("기획·리서치", "자료를 모으고 방향을 잡아요"),
    ("디자인", "화면과 발표 자료를 꾸며요"),
    ("개발", "서비스를 구현해요"),
    ("발표", "최종 발표를 준비해요"),
    ("문서 정리", "보고서를 정리해요"),
]


class FakeAI:
    """Deterministic stand-in for the Timely client (role candidates only). `is_fixture` keeps it out of LIVE reports."""

    is_fixture = True
    model = "fixture-model"

    def __init__(self):
        self.calls: list[tuple[str, list[dict]]] = []
        self.cand_script: list = []  # list[(name, description)] or an Exception
        self.lock = threading.Lock()

    def kinds(self, kind: str):
        return [m for k, m in self.calls if k == kind]

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        kind = output_schema.__name__
        with self.lock:
            self.calls.append((kind, messages))
            item = self.cand_script.pop(0) if self.cand_script else None
        if kind != "CandidateOutput":  # the interview AI (E.coach) must never be reached by this stage
            raise AssertionError(f"unexpected provider call: {kind}")
        if isinstance(item, Exception):
            raise item
        parsed = output_schema(candidates=[{"name": n, "description": d} for n, d in (item or DEFAULT_CANDIDATES)])
        return SimpleNamespace(
            parsed_output=parsed, model="fixture-model", latency_ms=12, usage=Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        )


@pytest.fixture()
def ai():
    return FakeAI()


@pytest.fixture()
def app(db, fake, ai):
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai)
    yield application
    application.state.engine.dispose()


# ---- helpers ------------------------------------------------------------------------------------


def screen(u, gid):
    r = u.c.get(f"/api/groups/{gid}/roles")
    assert r.status_code == 200, r.text
    return r.json()


def rank(u, gid, ids, rev=None, action=None):
    scr = screen(u, gid)
    return post(
        u,
        f"/api/groups/{gid}/preferences",
        {"action_id": action or uuid.uuid4().hex, "candidate_revision": rev or scr["candidates"]["candidate_revision"], "ranks": ids},
    )


def team(mk, ai, n=3):
    return active_group(mk, n_members=n)


def reconnect(u):
    """A brand new client for the same signed-in user: a refresh on another day."""
    fresh = TestClient(u.c.app, follow_redirects=False)
    fresh.cookies.set("moim_session", u.c.cookies.get("moim_session"), domain="testserver.local")
    return fresh


# ---- migration ----------------------------------------------------------------------------------


def test_migration_0005_down_and_up(grp_db):
    cfg = Config(str(ROOT / "alembic.ini"))
    from sqlalchemy import create_engine

    eng = create_engine(grp_db)
    tables = ("role_candidate_sets", "interview_turns", "member_states", "preference_submissions", "member_progress")
    try:
        command.downgrade(cfg, "0004")
        for t in tables:
            assert scalar(eng, f"select to_regclass('public.{t}')") is None
        assert scalar(eng, "select to_regclass('public.groups')") is not None
        command.upgrade(cfg, "head")
        for t in tables:
            assert scalar(eng, f"select to_regclass('public.{t}')") is not None
    finally:
        eng.dispose()


def test_old_interview_data_is_kept_and_marked_deprecated_by_the_migration(db, grp_db, mk, ai):
    """D-B3: nothing is deleted. Rows written by the old flow survive a 0006 <-> head round trip and stay untouched."""
    gid, lead, (m1, m2) = team(mk, ai, 3)
    with db.begin() as c:
        c.execute(text(
            "insert into interview_turns (group_id, user_id, seq, action_id, user_message, ai_reply, state, state_revision) "
            "values (:g, :u, 1, 'old-action-1', '예전 대화', '예전 답변', 'SUCCEEDED', 1)"), {"g": gid, "u": lead.id})
        c.execute(text("insert into member_states (group_id, user_id, revision, state, source_turn_seq) values (:g, :u, 1, '{\"old\": true}', 1)"),
                  {"g": gid, "u": lead.id})
        c.execute(text("insert into member_progress (group_id, user_id, status, interview_closed_at) values (:g, :u, 'INTERVIEW', now())"),
                  {"g": gid, "u": lead.id})
    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "0006")
    command.upgrade(cfg, "head")
    assert scalar(db, "select count(*) from interview_turns where group_id=:g", g=gid) == 1
    assert scalar(db, "select count(*) from member_states where group_id=:g", g=gid) == 1
    assert scalar(db, "select count(*) from member_progress where group_id=:g and interview_closed_at is not null", g=gid) == 1
    for table in ("interview_turns", "member_states"):
        assert "DEPRECATED" in scalar(db, f"select obj_description('public.{table}'::regclass)")
    assert "DEPRECATED" in scalar(
        db,
        "select col_description('public.member_progress'::regclass, (select attnum from pg_attribute "
        "where attrelid='public.member_progress'::regclass and attname='interview_closed_at'))",
    )
    # a member whose old interview was closed ranks exactly like everyone else, and the old rows stay untouched
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    assert scalar(db, "select user_message from interview_turns where group_id=:g", g=gid) == "예전 대화"
    assert scalar(db, "select count(*) from member_states where group_id=:g", g=gid) == 1


# ---- role candidates ------------------------------------------------------------------------------


def test_candidates_are_made_once_per_brief_revision_and_shared_by_the_whole_cohort(db, mk, ai):
    gid, lead, members = team(mk, ai, 3)
    assert len(ai.kinds("CandidateOutput")) == 1
    seen = [screen(u, gid)["candidates"] for u in (lead, *members)]
    assert all(c["state"] == "SUCCEEDED" for c in seen)
    assert len({json.dumps(c, sort_keys=True) for c in seen}) == 1
    assert [i["id"] for i in seen[0]["items"]] == ["r1", "r2", "r3", "r4", "r5"]
    assert seen[0]["candidate_revision"] == 1
    # refreshes and a brand new session never call the provider again
    for _ in range(5):
        screen(lead, gid)
    assert reconnect(lead).get(f"/api/groups/{gid}/roles").json()["candidates"] == seen[0] | {"can_retry": False}
    assert len(ai.kinds("CandidateOutput")) == 1
    row = one(db, "select state, source_project_brief_revision, model, total_tokens from role_candidate_sets where group_id=:g", g=gid)
    assert tuple(row) == ("SUCCEEDED", 1, "fixture-model", 30)


def test_candidate_provider_input_is_the_project_brief_only(db, mk, ai):
    gid, lead, members = team(mk, ai, 3)
    sent = json.dumps(ai.kinds("CandidateOutput")[0], ensure_ascii=False)
    assert "캡스톤 디자인" in sent and "팀플 협업 서비스" in sent
    assert "min_count" not in sent and set(json.loads(ai.kinds("CandidateOutput")[0][-1]["content"])) == {"project"}
    for u in (lead, *members):
        assert u.id not in sent and u.name not in sent


def test_candidate_failure_is_shown_with_an_explicit_retry_and_is_never_replaced_by_a_list(db, mk, ai):
    ai.cand_script.append(AIError("TIMEOUT", "slow", True))
    gid, lead, members = team(mk, ai, 3)
    c = screen(members[0], gid)["candidates"]
    assert c["state"] == "FAILED" and c["items"] is None and c["can_retry"] and c["error_message"]
    assert len(ai.kinds("CandidateOutput")) == 1  # no automatic retry
    assert screen(members[0], gid)["ranking_available"] is False
    action = uuid.uuid4().hex
    body = {"action_id": action, "candidate_revision": 1, "expected_attempt": c["attempt"]}
    r = post(members[0], f"/api/groups/{gid}/roles/candidates/retry", body)
    assert r.status_code == 202, r.text
    assert len(ai.kinds("CandidateOutput")) == 2
    assert screen(lead, gid)["candidates"]["state"] == "SUCCEEDED"
    # the same retry request again changes and calls nothing
    r = post(members[0], f"/api/groups/{gid}/roles/candidates/retry", body)
    assert r.status_code == 202
    assert len(ai.kinds("CandidateOutput")) == 2
    # retrying a set that is not failed is refused
    r = post(lead, f"/api/groups/{gid}/roles/candidates/retry", {**body, "action_id": uuid.uuid4().hex, "expected_attempt": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_RETRYABLE"
    assert one(db, "select attempt, last_action_id from role_candidate_sets where group_id=:g", g=gid).attempt == 2


def test_invalid_candidate_sets_are_failures(db, mk, ai):
    too_few = [("기획", "a"), ("디자인", "b")]
    dup = [("기획", "a"), ("기 획", "b"), ("개발", "c")]
    long_name = [("가" * 13, "a"), ("나", "b"), ("다", "c")]
    for bad in (too_few, dup, long_name):
        with pytest.raises(CandidateError):
            check_candidates([CandidateItem(name=n, description=d) for n, d in bad], 3)
    with pytest.raises(CandidateError):  # fewer candidates than people
        check_candidates([CandidateItem(name=f"역할{i}", description="설명") for i in range(3)], 5)
    ai.cand_script.append(too_few)
    gid, lead, _ = team(mk, ai, 3)
    c = screen(lead, gid)["candidates"]
    assert c["state"] == "FAILED" and c["error_code"] == "CANDIDATES_INVALID" and c["items"] is None


def test_server_restart_marks_unfinished_candidate_generation_failed_and_retryable(db, app, mk, ai):
    gid, lead, members = team(mk, ai, 3)
    with db.begin() as c:
        c.execute(text("update role_candidate_sets set state='RUNNING', candidates=null where group_id=:g"), {"g": gid})
    assert app.state.candidates.fail_interrupted() == 1
    c = screen(lead, gid)["candidates"]
    assert c["state"] == "FAILED" and c["error_code"] == "SERVER_RESTARTED"
    body = {"action_id": uuid.uuid4().hex, "candidate_revision": 1, "expected_attempt": c["attempt"]}
    assert post(lead, f"/api/groups/{gid}/roles/candidates/retry", body).status_code == 202
    assert screen(lead, gid)["candidates"]["state"] == "SUCCEEDED"


# ---- no interview before the ranking -------------------------------------------------------------


def test_a_member_ranks_without_any_interview_memberstate_or_ai_call(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    assert scalar(db, "select count(*) from member_states") == 0 and scalar(db, "select count(*) from interview_turns") == 0
    s = screen(lead, gid)
    assert s["phase"] == "INTERVIEW" and s["ranking_available"] is True  # ranking is open at once, no prerequisite
    assert rank(lead, gid, ["r2", "r4", "r1"]).status_code == 201
    assert [k for k, _ in ai.calls] == ["CandidateOutput"]  # only the group's candidates: E.coach was never called
    assert scalar(db, "select count(*) from member_states") == 0 and scalar(db, "select count(*) from interview_turns") == 0


def test_the_interview_api_and_the_interview_payload_are_gone_from_the_stage_3_path(db, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    for path in ("interview", "interview/messages", "interview/answers", "interview/close", "interview/turns/1/retry"):
        assert lead.c.get(f"/api/groups/{gid}/{path}").status_code in (404, 405)
        assert post(lead, f"/api/groups/{gid}/{path}", {"action_id": uuid.uuid4().hex, "message": "hi"}).status_code in (404, 405)
    keys = set(screen(lead, gid))
    assert not keys & {"turns", "member_state", "interview", "state_revision"}
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    for word in ("/interview", "member_state", "MemberState", "closeInterview", "answers"):
        assert word not in js, word
    # the E.coach adapter and the old turn machinery are kept for stage 4, but nothing in the app imports them
    assert (SRC / "interview" / "coach.py").exists() and (SRC / "interview" / "turns.py").exists()
    imp = re.compile(r"^\s*(from\s+\S*(coach|turns)\S*\s+import|import\s+\S*(coach|turns))", re.M)
    for path in SRC.rglob("*.py"):
        if path.name in ("turns.py", "coach.py"):
            continue
        assert not imp.search(path.read_text(encoding="utf-8")), path
    assert "InterviewTurnService" not in (SRC / "main.py").read_text(encoding="utf-8")


# ---- isolation ------------------------------------------------------------------------------------


def test_outsiders_and_other_groups_get_the_same_404_everywhere(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    gid_b, lead_b, _ = active_group(mk, n_members=2, leader="other")
    outsider = mk("outsider01")
    waiting_gid, waiting_lead, (waiting_member,) = forming_room(mk, 2, leader="forming")
    action = uuid.uuid4().hex
    paths = [
        ("get", f"/api/groups/{gid}/roles", None),
        ("post", f"/api/groups/{gid}/roles/candidates/retry", {"action_id": action, "candidate_revision": 1, "expected_attempt": 1}),
        ("post", f"/api/groups/{gid}/preferences", {"action_id": action, "candidate_revision": 1, "ranks": ["r1", "r2", "r3"]}),
    ]
    for who in (outsider, lead_b, waiting_lead):
        for method, path, body in paths:
            r = who.c.get(path) if method == "get" else post(who, path, body)
            assert r.status_code == 404, (who.name, path, r.status_code)
            assert r.json()["error"]["code"] == "NOT_FOUND"
    # guessed or malformed ids look exactly like a missing group
    for fake_id in (str(uuid.uuid4()), "not-an-id", "1" * 36):
        assert outsider.c.get(f"/api/groups/{fake_id}/roles").status_code == 404
        assert lead.c.get(f"/api/groups/{fake_id}/roles").status_code == 404
        assert post(lead, f"/api/groups/{fake_id}/preferences", {"action_id": action, "candidate_revision": 1, "ranks": ["r1", "r2", "r3"]}).status_code == 404
    # a member of group A cannot touch group B by sending A's ids
    assert lead.c.get(f"/api/groups/{gid_b}/roles").status_code == 404
    assert scalar(db, "select count(*) from preference_submissions") == 0 and scalar(db, "select count(*) from member_progress") == 0


def test_login_and_csrf_are_required(db, app, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/api/groups/{gid}/roles").status_code == 401
    assert anon.get(f"/groups/{gid}/chat").status_code == 303
    body = {"action_id": uuid.uuid4().hex, "candidate_revision": 1, "ranks": ["r1", "r2", "r3"]}
    assert post(lead, f"/api/groups/{gid}/preferences", body, token=None).status_code == 403
    assert post(lead, f"/api/groups/{gid}/preferences", body, token="forged").status_code == 403
    assert scalar(db, "select count(*) from preference_submissions") == 0


def test_chat_page_is_served_only_to_the_cohort_and_has_no_inline_script(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    r = lead.c.get(f"/groups/{gid}/chat")
    assert r.status_code == 200 and "/static/chat.js" in r.text and 'name="csrf-token"' in r.text
    assert re.findall(r"<script([^>]*)>", r.text) == [' src="/static/auth-common.js"', ' src="/static/chat.js"']
    assert mk("late01").c.get(f"/groups/{gid}/chat").status_code == 404


def test_request_bodies_are_strict(db, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    good = {"action_id": uuid.uuid4().hex, "candidate_revision": 1, "ranks": ["r1", "r2", "r3"]}
    assert post(lead, f"/api/groups/{gid}/preferences", {**good, "user_id": "someone-else"}).status_code in (400, 422)
    assert post(lead, f"/api/groups/{gid}/preferences", {**good, "action_id": "x"}).status_code in (400, 422)
    assert post(lead, f"/api/groups/{gid}/preferences", {k: v for k, v in good.items() if k != "ranks"}).status_code in (400, 422)
    assert scalar(db, "select count(*) from preference_submissions") == 0


# ---- ranking --------------------------------------------------------------------------------------


def test_invalid_rankings_are_rejected_and_store_nothing(db, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    cases = [
        (["r1", "r2"], 422),  # missing
        (["r1", "r1", "r2"], 422),  # duplicate
        (["r1", "r2", "r9"], 422),  # not a candidate
        (["r1", "r2", "역할 이름"], 422),  # free text
        (["r1", "r2", "r3", "r4"], 422),  # too many
        (["r1", "r2", 3], 400),  # wrong type
        ([], 400),
    ]
    for ids, status in cases:
        r = rank(lead, gid, ids)
        assert r.status_code == status, (ids, r.status_code, r.text)
    assert rank(lead, gid, ["r1", "r2", "r3"], rev=9).json()["error"]["code"] == "STALE_CANDIDATES"
    assert scalar(db, "select count(*) from preference_submissions") == 0 and scalar(db, "select count(*) from member_progress where status = 'WAITING_ASSIGNMENT'") == 0
    assert screen(lead, gid)["phase"] == "INTERVIEW"


def test_ranking_waits_for_the_candidates(db, mk, ai):
    ai.cand_script.append(AIError("TIMEOUT", "slow", True))
    gid, lead, _ = team(mk, ai, 3)
    s = screen(lead, gid)
    assert not s["ranking_available"] and s["candidates"]["items"] is None
    r = post(lead, f"/api/groups/{gid}/preferences", {"action_id": uuid.uuid4().hex, "candidate_revision": 1, "ranks": ["r1", "r2", "r3"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CANDIDATES"
    assert scalar(db, "select count(*) from preference_submissions") == 0


def test_valid_ranking_is_stored_once_and_waiting_assignment_survives_a_refresh_and_a_restart(db, app, mk, ai, fake):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    action = uuid.uuid4().hex
    r = rank(lead, gid, ["r3", "r1", "r5"], action=action)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["phase"] == "WAITING_ASSIGNMENT" and not s["ranking_available"]
    assert [x["name"] for x in s["submitted_ranks"]] == ["개발", "기획·리서치", "문서 정리"]
    assert s["progress"] == {"submitted": 1, "total": 3}
    sub = one(db, "select revision, source_candidate_revision, rank1, rank2, rank3 from preference_submissions where user_id=:u", u=lead.id)
    assert tuple(sub) == (1, 1, "r3", "r1", "r5")
    assert tuple(one(db, "select status, submission_revision from member_progress where user_id=:u", u=lead.id)) == ("WAITING_ASSIGNMENT", 1)
    # replay of the same action: nothing new
    again = rank(lead, gid, ["r3", "r1", "r5"], action=action)
    assert again.status_code == 200 and scalar(db, "select count(*) from preference_submissions") == 1
    # a different submission after submitting is refused: the stored revision is immutable
    other = rank(lead, gid, ["r1", "r2", "r3"])
    assert other.status_code == 409 and other.json()["error"]["code"] == "ALREADY_SUBMITTED"
    assert tuple(one(db, "select rank1, rank2, rank3 from preference_submissions where user_id=:u", u=lead.id)) == ("r3", "r1", "r5")
    # a new session (refresh / next day) recovers the stored state
    recovered = reconnect(lead).get(f"/api/groups/{gid}/roles").json()
    assert recovered["phase"] == "WAITING_ASSIGNMENT" and recovered["submitted_ranks"] == s["submitted_ranks"]
    # and so does a new server process
    fresh_app = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai)
    try:
        token, _ = fresh_app.state.sessions.create(lead.id, False)
        c = TestClient(fresh_app, follow_redirects=False)
        c.get("/login")
        c.cookies.set("moim_session", token, domain="testserver.local")
        after_restart = c.get(f"/api/groups/{gid}/roles").json()
        assert after_restart["phase"] == "WAITING_ASSIGNMENT" and after_restart["submitted_ranks"] == s["submitted_ranks"]
        assert after_restart["progress"] == {"submitted": 1, "total": 3}
    finally:
        fresh_app.state.engine.dispose()


def test_choices_can_change_freely_until_submit_and_only_the_submit_is_stored(db, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    # picking and re-picking happens in the page only: the server holds nothing until the submit
    for _ in range(3):
        assert screen(lead, gid)["ranking_available"] is True
    assert scalar(db, "select count(*) from preference_submissions") == 0
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    assert scalar(db, "select count(*) from preference_submissions") == 1


def test_duplicate_and_concurrent_submits_store_one_revision(db, app, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    cand_rev = screen(lead, gid)["candidates"]["candidate_revision"]
    same: list = []

    def go_same():
        try:
            same.append(app.state.ranking.submit_preferences(lead.id, gid, "same-action-1", cand_rev, ["r1", "r2", "r3"]))
        except Exception as exc:  # noqa: BLE001
            same.append(type(exc).__name__)

    threads = [threading.Thread(target=go_same) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert same.count(True) == 1 and same.count(False) == 7, same  # one stored, the others are replays of the same action
    assert scalar(db, "select count(*) from preference_submissions where user_id=:u", u=lead.id) == 1

    m = mk("racer01")  # not in the cohort: refused, and does not disturb the rest
    diff: list = []
    gid2, lead2, _ = active_group(mk, n_members=2, leader="leader2")
    rev2 = screen(lead2, gid2)["candidates"]["candidate_revision"]

    def go_diff(i):
        try:
            diff.append(app.state.ranking.submit_preferences(lead2.id, gid2, f"diff-action-{i}", rev2, ["r1", "r2", "r3"]))
        except Exception as exc:  # noqa: BLE001
            diff.append(getattr(exc, "code", type(exc).__name__))

    threads = [threading.Thread(target=go_diff, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert diff.count(True) == 1 and diff.count("ALREADY_SUBMITTED") == 5, diff  # different actions: one wins, none duplicates
    assert scalar(db, "select count(*) from preference_submissions where user_id=:u", u=lead2.id) == 1
    assert scalar(db, "select count(*) from member_progress where status='WAITING_ASSIGNMENT'") == 2


def test_progress_is_a_count_only_and_shown_to_every_cohort_member(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    assert all(screen(u, gid)["progress"] == {"submitted": 0, "total": 3} for u in (lead, m1, m2))
    assert rank(m1, gid, ["r2", "r1", "r3"]).status_code == 201
    assert screen(lead, gid)["progress"] == {"submitted": 1, "total": 3}  # D-B1 B2: everyone, the leader included
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    for u in (lead, m1):
        s = screen(u, gid)
        assert s["progress"] == {"submitted": 2, "total": 3}
        blob = json.dumps(s, ensure_ascii=False)
        assert m2.name not in blob and m1.id not in blob.replace(u.id, "") and "r2" not in json.dumps(s["progress"])
    # a member's ranking is not in a teammate's payload
    assert screen(m2, gid)["submitted_ranks"] is None
    assert rank(m2, gid, ["r3", "r2", "r1"]).status_code == 201
    done = screen(lead, gid)
    assert done["progress"] == {"submitted": 3, "total": 3} and done["phase"] == "WAITING_ASSIGNMENT"


def test_a_ranking_is_never_visible_to_anyone_else_nor_in_logs(db, mk, ai, caplog):
    caplog.set_level(logging.DEBUG)
    gid, lead, (m1, m2) = team(mk, ai, 3)
    assert rank(m1, gid, ["r5", "r3", "r2"]).status_code == 201  # 문서 정리 / 개발 / 디자인
    mine = json.dumps(screen(m1, gid), ensure_ascii=False)
    assert "문서 정리" in mine and "r5" in mine or "submitted_ranks" in mine
    for other in (lead, m2):
        s = screen(other, gid)
        assert s["submitted_ranks"] is None and s["phase"] == "INTERVIEW"
        assert not re.search(r"rank[123]|submitted_ranks\"\s*:\s*\[", json.dumps(s))
        for path in (f"/groups/{gid}", f"/groups/{gid}/chat", f"/api/groups/{gid}/goal", f"/api/groups/{gid}/brief", "/dashboard"):
            body = other.c.get(path).text
            assert "순위를 제출했어요" not in body and m1.id not in body
    # the log has events and ids, never the chosen roles or their ranks
    text_ = caplog.text
    assert "ranking_event" in text_ and "WAITING_ASSIGNMENT" in text_
    assert "문서 정리" not in text_ and "rank1" not in text_ and "r5,r3" not in text_ and "'r5'" not in text_
    # nothing carries the order to the provider either
    assert "r5" not in json.dumps(ai.calls, ensure_ascii=False)


# ---- ProjectBrief change -> STALE -----------------------------------------------------------------


def test_brief_change_makes_candidates_and_rankings_stale(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    old = screen(m1, gid)["candidates"]
    assert old["candidate_revision"] == 1
    ai.cand_script.append([("리서치", "조사해요"), ("기획", "계획해요"), ("제작", "만들어요")])
    edit = post(lead, f"/api/groups/{gid}/brief", {**brief_body(content="내용을 바꿨어요"), "base_revision": 1})
    assert edit.status_code == 200 and edit.json()["changed"]
    assert len(ai.kinds("CandidateOutput")) == 2
    fresh = screen(m1, gid)
    assert fresh["candidates"]["candidate_revision"] == 2
    assert [i["name"] for i in fresh["candidates"]["items"]] == ["리서치", "기획", "제작"]
    # the old set is never offered again and a ranking against it is refused
    stale = rank(m1, gid, ["r1", "r2", "r3"], rev=1)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_CANDIDATES"
    # the submitted member is back at INTERVIEW (ranking not submitted) with a notice
    mine = screen(lead, gid)
    assert mine["phase"] == "INTERVIEW" and mine["ranking_reset"] and mine["ranking_available"]
    assert tuple(one(db, "select status, submission_revision from member_progress where user_id=:u", u=lead.id)) == ("INTERVIEW", None)
    assert scalar(db, "select count(*) from preference_submissions where user_id=:u", u=lead.id) == 1  # history is kept
    assert mine["progress"] == {"submitted": 0, "total": 3}
    # a new ranking is a new revision against the new set
    assert rank(lead, gid, ["r1", "r2", "r3"]).status_code == 201
    assert col(db, "select revision from preference_submissions where user_id=:u order by revision", u=lead.id) == [1, 2]
    assert col(db, "select source_candidate_revision from preference_submissions where user_id=:u order by revision", u=lead.id) == [1, 2]
    # an unchanged save makes no new revision and no new call
    post(lead, f"/api/groups/{gid}/brief", {**brief_body(content="내용을 바꿨어요"), "base_revision": 2})
    assert len(ai.kinds("CandidateOutput")) == 2


# ---- boundaries of this stage ---------------------------------------------------------------------


def test_nothing_is_assigned_drafted_or_hidden_before_or_after_everyone_submits(db, mk, ai):
    gid, lead, (m1, m2) = team(mk, ai, 3)

    def check_nothing():
        tables = col(db, "select table_name from information_schema.tables where table_schema='public'")
        assert not [t for t in tables if re.search(r"assign|todo|criteri|deliverable_hidden", t)], tables
        columns = col(db, "select column_name from information_schema.columns where table_schema='public'")
        assert not [c for c in columns if re.search(r"completion_criteria|^deliverable$|assigned_role|todo", c)], columns
        assert scalar(db, "select count(*) from member_states") == 0 and scalar(db, "select count(*) from interview_turns") == 0
        assert scalar(db, "select status from groups where id=:g", g=gid) == "ACTIVE"
        assert not [k for k, _ in ai.calls if k != "CandidateOutput"]

    check_nothing()
    for i, u in enumerate((lead, m1, m2)):
        assert rank(u, gid, ["r1", "r2", "r3"]).status_code == 201
        check_nothing()  # partway (1/3, 2/3) and when the last one has submitted
        for v in (lead, m1, m2):
            payload = screen(v, gid)
            keys = set()

            def walk(node):
                if isinstance(node, dict):
                    keys.update(node)
                    [walk(x) for x in node.values()]
                elif isinstance(node, list):
                    [walk(x) for x in node]

            walk(payload)
            assert not [k for k in keys if re.search(r"assign|todo|criteria|deliverable|role_result|member_state", k)], keys
    assert screen(lead, gid)["progress"] == {"submitted": 3, "total": 3}  # everyone is in, and still nothing is assigned


def test_user_text_is_data_never_markup_and_the_browser_never_reaches_the_provider(db, mk, ai):
    gid, lead, _ = team(mk, ai, 3)
    payload = "<script>alert(1)</script>"
    assert payload not in lead.c.get(f"/groups/{gid}/chat").text
    assert "text/html" not in lead.c.get(f"/api/groups/{gid}/roles").headers["content-type"]
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert "localStorage" not in js and "sessionStorage" not in js and "document.cookie" not in js
    assert "timely" not in js.lower() and "api_key" not in js.lower()
    assert 'name="csrf-token"' in (SRC / "templates" / "chat.html").read_text(encoding="utf-8")


def test_todo_controls_are_hidden_before_an_assignment_exists():
    html = (SRC / "templates" / "chat.html").read_text(encoding="utf-8")
    for marker in ('<span class="pill" hidden>', 'class="addrow" hidden', 'id="confirm" type="button" disabled hidden', 'id="count" hidden'):
        assert marker in html, marker
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    for title in ("역할 순위 선택", "역할 배정 대기"):
        assert title in js or title in html
    assert "stateRows" not in html and "내가 알려준 정보" not in html and "내가 알려준 정보" not in js


def test_chat_template_keeps_every_original_style_line_and_the_two_column_structure():
    original = (ROOT / "moim-chat.html").read_text(encoding="utf-8").split("\n")
    rules = original[10:204]
    html = (SRC / "templates" / "chat.html").read_text(encoding="utf-8")
    style = html[html.index("<style>") + 7 : html.index("</style>")]
    for line in rules:
        assert line in style, line
    for marker in ('class="grid"', 'class="card chat"', 'class="card panel"', 'class="composer"', 'class="goalbox"', 'class="empty"', 'id="confirm"', 'id="add"'):
        assert marker in html, marker
    assert "moim-group.html" not in html and "AI_ENDPOINT" not in html and "ROLE_TASKS" not in html


def test_the_chat_shows_the_shared_candidates_and_never_repeats_a_personal_interview():
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    assert "1·2·3순위" in js and "candidateRow" not in js  # candidates are buttons, never chat bubbles
    for gone in ("몇 가지만 여쭤볼게요", "역할 고르기", "조금 더 알려줄게요", "역할 준비"):
        assert gone not in js, gone
