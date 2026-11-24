"""Stage 4 session B deterministic contract: role-fit todo draft, direct and natural-language edits, hidden criteria,
`투두 확정하기` revisions and the group read API.

The provider is a FIXTURE client (never reported as LIVE). It records every call so the tests can check that no
provider input carries a name, a user id, a ranking, a raw constraint or the conversation. Its outputs go through the
same pydantic schema check as the real client (a schema failure is AIError SCHEMA_INVALID).
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import date, timedelta

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from moim.roles import todo_ai as tai
from test_groups import col, db, fake, grp_db, one, post, scalar, today  # noqa: F401
from test_role_assignment import (  # noqa: F401
    ROOT,
    SECRET_CONSTRAINT,
    FakeAI,
    answer,
    app,
    assigned_team,
    forbidden_in,
    mk,
    reconnect,
    screen,
    seed,
    start,
    turn,
)
from wemeet_ai.m0.client import AIError

SRC = ROOT / "src" / "moim"
ACTIONS = ["구조 잡기", "초안 만들기", "피드백 반영해 고치기", "제출본 올리기"]
PHASES = ["RESEARCH", "DRAFT", "REVIEW", "SUBMIT"]


def default_draft(payload, minutes=40):
    """A valid draft: every one of my parts, phases in order, dates spread over the period."""
    first = date.fromisoformat(payload["period"]["first_day"])
    end = date.fromisoformat(payload["project"]["end_date"])
    parts = payload["my_parts"]
    per = max(2, min(4, 8 // len(parts)))
    plan = [(p, k) for p in parts for k in range(per)][:8]
    span = (end - first).days
    todos = []
    for i, (p, k) in enumerate(sorted(plan, key=lambda x: (x[1], x[0]["id"]))):
        due = first + timedelta(days=round((i + 1) * span / (len(plan) + 1)))
        todos.append({
            "title": f"{p['name']} {ACTIONS[k]}", "due_date": due.isoformat(), "part": p["id"], "phase": PHASES[k],
            "milestone": None, "deliverable": f"{p['name']}의 {ACTIONS[k]} 결과 파일",
            "completion_criteria": [f"{p['name']} {ACTIONS[k]} 결과를 팀 공유 폴더에 올렸다"],
            "evidence_types": ["DOCUMENT"], "estimated_minutes": minutes,
        })
    return {"todos": todos}


def default_criteria(payload):
    parts = payload["my_parts"]
    by = {t["ref"]: t for t in payload["todos"]}
    return {"items": [{"ref": r, "part": by[r]["part"] or parts[0]["id"], "deliverable": f"{by[r]['title']} 결과물",
                       "completion_criteria": [f"{by[r]['title']} 결과를 팀원이 확인할 수 있게 올렸다"],
                       "evidence_types": ["LINK"], "estimated_minutes": 60} for r in payload["targets"]]}


class TodoAI(FakeAI):
    """FakeAI plus the three todo schemas. Never LIVE."""

    def __init__(self):
        super().__init__()
        self.scripts.update({"DraftOutput": [], "ReviseOutput": [], "CriteriaOutput": []})

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        kind = output_schema.__name__
        if kind not in ("DraftOutput", "ReviseOutput", "CriteriaOutput"):
            return super().complete_json(messages, output_schema, options, stats=stats)
        with self.lock:
            self.calls.append((kind, messages))
            item = self.scripts[kind].pop(0) if self.scripts[kind] else None
        if isinstance(item, Exception):
            raise item
        payload = json.loads(messages[-1]["content"])
        if callable(item):
            data = item(payload)
        elif item is not None:
            data = item
        elif kind == "DraftOutput":
            data = default_draft(payload)
        elif kind == "CriteriaOutput":
            data = default_criteria(payload)
        else:
            data = {"understood": False, "changes": [], "reply": ""}
        try:
            parsed = output_schema.model_validate(data)
        except ValidationError:
            raise AIError("SCHEMA_INVALID", "fixture output does not match the schema") from None
        from types import SimpleNamespace
        from wemeet_ai.shared.contracts import Usage
        return SimpleNamespace(parsed_output=parsed, model="fixture-model", latency_ms=9, provider_request_id="fixture-req",
                               usage=Usage(prompt_tokens=30, completion_tokens=20, total_tokens=50))


@pytest.fixture()
def ai():
    return TodoAI()


# ---- helpers ------------------------------------------------------------------------------------


def finish_interview(u, gid):
    start(u, gid)
    assert answer(u, gid, message="피그마로 시안 두 번 만들어 봤어요").status_code == 202
    assert answer(u, gid, option="주 3시간").status_code in (201, 202)
    r = answer(u, gid, option="맞아요")
    assert r.status_code == 201 and r.json()["role"]["interview"]["state"] == "DONE", r.text


def td(u, gid):
    return screen(u, gid)["role"]["todos"]


def tstart(u, gid, action=None):
    return post(u, f"/api/groups/{gid}/todos/start", {"action_id": action or uuid.uuid4().hex})


def edit(u, gid, op, key=None, title=None, due=None, rev=None, action=None):
    body = {"action_id": action or uuid.uuid4().hex, "draft_revision": rev or td(u, gid)["draft_revision"], "op": op}
    if key is not None:
        body["key"] = key
    if title is not None:
        body["title"] = title
    if due is not None:
        body["due_date"] = due
    return post(u, f"/api/groups/{gid}/todos/edit", body)


def revise(u, gid, message, action=None):
    return post(u, f"/api/groups/{gid}/todos/revise", {"action_id": action or uuid.uuid4().hex, "message": message})


def confirm(u, gid, action=None, rev=None):
    return post(u, f"/api/groups/{gid}/todos/confirm",
                {"action_id": action or uuid.uuid4().hex, "draft_revision": rev or td(u, gid)["draft_revision"]})


def unlock(u, gid):
    return post(u, f"/api/groups/{gid}/todos/unlock", {"action_id": uuid.uuid4().hex})


def retry(u, gid):
    f = td(u, gid)["failed"]
    return post(u, f"/api/groups/{gid}/todos/retry", {"action_id": uuid.uuid4().hex, "seq": f["seq"], "expected_attempt": f["attempt"]})


def ready(mk, n=3):
    """A team whose first member finished the interview and has a draft."""
    gid, people = assigned_team(mk, n)
    a = people[0]
    finish_interview(a, gid)
    assert tstart(a, gid).status_code == 202
    assert td(a, gid)["state"] == "READY"
    return gid, people


def items(eng, gid, uid, deleted=False):
    from sqlalchemy import text

    with eng.connect() as c:
        return c.execute(text(
            "select * from todo_items where group_id=:g and user_id=:u" + ("" if deleted else " and deleted_at is null")
            + " order by position, item_no"), {"g": gid, "u": uid}).all()


def group_todos(u, gid):
    r = u.c.get(f"/api/groups/{gid}/todos")
    assert r.status_code == 200, r.text
    return r.json()


def walk_keys(node, keys):
    if isinstance(node, dict):
        keys.update(node)
        for v in node.values():
            walk_keys(v, keys)
    elif isinstance(node, list):
        for v in node:
            walk_keys(v, keys)
    return keys


# ---- draft ----------------------------------------------------------------------------------------


def test_no_draft_before_the_interview_is_done_or_the_coverage_is_ready(db, mk, ai):
    gid, (a, b, c) = assigned_team(mk, 3)
    assert td(a, gid)["state"] == "NONE" and td(a, gid)["can_start"] is False
    assert tstart(a, gid).status_code == 409  # interview not done
    with db.begin() as cx:  # a failed coverage blocks the draft too
        cx.execute(__import__("sqlalchemy").text(
            "update team_coverages set state='FAILED', parts=null, error_code='X', error_message='m' where group_id=:g"), {"g": gid})
    finish_interview(a, gid)
    assert td(a, gid)["can_start"] is False
    r = tstart(a, gid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "COVERAGE_NOT_READY"
    assert scalar(db, "select count(*) from todo_drafts") == 0 and not ai.kinds("DraftOutput")


def test_a_valid_draft_is_stored_with_hidden_criteria_linked_to_the_assignment_and_snapshot(db, mk, ai):
    gid, people = ready(mk)
    a = people[0]
    v = td(a, gid)
    assert v["state"] == "READY" and v["draft_revision"] == 1 and v["mode"] == "EDITING" and v["input_open"]
    assert 4 <= len(v["items"]) <= 8
    for it in v["items"]:
        assert set(it) == {"key", "title", "due_date"}  # nothing hidden reaches the browser
    assert "투두 초안" in v["messages"][-1]["text"]
    rows = items(db, gid, a.id)
    assert [f"k{r.item_no}" for r in rows] == [it["key"] for it in v["items"]]
    for r in rows:
        uuid.UUID(r.task_id)
        assert r.source_assignment_revision == 1 and r.origin == "AI" and r.criteria_for_title == r.title
        assert r.deliverable and r.completion_criteria and r.estimated_minutes
        assert set(r.evidence_types) <= set(tai.EVIDENCE_VALUES)
    draft = one(db, "select * from todo_drafts where group_id=:g and user_id=:u", g=gid, u=a.id)
    snap = one(db, "select * from role_interview_snapshots where group_id=:g and user_id=:u", g=gid, u=a.id)
    assert draft.member_state_revision == snap.member_state_revision and draft.source_assignment_revision == 1
    parts = one(db, "select parts from team_coverages where group_id=:g", g=gid).parts
    mine = one(db, "select slot_id from role_assignment_members where group_id=:g and user_id=:u", g=gid, u=a.id).slot_id
    assert {r.part_id for r in rows} == {p["id"] for p in parts if p["slot"] == mine}  # my coverage, all of it
    assert len(ai.kinds("DraftOutput")) == 1
    step = one(db, "select * from todo_ai_steps where group_id=:g and user_id=:u", g=gid, u=a.id)
    assert step.kind == "DRAFT" and step.state == "SUCCEEDED" and step.request_id == "fixture-req" and step.total_tokens == 50
    # a second start (refresh, double click) never makes a second draft or call
    assert tstart(a, gid).status_code == 200 and len(ai.kinds("DraftOutput")) == 1


def test_the_draft_input_is_b3_only(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    role = screen(a, gid)["role"]["role_name"]
    ai.scripts["RoleTurnOutput"] = [
        turn("experience", role=role),
        turn("weekly_available_minutes", value="피그마로 시안 두 번 만들어 봤어요", quote="피그마로 시안 두 번 만들어 봤어요", role=role),
        turn("constraints", role=role),
        turn(None),
    ]
    start(a, gid)
    answer(a, gid, message="피그마로 시안 두 번 만들어 봤어요")
    answer(a, gid, option="unknown")  # weekly time: 모름
    answer(a, gid, message=SECRET_CONSTRAINT)
    assert answer(a, gid, option="맞아요").json()["role"]["interview"]["state"] == "DONE"
    assert tstart(a, gid).status_code == 202
    (payload,) = ai.payloads("DraftOutput")
    assert set(payload) == {"project", "my_role", "team", "my_parts", "member", "period", "workload"}
    assert set(payload["member"]) == {"current_level", "weekly_available_minutes", "tools", "preferred_times", "scope"}
    assert payload["member"]["weekly_available_minutes"] == "UNKNOWN"  # not filled in
    assert payload["workload"]["weekly_budget_minutes"] == tai.UNKNOWN_WEEKLY_MINUTES
    assert set(payload["my_role"]) == {"role", "description"}
    for slot in payload["team"]:
        assert set(slot) == {"slot", "role", "parts", "is_me"}
    blob = json.dumps(ai.kinds("DraftOutput"), ensure_ascii=False)
    assert "비밀메모" not in blob and "시험 기간이라" not in blob and "blocked" not in blob
    forbidden_in(blob, people, extra=("rank", "순위", "submission", "user_id", "피그마로 시안 두 번 만들어 봤어요?"))
    msgs = [m["text"] for m in screen(a, gid)["role"]["interview"]["messages"]]
    for m in msgs[3:]:  # no conversation text other than the confirmed level value
        if m != "피그마로 시안 두 번 만들어 봤어요":
            assert m not in blob


def _bad(mutate):
    def make(payload):
        d = default_draft(payload)
        mutate(d, payload)
        return d
    return make


BAD_DRAFTS = {
    "generic": lambda d, p: d["todos"][0].update(title="최종 점검 및 수정"),
    "role_plus_prep": lambda d, p: d["todos"][0].update(title=p["my_role"]["role"] + " 준비"),
    "other_part": lambda d, p: d["todos"][0].update(part="p99"),
    "part_not_covered": lambda d, p: [t.update(part=p["my_parts"][0]["id"]) for t in d["todos"]] if len(p["my_parts"]) > 1 else d["todos"].clear(),
    "outside_period": lambda d, p: d["todos"][-1].update(due_date=(date.fromisoformat(p["project"]["end_date"]) + timedelta(days=1)).isoformat()),
    "too_many": lambda d, p: d["todos"].extend([dict(copy.deepcopy(d["todos"][0]), title=f"분실물 추가 작업 {i}번") for i in range(9)]),
    "workload": lambda d, p: [t.update(estimated_minutes=400) for t in d["todos"]],
    "phase_order": lambda d, p: [t.update(phase="SUBMIT") for t in d["todos"][:1]],
    "after_milestone": lambda d, p: d["todos"][-1].update(milestone="m1"),
    "bad_evidence": lambda d, p: d["todos"][0].update(evidence_types=["PDF_UPLOAD"]),
    "empty_criteria": lambda d, p: d["todos"][0].update(completion_criteria=[]),
    "dated_criteria": lambda d, p: d["todos"][0].update(completion_criteria=["11월 24일까지 결과를 팀 폴더에 올렸다"]),
    "same_day": lambda d, p: [t.update(due_date=d["todos"][0]["due_date"]) for t in d["todos"][:3]],
    "empty_deliverable": lambda d, p: d["todos"][0].update(deliverable=""),
}


@pytest.mark.parametrize("case", sorted(BAD_DRAFTS))
def test_an_invalid_draft_is_never_stored_and_offers_an_explicit_retry(db, mk, ai, case):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    finish_interview(a, gid)
    ai.scripts["DraftOutput"] = [_bad(BAD_DRAFTS[case])]
    tstart(a, gid)
    v = td(a, gid)
    assert v["state"] == "FAILED" and v["items"] == [] and v["failed"]["kind"] == "DRAFT", case
    assert v["messages"][-1]["err"] and "저장하지 않았어요" in v["messages"][-1]["text"]
    assert scalar(db, "select count(*) from todo_items") == 0
    code = scalar(db, "select error_code from todo_ai_steps")
    assert code.startswith("INVALID_"), code
    assert len(ai.kinds("DraftOutput")) == 1  # nothing retried by itself, no template fallback
    assert retry(a, gid).status_code == 202
    v = td(a, gid)
    assert v["state"] == "READY" and len(ai.kinds("DraftOutput")) == 2


def test_provider_failure_and_a_server_restart_are_retryable(db, app, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    finish_interview(a, gid)
    ai.scripts["DraftOutput"] = [AIError("TIMEOUT", "slow")]
    tstart(a, gid)
    v = td(a, gid)
    assert v["state"] == "FAILED" and "늦거나" in v["messages"][-1]["text"]
    with db.begin() as cx:  # a step left RUNNING by a crashed process
        cx.execute(__import__("sqlalchemy").text("update todo_ai_steps set state='RUNNING', error_code=null, error_message=null"))
    assert app.state.todos.fail_interrupted() == 1
    v = td(a, gid)
    assert v["failed"] and "서버가 다시 시작" in v["messages"][-1]["text"]
    assert retry(a, gid).status_code == 202 and td(a, gid)["state"] == "READY"
    assert post(a, f"/api/groups/{gid}/todos/retry", {"action_id": uuid.uuid4().hex, "seq": 1, "expected_attempt": 7}).status_code == 409


def test_a_save_failure_writes_nothing_and_is_retryable(db, app, mk, ai, monkeypatch):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    finish_interview(a, gid)
    svc = app.state.todos
    real = svc._write_draft

    def boom(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("disk full")

    monkeypatch.setattr(svc, "_write_draft", boom)
    tstart(a, gid)
    assert scalar(db, "select count(*) from todo_items") == 0  # rolled back as a whole
    assert scalar(db, "select error_code from todo_ai_steps") == "SAVE_FAILED"
    monkeypatch.setattr(svc, "_write_draft", real)
    assert retry(a, gid).status_code == 202 and td(a, gid)["state"] == "READY"


def test_blocked_dates_are_moved_by_code_and_never_sent(db, mk, ai):
    gid, people = assigned_team(mk, 3)
    a = people[0]
    finish_interview(a, gid)
    seen = {}

    def draft(payload):
        d = default_draft(payload)
        seen["due"] = d["todos"][1]["due_date"]
        return d

    ai.scripts["DraftOutput"] = [draft]
    probe = default_draft({"period": {"first_day": today().isoformat()}, "project": {"end_date": (today() + timedelta(days=12)).isoformat()},
                           "my_parts": [{"id": "p1", "name": "x"}]})
    blocked = probe["todos"][1]["due_date"]
    with db.begin() as cx:
        cx.execute(__import__("sqlalchemy").text(
            "update role_interview_snapshots set deadline_limits = cast(:l as jsonb) where user_id=:u"),
            {"l": json.dumps({"blocked_dates": [blocked], "blocked_weekdays": []}), "u": a.id})
    tstart(a, gid)
    assert td(a, gid)["state"] == "READY"
    dues = [r.due_date.isoformat() for r in items(db, gid, a.id)]
    assert blocked not in dues
    assert blocked not in json.dumps(ai.payloads("DraftOutput"))


# ---- pure checks ----------------------------------------------------------------------------------


def _payload(weekly=180, days=13, parts=2, milestone_day=6):
    first = date(2026, 12, 1)
    return tai.base_payload(
        {"name": "캠퍼스 분실물 앱", "content": "분실물 등록 앱과 발표", "start_date": first.isoformat(),
         "end_date": (first + timedelta(days=days - 1)).isoformat(), "deliverable_types": ["웹 서비스", "발표 자료"],
         "milestones": [{"name": "중간 발표", "date": (first + timedelta(days=milestone_day)).isoformat()}]},
        {"role": "UX/UI 디자인", "description": "화면을 설계해요"},
        [{"slot": "s1", "role": "UX/UI 디자인", "parts": ["분실물 앱 화면 설계", "발표 자료 디자인"][:parts], "is_me": True},
         {"slot": "s2", "role": "개발", "parts": ["분실물 등록 기능 구현"], "is_me": False}],
        [{"id": f"p{i}", "name": n, "deliverable": d} for i, (n, d) in enumerate([("분실물 앱 화면 설계", "d1"), ("발표 자료 디자인", "d2")][:parts], 1)],
        {"current_level": "처음", "weekly_available_minutes": weekly, "tools": "UNKNOWN", "preferred_times": "UNKNOWN"},
        first, first + timedelta(days=days - 1), first,
    )


def test_workload_uses_the_known_weekly_time_or_the_fixed_cap_when_unknown():
    p = _payload(weekly=120)
    d = tai.DraftOutput.model_validate(default_draft(p, minutes=30))
    tai.check_draft(d, p, {}, date(2026, 12, 1))
    heavy = tai.DraftOutput.model_validate(default_draft(p, minutes=90))
    with pytest.raises(tai.TodoInvalid) as e:
        tai.check_draft(heavy, p, {}, date(2026, 12, 1))
    assert e.value.code == "WORKLOAD_EXCEEDED"
    pu = _payload(weekly="UNKNOWN")
    assert pu["workload"]["weekly_budget_minutes"] == tai.UNKNOWN_WEEKLY_MINUTES
    tai.check_draft(tai.DraftOutput.model_validate(default_draft(pu, minutes=70)), pu, {}, date(2026, 12, 1))
    with pytest.raises(tai.TodoInvalid):
        tai.check_draft(tai.DraftOutput.model_validate(default_draft(pu, minutes=200)), pu, {}, date(2026, 12, 1))


def test_nothing_due_before_a_milestone_is_refused():
    p = _payload()
    d = default_draft(p)
    ms = p["project"]["milestones"][0]["date"]
    for t in d["todos"]:
        if t["due_date"] <= ms:
            t["due_date"] = (date.fromisoformat(ms) + timedelta(days=1)).isoformat()
    with pytest.raises(tai.TodoInvalid) as e:
        tai.check_draft(tai.DraftOutput.model_validate(d), p, {}, date(2026, 12, 1))
    assert e.value.code in ("NOTHING_BEFORE_MILESTONE", "SAME_DAY", "NOT_SPREAD", "PHASE_ORDER")


def test_generic_titles_are_caught_and_concrete_ones_pass():
    ctx = ["분실물 앱 화면 설계", "웹 서비스", "캠퍼스 분실물 앱"]
    for bad in ("디자인 준비", "UX/UI 디자인 완성", "최종 점검 및 수정", "자료 정리", "발표 준비하기"):
        assert tai.is_generic(bad, "UX/UI 디자인", ctx), bad
    for good in ("분실물 등록 화면 와이어프레임 3장 그리기", "분실물 앱 화면 시안 피드백 반영"):
        assert not tai.is_generic(good, "UX/UI 디자인", ctx), good


def test_evidence_types_are_one_enum_defined_in_one_place():
    assert tai.UPLOAD_FORMATS_ACTIVE is False
    assert set(tai.EVIDENCE_VALUES) == {e.value for e in tai.EvidenceType}
    for path in SRC.rglob("*.py"):
        if path.name != "todo_ai.py":
            assert "DESIGN_FILE" not in path.read_text(encoding="utf-8"), path


# ---- direct edits ----------------------------------------------------------------------------------


def test_direct_update_add_delete_are_saved_on_the_server_draft(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    v = td(a, gid)
    k0, k1 = v["items"][0]["key"], v["items"][1]["key"]
    end = date.fromisoformat(v["period"]["end_date"])
    assert edit(a, gid, "update", key=k0, title="분실물 화면 시안 다시 그리기").status_code == 201
    assert edit(a, gid, "update", key=k0, due=(end - timedelta(days=1)).isoformat()).status_code == 201
    assert edit(a, gid, "delete", key=k1).status_code == 201
    r = edit(a, gid, "add", title="팀 회의록 정리")
    assert r.status_code == 201
    v = r.json()["role"]["todos"]
    assert v["draft_revision"] == 5
    titles = [it["title"] for it in v["items"]]
    assert "분실물 화면 시안 다시 그리기" in titles and "팀 회의록 정리" in titles and k1 not in [it["key"] for it in v["items"]]
    added = v["items"][-1]
    assert added["due_date"] is None and added["key"] == f"k{len(v['items']) + 1}"  # a new key, never a reused one
    rows = items(db, gid, a.id, deleted=True)
    assert len({r.task_id for r in rows}) == len(rows)
    assert scalar(db, "select count(*) from todo_items where deleted_at is not null") == 1
    assert not ai.kinds("ReviseOutput") and not ai.kinds("CriteriaOutput")  # direct edits never call the AI


def test_direct_edit_limits_period_conflict_and_count(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    v = td(a, gid)
    k0 = v["items"][0]["key"]
    end = date.fromisoformat(v["period"]["end_date"])
    r = edit(a, gid, "update", key=k0, due=(end + timedelta(days=1)).isoformat())
    assert r.status_code == 422 and r.json()["error"]["code"] == "OUTSIDE_PERIOD"
    r = edit(a, gid, "update", key=k0, due=(today() - timedelta(days=1)).isoformat())
    assert r.status_code == 422
    assert edit(a, gid, "update", key=k0, title="x" * 61).status_code in (400, 422)
    assert edit(a, gid, "update", key="k99", title="없는 투두 고치기").status_code == 404
    stale = td(a, gid)["draft_revision"]
    assert edit(a, gid, "update", key=k0, title="첫 수정 분실물 화면").status_code == 201
    r = edit(a, gid, "update", key=k0, title="충돌하는 수정", rev=stale)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    while len(td(a, gid)["items"]) < 8:
        assert edit(a, gid, "add", title=f"추가 투두 {len(td(a, gid)['items'])}").status_code == 201
    r = edit(a, gid, "add", title="아홉 번째 투두")
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_MANY"
    # a resent action changes nothing twice
    act = uuid.uuid4().hex
    rev = td(a, gid)["draft_revision"]
    assert edit(a, gid, "delete", key=td(a, gid)["items"][-1]["key"], action=act, rev=rev).status_code == 201
    n = len(td(a, gid)["items"])
    assert edit(a, gid, "delete", key=td(a, gid)["items"][-1]["key"], action=act, rev=rev).status_code == 200
    assert len(td(a, gid)["items"]) == n


def test_no_path_changes_the_role_or_the_owner(db, mk, ai):
    gid, (a, b, c) = ready(mk)
    finish_interview(b, gid)
    tstart(b, gid)
    k0 = td(a, gid)["items"][0]["key"]
    body = {"action_id": uuid.uuid4().hex, "draft_revision": td(a, gid)["draft_revision"], "op": "update", "key": k0,
            "title": "역할 바꾸기 시도", "role_name": "개발"}
    assert post(a, f"/api/groups/{gid}/todos/edit", body).status_code == 400
    body = {"action_id": uuid.uuid4().hex, "draft_revision": td(a, gid)["draft_revision"], "op": "update", "key": k0,
            "title": "남의 투두 고치기", "user_id": b.id}
    assert post(a, f"/api/groups/{gid}/todos/edit", body).status_code == 400
    # b's request with a's key only ever touches b's own draft
    mine_before = [r.title for r in items(db, gid, a.id)]
    assert edit(b, gid, "update", key=k0, title="비의 자기 투두 고치기").status_code == 201
    assert [r.title for r in items(db, gid, a.id)] == mine_before
    # the AI change set has no role/owner field: an output that adds one fails the schema check
    ai.scripts["ReviseOutput"] = [{"understood": True, "changes": [{"op": "update", "ref": "t1", "role": "개발"}], "reply": ""}]
    revise(a, gid, "내 역할을 개발로 바꿔줘")
    v = td(a, gid)
    assert v["failed"]["kind"] == "REVISE" and scalar(db, "select error_code from todo_ai_steps where kind='REVISE'") == "SCHEMA_INVALID"
    assert screen(a, gid)["role"]["role_name"] == one(db, "select role_name from role_assignment_members where user_id=:u", u=a.id).role_name
    outsider = mk("outsider9")
    assert outsider.c.get(f"/api/groups/{gid}/todos").status_code == 404
    assert edit(outsider, gid, "add", title="밖에서 추가하기", rev=1).status_code == 404


def test_edits_wait_for_a_running_ai_step(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    with db.begin() as cx:
        cx.execute(__import__("sqlalchemy").text(
            "insert into todo_ai_steps (group_id, user_id, source_assignment_revision, seq, kind, action_id, user_message, state)"
            " values (:g, :u, 1, 2, 'REVISE', 'manual-step-01', '마감 늦춰줘', 'RUNNING')"), {"g": gid, "u": a.id})
    k0 = td(a, gid)["items"][0]["key"]
    assert edit(a, gid, "update", key=k0, title="기다려야 하는 수정").json()["error"]["code"] == "BUSY"
    assert revise(a, gid, "하나 더 바꿔줘").json()["error"]["code"] == "BUSY"
    assert confirm(a, gid).json()["error"]["code"] == "BUSY"
    assert td(a, gid)["busy"] and not td(a, gid)["input_open"]


# ---- natural-language revision ---------------------------------------------------------------------


def test_a_natural_language_change_is_a_checked_change_set(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    v = td(a, gid)
    end = date.fromisoformat(v["period"]["end_date"])
    before = [r.task_id for r in items(db, gid, a.id)]
    later = (date.fromisoformat(v["items"][0]["due_date"]) + timedelta(days=1)).isoformat()

    def change(payload):
        assert payload["request"] == "첫 번째 마감을 하루 늦추고 마지막 건 빼줘"
        assert [t["ref"] for t in payload["todos"]] == [f"t{i}" for i in range(1, len(v["items"]) + 1)]
        assert all("task_id" not in t and "deliverable" not in t for t in payload["todos"])
        return {"understood": True, "reply": "",
                "changes": [{"op": "update", "ref": "t1", "due_date": later},
                            {"op": "delete", "ref": f"t{len(v['items'])}"},
                            {"op": "add", "title": "발표 자료 접근성 점검표 만들기", "due_date": end.isoformat(), "part": None}]}

    ai.scripts["ReviseOutput"] = [change]
    r = revise(a, gid, "첫 번째 마감을 하루 늦추고 마지막 건 빼줘")
    assert r.status_code == 202
    v2 = td(a, gid)
    assert v2["draft_revision"] == 2 and v2["items"][0]["due_date"] == later
    assert "수정 1개" in v2["messages"][-1]["text"] and v2["messages"][-2] == {"from": "me", "text": "첫 번째 마감을 하루 늦추고 마지막 건 빼줘"}
    after = items(db, gid, a.id)
    assert after[0].task_id == before[0] and before[-1] not in [x.task_id for x in after]
    new = after[-1]
    assert new.task_id not in before and new.criteria_for_title is None and new.deliverable is None  # made at confirm time
    step = one(db, "select * from todo_ai_steps where kind='REVISE'")
    assert step.base_draft_revision == 1 and step.result_draft_revision == 2 and step.request_id == "fixture-req"
    blob = json.dumps(ai.kinds("ReviseOutput"), ensure_ascii=False)
    forbidden_in(blob, [a], extra=("비밀메모", "rank", "순위", "user_id"))


@pytest.mark.parametrize("change,code,words", [
    ({"op": "update", "ref": "t1", "due_date": "2099-01-01"}, "DATE_OUTSIDE_PERIOD", "기간 안에서만"),
    ({"op": "update", "ref": "t9", "title": "없는 투두 이름 바꾸기"}, "REF_INVALID", "찾지 못했어요"),
    ({"op": "update", "ref": "t1", "title": "자료 정리 준비"}, "GENERIC_TITLE", "너무 일반적"),
    ({"op": "add", "title": "남의 결과물 추가하기", "due_date": None, "part": "p9"}, "OTHER_PART", None),
])
def test_a_change_set_that_breaks_a_rule_is_not_applied(db, mk, ai, change, code, words):
    gid, (a, *_r) = ready(mk)
    if change.get("op") == "add":
        change["due_date"] = td(a, gid)["period"]["end_date"]
    before = [(r.title, r.due_date) for r in items(db, gid, a.id)]
    ai.scripts["ReviseOutput"] = [{"understood": True, "changes": [change], "reply": ""}]
    revise(a, gid, "이렇게 바꿔줘")
    v = td(a, gid)
    assert v["draft_revision"] == 1 and [(r.title, r.due_date) for r in items(db, gid, a.id)] == before
    assert scalar(db, "select error_code from todo_ai_steps where kind='REVISE'") == f"NOT_APPLIED_{code}"
    if words:
        assert words in v["messages"][-1]["text"]
    assert v["failed"] is None and v["input_open"]  # no dead end: the member says it again or edits directly


def test_too_much_work_or_too_many_todos_from_a_change_set_is_refused(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    v = td(a, gid)
    day = v["items"][0]["due_date"]
    ai.scripts["ReviseOutput"] = [{"understood": True, "reply": "", "changes": [
        {"op": "add", "title": f"발표 슬라이드 추가 시안 {i}번", "due_date": day, "part": None} for i in range(3)]}]
    revise(a, gid, "첫 주에 시안 세 개 더 넣어줘")
    assert scalar(db, "select error_code from todo_ai_steps where kind='REVISE'") in ("NOT_APPLIED_WORKLOAD_EXCEEDED", "NOT_APPLIED_TOO_MANY")
    assert td(a, gid)["draft_revision"] == 1


def test_not_understood_meaningless_and_replayed_requests(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    ai.scripts["ReviseOutput"] = [{"understood": False, "changes": [], "reply": "어느 투두를 바꿀지 알려 주세요."}]
    revise(a, gid, "음 그냥 좀 바꿔")
    assert td(a, gid)["messages"][-1]["text"] == "어느 투두를 바꿀지 알려 주세요."
    calls = len(ai.kinds("ReviseOutput"))
    r = revise(a, gid, "ㅇㅇㅇ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "MEANINGLESS"
    assert len(ai.kinds("ReviseOutput")) == calls and scalar(db, "select count(*) from todo_ai_steps where kind='REVISE'") == 1
    act = uuid.uuid4().hex
    revise(a, gid, "마감을 더 여유 있게 해줘", action=act)
    assert revise(a, gid, "마감을 더 여유 있게 해줘", action=act).status_code == 200
    assert len(ai.kinds("ReviseOutput")) == calls + 1


def test_a_failed_revision_keeps_the_request_and_retries_against_the_current_draft(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    ai.scripts["ReviseOutput"] = [AIError("UPSTREAM_ERROR", "x")]
    revise(a, gid, "두 번째 투두 마감을 하루 당겨줘")
    v = td(a, gid)
    assert v["failed"]["kind"] == "REVISE" and v["messages"][-2]["text"] == "두 번째 투두 마감을 하루 당겨줘"
    d2 = (date.fromisoformat(v["items"][1]["due_date"]) - timedelta(days=1)).isoformat()
    ai.scripts["ReviseOutput"] = [{"understood": True, "reply": "", "changes": [{"op": "update", "ref": "t2", "due_date": d2}]}]
    assert retry(a, gid).status_code == 202
    v = td(a, gid)
    assert v["failed"] is None and v["draft_revision"] == 2


# ---- confirm, unlock, re-confirm ------------------------------------------------------------------


def test_confirm_fixes_one_immutable_revision_and_is_idempotent(db, mk, ai):
    gid, (a, b, c) = ready(mk)
    assert group_todos(b, gid) == {"assignments": []}  # nothing public before a confirmation
    act = uuid.uuid4().hex
    r = confirm(a, gid, action=act)
    assert r.status_code == 201 and r.json()["role"]["todos"]["mode"] == "CONFIRMED"
    assert not ai.kinds("CriteriaOutput")  # the AI draft already had valid criteria
    assert confirm(a, gid, action=act).status_code == 200  # resend
    assert confirm(a, gid).status_code == 200  # second click
    assert scalar(db, "select count(*) from todo_confirmations") == 1
    conf = one(db, "select * from todo_confirmations")
    assert conf.revision == 1 and conf.draft_revision == 1 and conf.source_assignment_revision == 1
    rows = col(db, "select task_id from todo_confirmed_items order by position")
    assert set(rows) == {r.task_id for r in items(db, gid, a.id)}
    g = group_todos(c, gid)
    role = one(db, "select role_name from role_assignment_members where user_id=:u", u=a.id).role_name
    assert g["assignments"][0]["role_name"] == role
    assert set(walk_keys(g, set())) == {"assignments", "role_name", "todos", "title", "due_date"}
    v = td(a, gid)
    assert edit(a, gid, "update", key=v["items"][0]["key"], title="확정 뒤 수정").json()["error"]["code"] == "CONFIRMED"
    assert revise(a, gid, "마감 늦춰줘").json()["error"]["code"] == "CONFIRMED"
    assert not v["input_open"]


def test_unlock_edit_and_reconfirm_keeps_task_ids_and_the_old_revision(db, mk, ai):
    gid, (a, b, c) = ready(mk)
    confirm(a, gid)
    from sqlalchemy import text

    with db.connect() as cx:
        rev1 = cx.execute(text("select * from todo_confirmed_items where confirmation_revision=1 order by position")).all()
    old_public = group_todos(b, gid)
    assert unlock(a, gid).status_code == 201
    assert group_todos(b, gid) == old_public  # the group keeps seeing revision 1 until a re-confirm
    v = td(a, gid)
    k_edit, k_del = v["items"][0]["key"], v["items"][1]["key"]
    edited_id = next(r.task_id for r in items(db, gid, a.id) if f"k{r.item_no}" == k_edit)
    deleted_id = next(r.task_id for r in items(db, gid, a.id) if f"k{r.item_no}" == k_del)
    edit(a, gid, "update", key=k_edit, title="분실물 화면 시안 두 가지 비교")
    edit(a, gid, "delete", key=k_del)
    edit(a, gid, "add", title="분실물 화면 사용성 메모 쓰기", due=v["period"]["end_date"])
    r = confirm(a, gid)
    assert r.status_code == 202
    (payload,) = ai.payloads("CriteriaOutput")
    titles = {t["ref"]: t["title"] for t in payload["todos"]}
    assert sorted(titles[x] for x in payload["targets"]) == sorted(["분실물 화면 시안 두 가지 비교", "분실물 화면 사용성 메모 쓰기"])
    v = td(a, gid)
    assert v["mode"] == "CONFIRMED" and v["messages"][-1]["text"].startswith("투두를 확정했어요")
    with db.connect() as cx:
        rev2 = cx.execute(text("select * from todo_confirmed_items where confirmation_revision=2 order by position")).all()
        again1 = cx.execute(text("select * from todo_confirmed_items where confirmation_revision=1 order by position")).all()
    assert again1 == rev1  # revision 1 criteria untouched
    ids1, ids2 = {r.task_id for r in rev1}, {r.task_id for r in rev2}
    assert edited_id in ids2 and deleted_id not in ids2
    assert len(ids2 - ids1) == 1  # only the added todo has a new task id
    e1 = next(r for r in rev1 if r.task_id == edited_id)
    e2 = next(r for r in rev2 if r.task_id == edited_id)
    assert e1.deliverable != e2.deliverable and e2.title == "분실물 화면 시안 두 가지 비교"
    unchanged = ids1 & ids2 - {edited_id}
    for t in unchanged:
        assert next(r for r in rev1 if r.task_id == t).completion_criteria == next(r for r in rev2 if r.task_id == t).completion_criteria
    assert all(r.part_id and r.deliverable and r.completion_criteria and r.evidence_types for r in rev2)
    assert set(e for r in rev2 for e in r.evidence_types) <= set(tai.EVIDENCE_VALUES)
    titles2 = [t["title"] for t in group_todos(b, gid)["assignments"][0]["todos"]]
    assert "분실물 화면 사용성 메모 쓰기" in titles2
    assert deleted_id not in col(db, "select task_id from todo_items where deleted_at is null")


def test_a_failed_criteria_step_blocks_the_confirmation(db, mk, ai):
    gid, (a, b, c) = ready(mk)
    v = td(a, gid)
    edit(a, gid, "update", key=v["items"][0]["key"], title="분실물 화면 시안 다시 그리기")
    ai.scripts["CriteriaOutput"] = [lambda p: {"items": []}]  # missing criteria
    assert confirm(a, gid).status_code == 202
    v = td(a, gid)
    assert v["mode"] == "EDITING" and v["failed"]["kind"] == "CRITERIA" and "확정하지 않았어요" in v["messages"][-1]["text"]
    assert scalar(db, "select count(*) from todo_confirmations") == 0 and group_todos(b, gid) == {"assignments": []}
    ai.scripts["CriteriaOutput"] = [AIError("TIMEOUT", "x")]
    retry(a, gid)
    assert scalar(db, "select count(*) from todo_confirmations") == 0
    assert retry(a, gid).status_code == 202
    assert td(a, gid)["mode"] == "CONFIRMED" and scalar(db, "select count(*) from todo_confirmations") == 1


def test_confirm_needs_a_title_and_due_date_for_every_todo(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    edit(a, gid, "add", title="마감 없는 분실물 화면 메모")
    r = confirm(a, gid)
    assert r.status_code == 422 and r.json()["error"]["message"] == "마감일이 비어 있는 투두가 있어요."
    for it in td(a, gid)["items"]:
        edit(a, gid, "delete", key=it["key"])
    r = confirm(a, gid)
    assert r.status_code == 422 and r.json()["error"]["message"] == "투두를 하나 이상 만들어 주세요."
    assert scalar(db, "select count(*) from todo_confirmations") == 0


def test_a_confirm_save_failure_leaves_nothing_half_written(db, app, mk, ai, monkeypatch):
    gid, (a, *_r) = ready(mk)

    def boom(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(app.state.todos, "_confirm_now", boom)
    with pytest.raises(RuntimeError):
        confirm(a, gid)
    assert scalar(db, "select count(*) from todo_confirmations") == 0 and td(a, gid)["mode"] == "EDITING"
    assert scalar(db, "select count(*) from todo_actions where kind='CONFIRM'") == 0


def test_confirmed_revisions_are_immutable_in_the_database(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    confirm(a, gid)
    sa = __import__("sqlalchemy")
    for sql in ("update todo_confirmed_items set title='바꿈'", "delete from todo_confirmed_items",
                "update todo_confirmations set draft_revision=9", "delete from todo_confirmations"):
        with pytest.raises(Exception, match="confirmed todo revisions are immutable"):  # the trigger, not any error
            with db.begin() as cx:
                cx.execute(sa.text(sql))
    with db.begin() as cx:  # session C may mark a revision STALE
        cx.execute(sa.text("update todo_confirmations set stale_at=now()"))
    assert scalar(db, "select count(*) from todo_confirmed_items") >= 4


# ---- privacy, security, recovery ------------------------------------------------------------------


def test_private_draft_and_criteria_never_reach_others_the_group_api_or_logs(db, mk, ai, caplog):
    caplog.set_level(logging.DEBUG)
    gid, (a, b, c) = ready(mk)
    revise(a, gid, "첫 번째 마감을 하루 늦춰줘 비밀요청")
    secret_rows = items(db, gid, a.id)
    secrets = [r.title for r in secret_rows] + [r.deliverable for r in secret_rows] + [x for r in secret_rows for x in r.completion_criteria]
    for other in (b, c):
        blob = json.dumps(screen(other, gid), ensure_ascii=False) + json.dumps(group_todos(other, gid), ensure_ascii=False)
        for sec in secrets + ["비밀요청"]:
            assert sec not in blob
    confirm(a, gid)
    pub = json.dumps(group_todos(b, gid), ensure_ascii=False)
    for r in items(db, gid, a.id):
        assert r.task_id not in pub and r.deliverable not in pub
        assert all(x not in pub for x in r.completion_criteria)
    forbidden_in(pub, [a, b, c], extra=("revision", "slot", "rank", "evidence", "criteria", "deliverable", "user_id"))
    logs = caplog.text
    for sec in secrets + ["비밀요청"]:
        assert sec not in logs
    assert "fixture-req" not in json.dumps(screen(a, gid))


def test_login_and_csrf_are_required_for_every_todo_endpoint(db, app, mk, ai):
    gid, (a, *_r) = ready(mk)
    from fastapi.testclient import TestClient

    anon = TestClient(app, follow_redirects=False)
    for path in ("start", "edit", "revise", "confirm", "unlock", "retry"):
        assert anon.post(f"/api/groups/{gid}/todos/{path}", json={}).status_code in (401, 403)
        assert post(a, f"/api/groups/{gid}/todos/{path}", {"action_id": uuid.uuid4().hex}, token=None).status_code == 403
    assert anon.get(f"/api/groups/{gid}/todos").status_code == 401


def test_titles_are_data_and_the_browser_code_never_writes_markup(db, mk, ai):
    gid, (a, *_r) = ready(mk)
    xss = "<img src=x onerror=alert(1)>분실물"
    edit(a, gid, "add", title=xss)
    r = a.c.get(f"/api/groups/{gid}/roles")
    assert r.headers["content-type"].startswith("application/json") and xss in [it["title"] for it in r.json()["role"]["todos"]["items"]]
    js = (SRC / "static" / "chat.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
    assert "timely" not in js.lower() and "api_key" not in js.lower()
    assert "localStorage" not in js and "sessionStorage" not in js


def test_refresh_and_restart_recover_the_draft_and_the_confirmation(db, app, mk, ai):
    gid, (a, *_r) = ready(mk)
    edit(a, gid, "update", key=td(a, gid)["items"][0]["key"], title="분실물 화면 새 시안")
    before = td(a, gid)
    fresh = reconnect(a)
    assert td(fresh, gid)["items"] == before["items"]
    from moim.config import load_settings
    from moim.main import create_app

    other = create_app(load_settings(), ai_client_factory=lambda: ai)
    try:
        from fastapi.testclient import TestClient

        with TestClient(other, follow_redirects=False) as tc:
            tc.cookies.set("moim_session", a.c.cookies.get("moim_session"), domain="testserver.local")
            tc.cookies.set("moim_csrf", a.c.cookies.get("moim_csrf"), domain="testserver.local")
            v = tc.get(f"/api/groups/{gid}/roles").json()["role"]["todos"]
            assert v["items"] == before["items"] and v["draft_revision"] == before["draft_revision"]
    finally:
        other.state.engine.dispose()
    confirm(a, gid)
    assert td(reconnect(a), gid)["mode"] == "CONFIRMED"


def test_one_members_draft_never_blocks_another(db, mk, ai):
    gid, (a, b, c) = assigned_team(mk, 3)
    finish_interview(b, gid)
    ai.scripts["DraftOutput"] = [AIError("TIMEOUT", "x")]
    tstart(b, gid)
    assert td(b, gid)["state"] == "FAILED"
    finish_interview(c, gid)
    tstart(c, gid)
    assert td(c, gid)["state"] == "READY"
    confirm(c, gid)
    assert len(group_todos(a, gid)["assignments"]) == 1  # a has not even finished the interview


def test_migration_0009_down_and_up_keeps_the_stage_4_interview_data(db, grp_db, mk, ai):
    gid, (a, *_r) = ready(mk)
    cfg = Config(str(ROOT / "alembic.ini"))
    try:
        command.downgrade(cfg, "0008")
        for t in ("todo_drafts", "todo_items", "todo_ai_steps", "todo_confirmations", "todo_confirmed_items", "todo_actions"):
            assert scalar(db, f"select to_regclass('public.{t}')") is None
        assert scalar(db, "select count(*) from role_interview_snapshots where group_id=:g", g=gid) == 1
        assert scalar(db, "select count(*) from role_assignments where group_id=:g", g=gid) == 1
        command.upgrade(cfg, "head")
        assert scalar(db, "select to_regclass('public.todo_confirmed_items')") is not None
        assert scalar(db, "select count(*) from role_interview_snapshots where group_id=:g", g=gid) == 1
    finally:
        command.upgrade(cfg, "head")
