"""Stage 4 session A deterministic contract: server role assignment, internal team coverage, post-assignment interview.

The provider is a FIXTURE client (never reported as LIVE). It records every call's messages so a test can check that no
provider input carries a name, a user id, a ranking or a raw constraint. The assignment RNG is a seeded random.Random
injected by the test; the product uses random.SystemRandom. FastAPI background tasks run before the test client returns.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from moim.config import load_settings
from moim.main import create_app
from moim.roles import interview_ai as ai_mod
from moim.roles.assignment import AssignmentService, assign_roles, capacities
from moim.roles.coverage import CoverageError, PartItem, check_coverage
from test_groups import (  # noqa: F401  (fixtures and helpers of the stage 2 suite)
    active_group,
    col,
    db,
    fake,
    grp_db,
    one,
    post,
    scalar,
    today,
    user_row,
)
from wemeet_ai.m0.client import AIError
from wemeet_ai.shared.contracts import Usage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "moim"

CANDIDATES = [
    ("기획·리서치", "자료를 모으고 방향을 잡아요"),
    ("디자인", "화면과 발표 자료를 꾸며요"),
    ("개발", "서비스를 구현해요"),
    ("발표", "최종 발표를 준비해요"),
    ("문서 정리", "보고서를 정리해요"),
]
SECRET_CONSTRAINT = "11월 시험 기간이라 화요일은 안돼요 비밀메모"


def _usage():
    return Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30)


class FakeAI:
    """Deterministic stand-in for Timely: role candidates, team coverage and interview turns. Never LIVE."""

    is_fixture = True
    model = "fixture-model"

    def __init__(self):
        self.calls: list[tuple[str, list[dict]]] = []
        self.scripts: dict[str, list] = {"CandidateOutput": [], "CoverageOutput": [], "RoleTurnOutput": []}
        self.lock = threading.Lock()

    def kinds(self, kind):
        return [m for k, m in self.calls if k == kind]

    def payloads(self, kind):
        return [json.loads(m[-1]["content"]) for m in self.kinds(kind)]

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        kind = output_schema.__name__
        with self.lock:
            self.calls.append((kind, messages))
            script = self.scripts.get(kind, [])
            item = script.pop(0) if script else None
        if isinstance(item, Exception):
            raise item
        payload = json.loads(messages[-1]["content"])
        if kind == "CandidateOutput":
            data = {"candidates": [{"name": n, "description": d} for n, d in (item or CANDIDATES)]}
        elif kind == "CoverageOutput":
            data = item if item is not None else default_coverage(payload)
        elif kind == "RoleTurnOutput":
            data = item(payload) if callable(item) else item if item is not None else default_turn(payload)
        else:
            raise AssertionError(kind)
        return SimpleNamespace(parsed_output=output_schema.model_validate(data), model="fixture-model", latency_ms=7, usage=_usage())


def default_coverage(payload):
    ds = [d["id"] for d in payload["project"]["deliverables"]]
    slots = payload["slots"]
    parts = [{"name": f"{s['role']} 담당 {s['slot_id']}", "source": ds[i % len(ds)], "slot": s["slot_id"]} for i, s in enumerate(slots)]
    for j, d in enumerate(ds[len(slots):]):
        parts.append({"name": f"추가 결과물 {d}", "source": d, "slot": slots[j % len(slots)]["slot_id"]})
    return {"parts": parts}


ROLE_QUESTIONS = {
    "experience": ("{role} 일을 해 본 적이 있나요?", [("처음이에요", "처음 해봄"), ("조금 해봤어요", "조금 해봄")]),
    "weekly_available_minutes": ("일주일에 {role} 작업에 쓸 수 있는 시간은요?", [("주 3시간", 180), ("주 6시간", 360)]),
    "tools": ("{role}에 쓸 수 있는 도구가 있나요?", [("피그마", ["피그마"]), ("파워포인트", ["파워포인트"])]),
    "preferred_times": ("{role} 작업은 언제가 편한가요?", [("평일 저녁", ["평일 저녁"]), ("주말", ["주말"])]),
    "constraints": ("{role} 마감에 영향을 줄 일정이 있나요?", [("없어요", []), ("시험 기간이 있어요", ["시험 기간"])]),
}


def turn(field=None, understood=True, value=None, quote="", text=None, options=None, extras=None, reply="", role="역할"):
    nxt = None
    if field:
        t, opts = ROLE_QUESTIONS[field]
        nxt = {"field": field, "text": text or t.format(role=role), "options": options if options is not None else [{"label": a, "value": b} for a, b in opts]}
    return {"understood": understood, "value": value, "quote": quote, "extras": extras or [], "reply": reply, "next": nxt}


def default_turn(payload):
    """Interpret a text answer as itself and ask the first required item not asked yet (then stop)."""
    role = payload["my_role"]["role"]
    value = quote = None
    if payload["answer_kind"] == "text":
        value = quote = payload["answer"]
        if payload["last_question"]["field"] == "weekly_available_minutes":
            m = re.search(r"\d+", payload["answer"])
            value, quote = (int(m[0]) * 60, m[0]) if m else (None, "")
    nxt = payload["must_choose_from"] or payload["required_unasked"]
    return turn(nxt[0] if nxt else None, value=value, quote=quote or "", role=role)


@pytest.fixture()
def ai():
    return FakeAI()


@pytest.fixture()
def seed():
    return {"value": 7}


@pytest.fixture()
def app(db, fake, ai, seed):
    application = create_app(
        load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
        rng_factory=lambda: random.Random(seed["value"]),
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


# ---- helpers ------------------------------------------------------------------------------------


def screen(u, gid):
    r = u.c.get(f"/api/groups/{gid}/roles")
    assert r.status_code == 200, r.text
    return r.json()


def rank(u, gid, ids, action=None):
    rev = screen(u, gid)["candidates"]["candidate_revision"]
    return post(u, f"/api/groups/{gid}/preferences", {"action_id": action or uuid.uuid4().hex, "candidate_revision": rev, "ranks": ids})


def team(mk, n=3, leader="leader"):
    gid, lead, members = active_group(mk, n_members=n, leader=leader)
    return gid, [lead, *members]


def assigned_team(mk, n=3, rankings=None, leader="leader"):
    gid, people = team(mk, n, leader)
    rankings = rankings or [["r1", "r2", "r3"], ["r2", "r1", "r3"], ["r3", "r1", "r2"], ["r4", "r1", "r2"], ["r5", "r1", "r2"]]
    for u, r in zip(people, rankings):
        assert rank(u, gid, r).status_code == 201
    return gid, people


def start(u, gid):
    return post(u, f"/api/groups/{gid}/role-interview/start", {"action_id": uuid.uuid4().hex})


def answer(u, gid, message=None, option=None, action=None):
    body = {"action_id": action or uuid.uuid4().hex}
    if message is not None:
        body["message"] = message
    if option is not None:
        q = screen(u, gid)["role"]["interview"]["question"]
        body["question_id"] = q["question_id"]
        body["option_id"] = option if option in [o["option_id"] for o in q["options"]] else next(
            o["option_id"] for o in q["options"] if o["label"] == option
        )
    return post(u, f"/api/groups/{gid}/role-interview/answer", body)


def iv(u, gid):
    return screen(u, gid)["role"]["interview"]


def reconnect(u):
    fresh = TestClient(u.c.app, follow_redirects=False)
    fresh.cookies.set("moim_session", u.c.cookies.get("moim_session"), domain="testserver.local")
    fresh.cookies.set("moim_csrf", u.c.cookies.get("moim_csrf"), domain="testserver.local")
    return SimpleNamespace(id=u.id, name=u.name, c=fresh)


def rows(eng, sql, **params):
    """All rows, on a connection that is always closed (an open one would block the next test's TRUNCATE)."""
    from sqlalchemy import text

    with eng.connect() as c:
        return c.execute(text(sql), params).all()


def forbidden_in(blob: str, people, extra=()):
    for p in people:  # names are matched as a whole word ("leader" is a prefix of "leaderm0")
        assert p.id not in blob, "user id leaked"
        assert not re.search(rf"(?<![0-9a-z]){re.escape(p.name)}(?![0-9a-z])", blob), "name leaked"
    for word in extra:
        assert word not in blob, word


# ---- assignment: the pure algorithm with a fixed RNG ------------------------------------------------


def test_first_choices_without_conflict_are_all_rank_one():
    placements, audit = assign_roles({"a": ("r1", "r2", "r3"), "b": ("r2", "r1", "r3"), "c": ("r3", "r2", "r1")},
                                     ["r1", "r2", "r3", "r4"], random.Random(1))
    assert {(p.user_id, p.role_id, p.rank) for p in placements} == {("a", "r1", 1), ("b", "r2", 1), ("c", "r3", 1)}
    assert audit == []  # no tie, no random draw


def test_a_tie_is_drawn_by_the_server_rng_and_the_loser_moves_to_the_next_choice():
    rankings = {"a": ("r1", "r2", "r3"), "b": ("r1", "r3", "r2")}
    winners = set()
    for s in range(12):
        placements, audit = assign_roles(rankings, ["r1", "r2", "r3"], random.Random(s))
        got = {p.user_id: (p.role_id, p.rank) for p in placements}
        assert sorted(r for r, _ in got.values()) in (["r1", "r2"], ["r1", "r3"])
        win = next(u for u, (r, _) in got.items() if r == "r1")
        lose = "b" if win == "a" else "a"
        assert got[win] == ("r1", 1) and got[lose] == (rankings[lose][1], 2)
        assert audit == [{"round": 1, "role_id": "r1", "contenders": ["a", "b"], "winners": [win]}]
        winners.add(win)
        again, _ = assign_roles(rankings, ["r1", "r2", "r3"], random.Random(s))
        assert again == placements  # the same RNG state gives the same result
    assert winners == {"a", "b"}  # the draw is really random: both can win


def test_round_two_and_three_conflicts_and_random_remaining_role():
    rankings = {u: ("r1", "r2", "r3") for u in "abcd"}  # four people want the same three roles
    placements, audit = assign_roles(rankings, ["r1", "r2", "r3", "r4", "r5"], random.Random(3))
    got = {p.user_id: (p.role_id, p.rank) for p in placements}
    assert sorted(v for v in got.values() if v[1] is not None) == [("r1", 1), ("r2", 2), ("r3", 3)]
    left = [v for v in got.values() if v[1] is None]
    assert len(left) == 1 and left[0][0] in ("r4", "r5")  # after the 3rd choice: a random role among the free ones
    assert [a["round"] for a in audit] == [1, 2, 3, "remaining"]
    assert len({p.slot_id for p in placements}) == 4


def test_duplicate_roles_only_when_roles_are_fewer_than_members_and_each_member_gets_a_slot():
    assert capacities(["r1", "r2", "r3"], 3) == {"r1": 1, "r2": 1, "r3": 1}
    assert capacities(["r1", "r2"], 4) == {"r1": 2, "r2": 2}
    rankings = {u: ("r1", "r2", "r1") for u in "abcd"}
    placements, _ = assign_roles(rankings, ["r1", "r2"], random.Random(5))
    by_role: dict = {}
    for p in placements:
        by_role.setdefault(p.role_id, []).append(p)
    assert sorted(len(v) for v in by_role.values()) == [2, 2]
    assert len({p.slot_id for p in placements}) == 4  # members of one role are told apart by their slot


def test_the_product_rng_is_system_random_with_no_seed():
    assert AssignmentService()._rng_factory is random.SystemRandom
    src = (SRC / "roles" / "assignment.py").read_text(encoding="utf-8") + (SRC / "main.py").read_text(encoding="utf-8")
    assert not re.search(r"random\.seed\(|[^m]Random\(\s*\d|Random\(seed", src)  # no fixed seed in the product


# ---- assignment: stored once ----------------------------------------------------------------------


def test_no_assignment_until_the_whole_cohort_submits_then_exactly_one(db, mk, ai):
    gid, (a, b, c) = team(mk, 3)
    for i, (u, r) in enumerate(((a, ["r1", "r2", "r3"]), (b, ["r1", "r3", "r2"]))):
        assert rank(u, gid, r).status_code == 201
        assert scalar(db, "select count(*) from role_assignments") == 0
        assert scalar(db, "select count(*) from team_coverages") == 0
        assert scalar(db, "select count(*) from role_interviews") == 0
        assert screen(u, gid)["phase"] == "WAITING_ASSIGNMENT" and screen(u, gid)["role"] is None
    assert not ai.kinds("CoverageOutput") and not ai.kinds("RoleTurnOutput")
    assert rank(c, gid, ["r2", "r1", "r3"]).status_code == 201
    row = one(db, "select revision, state, operation, source_cohort_revision, source_candidate_revision from role_assignments where group_id=:g", g=gid)
    assert tuple(row) == (1, "CURRENT", "INITIAL", 1, 1)
    assert col(db, "select status from member_progress where group_id=:g", g=gid) == ["ROLE_TODOS"] * 3
    assert scalar(db, "select count(distinct assignment_revision) from member_progress where group_id=:g", g=gid) == 1
    # the assignment keeps the exact preference submission revisions it was made from
    assert scalar(db, """select count(*) from role_assignment_members m join member_progress p
                         on p.group_id=m.group_id and p.user_id=m.user_id and p.submission_revision=m.submission_revision
                         where m.group_id=:g""", g=gid) == 3
    roles = dict(rows(db, "select user_id, role_id from role_assignment_members where group_id=:g", g=gid))
    assert roles[c.id] == "r2" and sorted(roles.values()) in (["r1", "r2", "r3"],)


def test_refresh_reconnect_and_a_replayed_submit_never_draw_again(db, app, mk, ai):
    gid, (a, b, c) = team(mk, 3)
    assert rank(a, gid, ["r1", "r2", "r3"]).status_code == 201
    assert rank(b, gid, ["r1", "r2", "r3"]).status_code == 201
    act = uuid.uuid4().hex
    assert rank(c, gid, ["r1", "r2", "r3"], action=act).status_code == 201
    before = col(db, "select user_id || ':' || role_id || ':' || coalesce(assigned_rank::text, '-') from role_assignment_members order by user_id")
    assert rank(c, gid, ["r1", "r2", "r3"], action=act).status_code == 200  # the same action is a replay
    assert rank(c, gid, ["r1", "r2", "r3"]).status_code == 409  # a new action is refused
    for u in (a, b, c):
        for _ in range(2):
            screen(reconnect(u), gid)
    with TestClient(app):  # a restart: startup recovery must not assign again
        pass
    after = col(db, "select user_id || ':' || role_id || ':' || coalesce(assigned_rank::text, '-') from role_assignment_members order by user_id")
    assert before == after and scalar(db, "select count(*) from role_assignments") == 1


def test_concurrent_last_submissions_make_one_assignment(db, app, mk, ai):
    gid, (a, b, c, d) = team(mk, 4)
    assert rank(a, gid, ["r1", "r2", "r3"]).status_code == 201
    assert rank(b, gid, ["r2", "r1", "r3"]).status_code == 201
    rev = screen(c, gid)["candidates"]["candidate_revision"]
    out = []

    def go(u, r):
        try:
            out.append(app.state.ranking.submit_preferences(u.id, gid, uuid.uuid4().hex, rev, r))
        except Exception as exc:  # noqa: BLE001
            out.append(type(exc).__name__)

    ts = [threading.Thread(target=go, args=(c, ["r3", "r1", "r2"])), threading.Thread(target=go, args=(d, ["r4", "r1", "r2"]))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(o[0] for o in out) == [True, True]
    assert [o[1] for o in out].count(1) == 1 and [o[1] for o in out].count(None) == 1  # exactly one call made revision 1
    assert scalar(db, "select count(*) from role_assignments") == 1
    assert scalar(db, "select count(*) from role_assignment_members") == 4


def test_each_member_sees_only_their_own_role_and_rank(db, mk, ai):
    gid, (a, b, c) = team(mk, 3)
    for u, r in ((a, ["r1", "r2", "r3"]), (b, ["r1", "r3", "r2"]), (c, ["r4", "r1", "r2"])):
        assert rank(u, gid, r).status_code == 201
    names = dict(CANDIDATES and [(f"r{i}", n) for i, (n, _) in enumerate(CANDIDATES, 1)])
    roles = {r.user_id: r for r in rows(db, "select user_id, role_id, assigned_rank from role_assignment_members")}
    for u in (a, b, c):
        s = screen(u, gid)
        mine = roles[u.id]
        assert s["phase"] == "ROLE_TODOS"
        assert s["role"]["role_name"] == names[mine.role_id]
        assert s["role"]["rank_text"] == (f"{mine.assigned_rank}순위로 배정" if mine.assigned_rank else "남은 역할로 배정")
        assert s["role"]["announcement"][1] == f"{u.name}님은 이번 프로젝트에서 {names[mine.role_id]} 역할을 맡게 됐어요({s['role']['rank_text']})."
        blob = json.dumps(s["role"], ensure_ascii=False)
        others = [p for p in (a, b, c) if p.id != u.id]
        forbidden_in(blob, others)
        mine_only = json.dumps({k: v for k, v in s["role"].items() if k != "role_description"}, ensure_ascii=False)
        for o in others:  # another member's role name never appears in my role part (my description is free text)
            assert names[roles[o.id].role_id] not in mine_only
        assert "draw" not in blob and "contenders" not in blob and "submitted_ranks" not in blob
        assert s["submitted_ranks"] is None  # the ranking itself is not repeated after the assignment
    # the assignment announcement is server text: no AI call made it
    assert not ai.kinds("RoleTurnOutput")


def test_no_api_lets_a_member_change_a_role_and_the_db_keeps_the_revision_immutable(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    for path in ("roles/assign", "assignment", "role", "roles/reassign", "role-interview/role"):
        assert post(a, f"/api/groups/{gid}/{path}", {"action_id": uuid.uuid4().hex, "role_id": "r5"}).status_code in (404, 405)
    with pytest.raises(Exception):
        with db.begin() as cx:
            cx.execute(__import__("sqlalchemy").text(
                "insert into role_assignments (group_id, revision, source_cohort_revision, source_candidate_revision, operation, state, draw_audit)"
                " values (:g, 2, 1, 1, 'INITIAL', 'CURRENT', '[]')"), {"g": gid})


# ---- coverage ---------------------------------------------------------------------------------------


def test_coverage_is_made_once_per_assignment_revision_from_the_brief_and_nameless_slots(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    assert len(ai.kinds("CoverageOutput")) == 1
    row = one(db, "select state, parts, attempt from team_coverages where group_id=:g and source_assignment_revision=1", g=gid)
    assert row.state == "SUCCEEDED" and row.attempt == 1
    slots = col(db, "select slot_id from role_assignment_members where group_id=:g order by slot_id", g=gid)
    assert {p["slot"] for p in row.parts} == set(slots)
    payload = ai.payloads("CoverageOutput")[0]
    assert set(payload) == {"project", "slots"}
    assert [d["id"] for d in payload["project"]["deliverables"]] == ["d1", "d2"]
    forbidden_in(json.dumps(payload, ensure_ascii=False), people, extra=("rank", "user_id", "순위"))
    for u in people:  # the coverage is internal: the browser sees only its state
        blob = json.dumps(screen(u, gid), ensure_ascii=False)
        assert '"parts"' not in blob and "담당 s1" not in blob
        assert screen(u, gid)["role"]["coverage"]["state"] == "SUCCEEDED"
    screen(people[0], gid)
    assert len(ai.kinds("CoverageOutput")) == 1  # never regenerated on refresh


@pytest.mark.parametrize("parts,code", [
    ([("발표 자료", "d1", "s1"), ("보고서 초안", "d1", "s2")], "COVERAGE_MISSING"),  # d2 is not covered
    ([("발표 자료", "d1", "s1"), ("보고서", "d2", "s1")], "COVERAGE_MISSING"),  # s2 owns nothing
    ([("발표 자료", "d1", "s1"), ("발표  자료", "d2", "s2")], "COVERAGE_DUPLICATE"),
    ([("발표 자료", "d1", "s1"), ("보고서", "d9", "s2")], "COVERAGE_UNKNOWN_ID"),
    ([("발표 자료", "d1", "s1"), ("보고서", "d2", "s7")], "COVERAGE_UNKNOWN_ID"),
    ([("x", "d1", "s1"), ("보고서", "d2", "s2")], "COVERAGE_INVALID"),
])
def test_coverage_check_rejects_missing_duplicate_and_unknown_ids(parts, code):
    with pytest.raises(CoverageError) as exc:
        check_coverage([PartItem(name=n, source=s, slot=o) for n, s, o in parts], ["d1", "d2"], ["s1", "s2"])
    assert exc.value.code == code
    ok = check_coverage([PartItem(name="발표 자료", source="d1", slot="s1"), PartItem(name="보고서", source="d2", slot="s2")],
                        ["d1", "d2"], ["s1", "s2"])
    assert [p["id"] for p in ok] == ["p1", "p2"]


def test_coverage_failure_is_an_error_with_an_explicit_retry_and_no_fallback_list(db, mk, ai):
    ai.scripts["CoverageOutput"] = [{"parts": [{"name": "발표 자료", "source": "d1", "slot": "s1"}]}]  # misses d2 and slots
    gid, people = assigned_team(mk, 3)
    a = people[0]
    cov = screen(a, gid)["role"]["coverage"]
    assert cov["state"] == "FAILED" and cov["can_retry"] and cov["error_message"]
    assert scalar(db, "select parts from team_coverages where group_id=:g", g=gid) is None
    assert len(ai.kinds("CoverageOutput")) == 1  # no automatic retry
    act = uuid.uuid4().hex
    body = {"action_id": act, "assignment_revision": 1, "expected_attempt": 1}
    r = post(people[1], f"/api/groups/{gid}/coverage/retry", body)
    assert r.status_code == 202 and screen(people[1], gid)["role"]["coverage"]["state"] == "SUCCEEDED"
    assert post(people[1], f"/api/groups/{gid}/coverage/retry", body).status_code == 202  # replay: no second call
    assert post(people[2], f"/api/groups/{gid}/coverage/retry", {**body, "action_id": uuid.uuid4().hex}).status_code == 409
    assert len(ai.kinds("CoverageOutput")) == 2
    assert scalar(db, "select attempt from team_coverages where group_id=:g", g=gid) == 2


def test_coverage_provider_error_and_restart_are_retryable_failures(db, app, mk, ai):
    ai.scripts["CoverageOutput"] = [AIError("TIMEOUT", "slow")]
    gid, people = assigned_team(mk, 3)
    assert scalar(db, "select error_code from team_coverages where group_id=:g", g=gid) == "TIMEOUT"
    with db.begin() as cx:
        cx.execute(__import__("sqlalchemy").text("update team_coverages set state='RUNNING', error_code=null, error_message=null"))
    with TestClient(app):
        pass
    assert scalar(db, "select error_code from team_coverages where group_id=:g", g=gid) == "SERVER_RESTARTED"
    assert screen(people[0], gid)["role"]["coverage"]["can_retry"]


# ---- interview: AI-led, server-checked --------------------------------------------------------------


def test_the_first_question_is_made_by_the_ai_for_the_assigned_role(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    v = iv(a, gid)
    assert v["state"] == "ASKING" and not v["started"] and v["question"] is None
    r = start(a, gid)
    assert r.status_code == 202
    v = iv(a, gid)
    assert v["started"] and not v["busy"] and v["questions_asked"] == 1
    payload = ai.payloads("RoleTurnOutput")[0]
    role = screen(a, gid)["role"]["role_name"]
    assert payload["my_role"]["role"] == role and payload["answer_kind"] == "none" and payload["answer"] is None
    assert v["messages"][-1] == {"from": "ai", "text": f"{role} 일을 해 본 적이 있나요?"}
    labels = [o["label"] for o in v["question"]["options"]]
    assert labels == ["처음이에요", "조금 해봤어요", "잘 모르겠어요"]
    assert "value" not in json.dumps(v["question"])  # button values stay on the server
    assert start(a, gid).status_code == 200 and len(ai.kinds("RoleTurnOutput")) == 1  # no second first question


def test_full_interview_text_button_unknown_constraints_confirm_and_snapshot(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    role = screen(a, gid)["role"]["role_name"]
    ai.scripts["RoleTurnOutput"] = [
        turn("experience", role=role),
        turn("weekly_available_minutes", value="피그마로 시안 두 번 만들어 봤어요", quote="피그마로 시안 두 번 만들어 봤어요", role=role),
        turn("tools", role=role),
        turn("constraints", role=role),
        turn("preferred_times", role=role),
        turn(None),
    ]
    start(a, gid)
    assert answer(a, gid, message="피그마로 시안 두 번 만들어 봤어요").status_code == 202
    assert answer(a, gid, option="주 3시간").status_code == 202
    assert answer(a, gid, option="unknown").status_code == 202  # tools: 모름
    assert answer(a, gid, message=SECRET_CONSTRAINT).status_code == 202
    r = answer(a, gid, option="평일 저녁")  # the 5th question: a button needs no AI call
    assert r.status_code == 201
    v = r.json()["role"]["interview"]
    assert v["state"] == "CONFIRMING" and v["questions_asked"] == 5
    assert len(ai.kinds("RoleTurnOutput")) == 5
    summary = v["messages"][-1]["text"]
    assert "역할 관련 경험: 피그마로 시안 두 번 만들어 봤어요" in summary and "주간 가능 시간: 주 3시간" in summary
    assert "쓸 수 있는 도구: 모름" in summary and "선호 시간: 평일 저녁" in summary
    assert "매주 화요일" in summary and "나만 볼 수 있게" in summary
    assert SECRET_CONSTRAINT not in summary
    assert [o["label"] for o in v["question"]["options"]][0] == "맞아요"
    # the raw constraint text never reached the provider, nor did any name, id or ranking
    for m in ai.kinds("RoleTurnOutput"):
        blob = json.dumps(m, ensure_ascii=False)
        assert "비밀메모" not in blob and "시험 기간이라" not in blob
        forbidden_in(blob, people, extra=("rank1", "1순위", "submission", "user_id"))
    withheld = ai.payloads("RoleTurnOutput")[4]
    assert withheld["answer_kind"] == "withheld" and withheld["answer"] is None
    assert withheld["known"]["constraints"] == "답함(내용은 비공개)"
    r = answer(a, gid, option="맞아요")
    assert r.status_code == 201 and r.json()["role"]["interview"]["state"] == "DONE"
    snap = one(db, "select * from role_interview_snapshots where group_id=:g and user_id=:u", g=gid, u=a.id)
    assert snap.source_assignment_revision == 1
    assert snap.provider_input == {"current_level": "피그마로 시안 두 번 만들어 봤어요", "weekly_available_minutes": 180,
                                   "tools": "UNKNOWN", "preferred_times": ["평일 저녁"]}
    assert snap.private_constraints == {"raw": [SECRET_CONSTRAINT]}
    assert snap.deadline_limits["blocked_weekdays"] == [1]
    assert not ai.kinds("RoleTurnOutput")[5:]  # confirming makes no AI call
    assert answer(a, gid, message="한 번 더").status_code == 409  # a finished interview takes no more answers


def test_at_most_five_questions_even_if_the_ai_keeps_asking(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), turn("weekly_available_minutes"), turn("tools"),
                                    turn("preferred_times"), turn("constraints"), turn("experience")]
    start(a, gid)
    for _ in range(4):
        assert answer(a, gid, option="o0").status_code == 202
    v = iv(a, gid)
    assert v["questions_asked"] == 5 and v["state"] == "ASKING"
    r = answer(a, gid, option="o1")  # 5th answer (a button): no AI call, straight to the confirm step
    assert r.status_code == 201 and r.json()["role"]["interview"]["state"] == "CONFIRMING"
    assert len(ai.kinds("RoleTurnOutput")) == 5
    assert scalar(db, "select questions_asked from role_interviews where user_id=:u", u=a.id) == 5


def test_both_required_unknown_still_reaches_the_todo_input_without_invented_values(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), turn("weekly_available_minutes"), turn(None)]
    start(a, gid)
    answer(a, gid, option="unknown")
    assert answer(a, gid, option="unknown").status_code == 202
    assert iv(a, gid)["state"] == "CONFIRMING"
    answer(a, gid, option="맞아요")
    snap = one(db, "select provider_input from role_interview_snapshots where user_id=:u", u=a.id)
    assert snap.provider_input == {"current_level": "UNKNOWN", "weekly_available_minutes": "UNKNOWN", "tools": "UNKNOWN",
                                   "preferred_times": "UNKNOWN"}


def test_meaningless_input_stores_counts_and_calls_nothing(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    start(a, gid)
    calls = len(ai.calls)
    before = (scalar(db, "select count(*) from role_interview_turns"), scalar(db, "select count(*) from role_member_states"))
    for junk in ("ㅇㅇㅇ", "...", "ㅋ", "?!", "a"):
        r = answer(a, gid, message=junk)
        assert r.status_code == 422 and r.json()["error"]["code"] == "MEANINGLESS"
    assert len(ai.calls) == calls
    assert (scalar(db, "select count(*) from role_interview_turns"), scalar(db, "select count(*) from role_member_states")) == before
    assert iv(a, gid)["questions_asked"] == 1


def test_not_understood_keeps_the_same_question_without_counting_it(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), turn("weekly_available_minutes", understood=False, reply="경험을 여쭤본 거예요.")]
    start(a, gid)
    qid = iv(a, gid)["question"]["question_id"]
    assert answer(a, gid, message="오늘 날씨 좋네요").status_code == 202
    v = iv(a, gid)
    assert v["question"]["question_id"] == qid and v["questions_asked"] == 1
    assert v["messages"][-1]["text"] == "경험을 여쭤본 거예요."
    assert "experience" not in json.dumps(one(db, "select state from role_member_states order by revision desc limit 1").state["fields"])


def test_ungrounded_ai_values_are_never_stored(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [
        turn("weekly_available_minutes"),
        # the AI invents 600 minutes and a tool the member never said
        turn("experience", value=600, quote="많이", extras=[{"field": "tools", "value": ["포토샵"], "quote": "포토샵"}]),
    ]
    start(a, gid)
    answer(a, gid, message="시간은 많이 낼 수 있어요")
    state = one(db, "select state from role_member_states order by revision desc limit 1").state
    assert "weekly_available_minutes" not in state["fields"] and "tools" not in state["fields"]
    assert iv(a, gid)["question"]["question_id"] == "q0"  # not understood: the same question stays


@pytest.mark.parametrize("bad", [
    turn("experience", text="experience 항목을 알려 주세요"),  # internal wording
    turn("tools", options=[{"label": "피그마", "value": ["피그마"]}]),  # fewer than 2 buttons
    turn("experience"),  # repeats an already asked question
    turn(None),  # stops before the second required item was asked
    {"understood": True, "value": None, "quote": "", "extras": [], "reply": "", "next": {"field": "role", "text": "역할을 바꿀까요?", "options": []}},
])
def test_an_unusable_ai_question_fails_the_step_with_a_retry_and_no_fixed_question(db, mk, ai, bad):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), bad, turn("weekly_available_minutes")]
    start(a, gid)
    answer(a, gid, option="o0")
    v = iv(a, gid)
    assert v["failed"] == {"seq": 1, "attempt": 1} and v["question"] is None and not v["input_open"]
    assert v["messages"][-1]["err"]
    assert scalar(db, "select count(*) from role_member_states where user_id=:u", u=a.id) == 1  # nothing applied
    assert answer(a, gid, message="다른 대답이에요").status_code == 409  # retry first
    r = post(a, f"/api/groups/{gid}/role-interview/retry", {"action_id": uuid.uuid4().hex, "seq": 1, "expected_attempt": 1})
    assert r.status_code == 202
    v = iv(a, gid)
    assert v["failed"] is None and v["question"]["question_id"] == "q1"
    state = one(db, "select state from role_member_states order by revision desc limit 1").state
    assert state["fields"]["experience"]["status"] == "KNOWN"  # the member's answer was kept for the retry


def test_ai_provider_failure_keeps_the_answer_and_offers_a_retry(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), AIError("TIMEOUT", "slow")]
    start(a, gid)
    answer(a, gid, message="조금 해봤어요 발표 자료 만들기")
    v = iv(a, gid)
    assert v["failed"] and "늦거나" in v["messages"][-1]["text"]
    assert v["messages"][-2] == {"from": "me", "text": "조금 해봤어요 발표 자료 만들기"}
    assert len(ai.kinds("RoleTurnOutput")) == 2  # no automatic retry


def test_confirm_step_edits_are_code_only_and_invalid_edits_store_nothing(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    ai.scripts["RoleTurnOutput"] = [turn("experience"), turn("weekly_available_minutes"), turn(None)]
    start(a, gid)
    answer(a, gid, option="o0")
    answer(a, gid, option="o0")
    calls = len(ai.calls)
    assert iv(a, gid)["state"] == "CONFIRMING"
    assert answer(a, gid, message="그냥 글").status_code == 422  # the confirm step takes buttons
    r = answer(a, gid, option="시간 고치기")
    assert r.status_code == 201 and r.json()["role"]["interview"]["editing"]
    n = scalar(db, "select count(*) from role_member_states")
    r = answer(a, gid, message="많이요")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ANSWER"
    assert scalar(db, "select count(*) from role_member_states") == n
    r = answer(a, gid, message="주 5시간 30분")
    assert r.status_code == 201
    v = r.json()["role"]["interview"]
    assert v["state"] == "CONFIRMING" and "주간 가능 시간: 주 5시간 30분" in v["messages"][-1]["text"]
    answer(a, gid, option="경험 고치기")
    answer(a, gid, option="unknown")
    answer(a, gid, option="맞아요")
    snap = one(db, "select provider_input from role_interview_snapshots where user_id=:u", u=a.id)
    assert snap.provider_input["weekly_available_minutes"] == 330 and snap.provider_input["current_level"] == "UNKNOWN"
    assert len(ai.calls) == calls  # no AI call in the confirm step


def test_stale_question_forged_option_and_replayed_answer(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    start(a, gid)
    q = iv(a, gid)["question"]
    bad = post(a, f"/api/groups/{gid}/role-interview/answer", {"action_id": uuid.uuid4().hex, "question_id": q["question_id"], "option_id": "o9"})
    assert bad.status_code == 422
    act = uuid.uuid4().hex
    assert answer(a, gid, option="o0", action=act).status_code == 202
    calls = len(ai.calls)
    assert post(a, f"/api/groups/{gid}/role-interview/answer", {"action_id": act, "question_id": q["question_id"], "option_id": "o0"}).status_code == 200
    stale = post(a, f"/api/groups/{gid}/role-interview/answer", {"action_id": uuid.uuid4().hex, "question_id": q["question_id"], "option_id": "o0"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_QUESTION"
    assert len(ai.calls) == calls
    assert post(a, f"/api/groups/{gid}/role-interview/answer", {"action_id": uuid.uuid4().hex, "message": "x", "question_id": "q", "option_id": "o"}).status_code == 422
    assert post(a, f"/api/groups/{gid}/role-interview/answer", {"action_id": uuid.uuid4().hex, "message": "안녕", "extra": 1}).status_code == 400


def test_one_members_unfinished_interview_never_blocks_another(db, mk, ai):
    gid, (a, b, c) = assigned_team(mk, 3)
    ai.scripts["RoleTurnOutput"] = [turn("experience"), AIError("UPSTREAM_ERROR", "x")]
    start(a, gid)
    answer(a, gid, option="o0")
    assert iv(a, gid)["failed"]  # a is stuck on a failed step and has not finished
    start(b, gid)
    answer(b, gid, option="o0")
    answer(b, gid, option="o0")
    answer(b, gid, option="맞아요")
    assert iv(b, gid)["state"] == "DONE" and iv(a, gid)["state"] == "ASKING" and not iv(c, gid)["started"]
    assert scalar(db, "select count(*) from role_interview_snapshots") == 1


def test_the_interview_never_changes_the_role(db, mk, ai):
    gid, (a, b, c) = assigned_team(mk, 3)
    before = col(db, "select user_id || role_id || coalesce(assigned_rank::text,'-') from role_assignment_members order by user_id")
    ai.scripts["RoleTurnOutput"] = [turn("experience"), turn("weekly_available_minutes", understood=False),
                                    turn("weekly_available_minutes"), turn(None)]
    start(a, gid)
    answer(a, gid, message="저는 개발 역할로 바꾸고 싶어요")  # not an answer: the same question stays
    answer(a, gid, option="o0")
    answer(a, gid, option="o0")
    assert answer(a, gid, option="맞아요").status_code == 201 and iv(a, gid)["state"] == "DONE"
    after = col(db, "select user_id || role_id || coalesce(assigned_rank::text,'-') from role_assignment_members order by user_id")
    assert before == after and scalar(db, "select count(*) from role_assignments") == 1


# ---- privacy and security -------------------------------------------------------------------------


def test_a_members_interview_is_never_visible_to_anyone_else_nor_in_logs(db, mk, ai, caplog):
    caplog.set_level(logging.DEBUG)
    gid, (a, b, c) = assigned_team(mk, 3)
    start(a, gid)
    answer(a, gid, message="포트폴리오용으로 피그마 시안 만들어 봤어요")
    secret = "포트폴리오용으로"
    for u in (b, c):
        blob = json.dumps(screen(u, gid), ensure_ascii=False)
        assert secret not in blob
    assert secret in json.dumps(screen(a, gid), ensure_ascii=False)
    for rec in caplog.records:
        line = rec.getMessage()
        assert secret not in line and "피그마" not in line
        for n, _ in CANDIDATES:
            assert n not in line  # no role name (and so no who-got-what) in any log line
    gid2, (x, *_rest) = assigned_team(mk, 2, leader="other")
    for path, body in (("role-interview/start", {}), ("role-interview/answer", {"message": "안녕하세요"}),
                       ("role-interview/retry", {"seq": 1, "expected_attempt": 1}),
                       ("coverage/retry", {"assignment_revision": 1, "expected_attempt": 1})):
        r = post(x, f"/api/groups/{gid}/{path}", {"action_id": uuid.uuid4().hex, **body})
        assert r.status_code == 404, (path, r.text)
    assert x.c.get(f"/api/groups/{gid}/roles").status_code == 404


def test_login_and_csrf_are_required_for_every_new_endpoint(db, app, mk, ai):
    gid, (a, *_r) = assigned_team(mk, 3)
    anon = TestClient(app, follow_redirects=False)
    for path in ("role-interview/start", "role-interview/answer", "role-interview/retry", "coverage/retry"):
        assert anon.post(f"/api/groups/{gid}/{path}", json={"action_id": uuid.uuid4().hex}).status_code in (401, 403)
        assert post(a, f"/api/groups/{gid}/{path}", {"action_id": uuid.uuid4().hex}, token=None).status_code == 403


def test_user_text_is_data_and_the_browser_code_never_touches_markup_or_the_provider(db, mk, ai):
    gid, (a, *_r) = assigned_team(mk, 3)
    start(a, gid)
    xss = "<img src=x onerror=alert(1)> 해봤어요"
    answer(a, gid, message=xss)
    r = a.c.get(f"/api/groups/{gid}/roles")
    assert r.headers["content-type"].startswith("application/json") and xss in json.dumps(r.json(), ensure_ascii=False)
    assert xss not in a.c.get(f"/groups/{gid}/chat").text
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "insertAdjacentHTML", "document.write", "localStorage", "sessionStorage", "document.cookie", "timely", "api_key"):
        assert bad.lower() not in js.lower(), bad


def test_assignment_needs_the_cohort_and_a_late_joiner_cannot_enter(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    late = mk("latecomer")
    assert late.c.get(f"/api/groups/{gid}/roles").status_code == 404
    assert post(late, f"/api/groups/{gid}/role-interview/start", {"action_id": uuid.uuid4().hex}).status_code == 404


# ---- recovery -------------------------------------------------------------------------------------


def test_refresh_and_restart_recover_role_todos_and_the_interview(db, app, mk, ai):
    gid, (a, *_r) = assigned_team(mk, 3)
    start(a, gid)
    answer(a, gid, message="발표 자료 만들어 본 적 있어요")
    seen = iv(a, gid)
    fresh = reconnect(a)
    assert iv(fresh, gid) == seen and screen(fresh, gid)["phase"] == "ROLE_TODOS"
    # a step left RUNNING by a dead process becomes a retryable failure on start-up
    ai.scripts["RoleTurnOutput"] = []
    with db.begin() as cx:
        cx.execute(__import__("sqlalchemy").text(
            "insert into role_interview_turns (group_id, user_id, source_assignment_revision, seq, kind, action_id, user_message, state)"
            " values (:g, :u, 1, 2, 'ANSWER', :act, '주 4시간', 'RUNNING')"), {"g": gid, "u": a.id, "act": uuid.uuid4().hex})
    with TestClient(app):
        pass
    v = iv(reconnect(a), gid)
    assert v["failed"] == {"seq": 2, "attempt": 1} and "다시 시작" in v["messages"][-1]["text"]
    r = post(a, f"/api/groups/{gid}/role-interview/retry", {"action_id": uuid.uuid4().hex, "seq": 2, "expected_attempt": 1})
    assert r.status_code == 202 and iv(a, gid)["failed"] is None


# ---- constraint conversion (code only) ------------------------------------------------------------


def test_constraints_convert_dates_weekdays_and_preferred_times_by_code():
    start_d, end_d = date(2026, 11, 20), date(2026, 12, 20)
    dates, weekdays, preferred = ai_mod.parse_limits(
        ["11월 25일~27일은 시험이라 안돼요, 화요일은 알바가 있어요", "주말 오후가 좋아요", "12/31 여행"], start_d, end_d
    )
    assert dates == ["2026-11-25", "2026-11-26", "2026-11-27"]  # 12/31 is outside the project period
    assert weekdays == [1]
    assert preferred == ["주말", "오후"]
    # "다음 주 화요일" is one date (from today), not every Tuesday; without today it is not converted at all
    d2, w2, _ = ai_mod.parse_limits(["다음 주 화요일은 시험이라 안돼요"], start_d, end_d, today=date(2026, 11, 19))
    assert d2 == ["2026-11-24"] and w2 == []
    assert ai_mod.parse_limits(["다음 주 화요일은 시험이라 안돼요"], start_d, end_d) == ([], [], [])
    assert ai_mod.parse_minutes("주 5시간") == 300 and ai_mod.parse_minutes("90분") == 90
    assert ai_mod.parse_minutes("3~5시간") is None and ai_mod.parse_minutes("많이") is None


def test_hour_buttons_must_show_the_one_value_they_store():
    def kept(pairs):
        opts = [ai_mod.NextOption(label=lab, value=v) for lab, v in pairs]  # at most 4 buttons are read
        return [o["label"] for o in ai_mod.clean_options("weekly_available_minutes", opts)]

    assert kept((("1~2시간", 90), ("주 3시간", 180), ("주 5시간 이상", 300), ("주 4시간", 300))) == ["주 3시간", "주 5시간 이상"]
    assert kept((("90분", 90), ("주 2시간", 90))) == ["90분"]


# ---- migration --------------------------------------------------------------------------------------


def test_migration_0008_down_and_up_keeps_existing_interview_and_ranking_data(db, grp_db, mk, ai):
    gid, people = assigned_team(mk, 3)
    sa = __import__("sqlalchemy")
    with db.begin() as cx:  # an old (deprecated) stage 3 interview row must survive
        cx.execute(sa.text("insert into interview_turns (group_id, user_id, seq, action_id, user_message, state)"
                           " values (:g, :u, 1, 'old-action-1', '옛 대화', 'FAILED')"), {"g": gid, "u": people[0].id})
    cfg = Config(str(ROOT / "alembic.ini"))
    try:
        command.downgrade(cfg, "0007")
        for t in ("role_assignments", "team_coverages", "role_interviews", "role_interview_turns", "role_interview_snapshots"):
            assert scalar(db, f"select to_regclass('public.{t}')") is None
        assert col(db, "select status from member_progress where group_id=:g", g=gid) == ["WAITING_ASSIGNMENT"] * 3
        assert scalar(db, "select count(*) from preference_submissions where group_id=:g", g=gid) == 3
        assert scalar(db, "select count(*) from interview_turns where group_id=:g", g=gid) == 1
        command.upgrade(cfg, "head")
        assert scalar(db, "select to_regclass('public.role_assignments')") is not None
        assert col(db, "select status from member_progress where group_id=:g", g=gid) == ["WAITING_ASSIGNMENT"] * 3
        assert scalar(db, "select count(*) from interview_turns where group_id=:g", g=gid) == 1
        assert scalar(db, "select count(*) from preference_submissions where group_id=:g", g=gid) == 3
    finally:
        command.upgrade(cfg, "head")


def test_db_constraints_hold_role_todos_and_the_question_budget(db, mk, ai):
    gid, (a, *_r) = assigned_team(mk, 3)
    sa = __import__("sqlalchemy")
    for sql in (
        "update member_progress set assignment_revision = null where user_id = :u",
        "update member_progress set submission_revision = null where user_id = :u",
        "update role_interviews set questions_asked = 6 where user_id = :u",
        "update role_interviews set state = 'CHATTING' where user_id = :u",
    ):
        with pytest.raises(Exception):
            with db.begin() as cx:
                cx.execute(sa.text(sql), {"u": a.id})
