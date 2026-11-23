"""Stage 8D deterministic contract: the portfolio API, the summaries and their checks (RAG_DESIGN 2.5.1-2.5.2, 3.6,
7.3; D-I1, D-I2).

Everything here is FIXTURE, never LIVE: the corpus is written straight into the tables (`portfolio_corpus.py`) and the
AI client is scripted. What a real model writes is a LIVE question (`docs/LIVE_TESTING.md`).
"""
from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from moim.config import load_settings
from moim.main import create_app
from moim.portfolio import repo
from moim.portfolio import summary as SM
from moim.portfolio.routes import IMPORT_CALL_LIMIT, estimate_import_calls
import portfolio_corpus as PC
from test_groups import db, grp_db, user_row  # noqa: F401
from test_portfolio_retrieval import AskAI

ID_RE = re.compile(r'<document id="([^"]+)"[^>]*>')
FORBIDDEN_KEYS = {"embedding", "index_text", "context", "bigrams", "stage_meta", "basis", "last_action_id",
                  "source_block_ids", "refs", "deliverable", "completion_criteria", "prompt", "path", "storage_path"}


class SumAI(AskAI):
    """AskAI plus scripted summary outputs. Default outputs cite the first input ids."""

    def __init__(self):
        super().__init__()
        self.scripts.update({"ProjectSummaryOut": [], "PortfolioSummaryOut": []})

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        name = output_schema.__name__
        if name not in ("ProjectSummaryOut", "PortfolioSummaryOut"):
            return super().complete_json(messages, output_schema, options, stats=stats)
        from types import SimpleNamespace as NS

        from wemeet_ai.shared.contracts import Usage

        self.calls.append((name, messages))
        ids = [i for i in ID_RE.findall(messages[-1]["content"]) if i[0] in "cp"]
        item = self.scripts[name].pop(0) if self.scripts[name] else None
        if isinstance(item, Exception):
            raise item
        if callable(item):
            data = item(ids)
        elif item is not None:
            data = item
        elif name == "ProjectSummaryOut":
            data = {"did": [{"text": "길찾기 앱의 화면을 설계했어요.", "sources": ids[:1]}],
                    "made": [{"text": "사용성 테스트 결과를 정리했어요.", "sources": ids[1:2] or ids[:1]}], "learned": []}
        else:
            data = {"intro": [{"text": "사용자 조사로 화면을 고치는 일을 했어요.", "sources": ids[:2]}],
                    "skills": [{"name": "사용자 조사", "sources": ids[:2]}, {"name": "사용자  조사", "sources": ids[2:3] or ids[:1]},
                               {"name": "화면 설계", "sources": ids[:1]}]}
        parsed = output_schema.model_validate(data)
        return NS(parsed_output=parsed, model="fixture-model", latency_ms=5, provider_request_id="fx",
                  usage=Usage(prompt_tokens=50, completion_tokens=10, total_tokens=60))


@pytest.fixture()
def ai():
    return SumAI()


@pytest.fixture()
def app(db, ai):
    application = create_app(load_settings(), ai_client_factory=lambda: ai)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def world(db, app):
    a = user_row(db, app, "ragusera")
    b = user_row(db, app, "raguserb")
    ca = PC.build(db, PC.USER_A, user_id=a.id)
    cb = PC.build(db, PC.USER_B, user_id=b.id)
    return SimpleNamespace(a=a, b=b, ca=ca, cb=cb)


def post(u, path, body=None, token=..., method="post"):
    h = {}
    if token is ...:
        token = u.c.cookies.get("moim_csrf")
    if token:
        h["X-CSRF-Token"] = token
    if method == "delete":
        return u.c.delete(path, headers=h)
    return u.c.post(path, json=body if body is not None else {}, headers=h)


def act() -> str:
    return "act-" + uuid.uuid4().hex[:16]


def keys_of(obj) -> set:
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= keys_of(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= keys_of(v)
    return out


def first_project(w) -> str:
    return w.ca.projects["캠퍼스 길찾기 앱"]


# ---- reading --------------------------------------------------------------------------------------------------------------


def test_overview_lists_only_the_owners_projects_and_leaks_nothing_internal(world):
    r = world.a.c.get("/api/portfolio")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    names = {p["name"] for p in data["projects"]}
    assert names == set(world.ca.projects)
    assert data["portfolio_summary"] is None and data["suggestions"]
    assert not keys_of(data) & FORBIDDEN_KEYS
    body = r.text
    assert "베타팀" not in body and "실패작업비밀" not in body  # B's marker, A's FAILED item's material
    failed = [p for p in data["projects"] if p["failed"]]
    assert failed and all(p["ready"] + p["failed"] + p["running"] == p["items"] for p in data["projects"])


def test_project_detail_lists_items_files_and_states_without_material(world):
    pid = first_project(world)
    r = world.a.c.get(f"/api/portfolio/projects/{pid}")
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "캠퍼스 길찾기 앱" and d["summary"] is None and d["suggestions"]
    assert all(i["state"] in ("READY", "FAILED", "RUNNING") for i in d["items"])
    assert all(f["name"] and f["kind"] for i in d["items"] for f in i["files"])
    assert not keys_of(d) & FORBIDDEN_KEYS
    assert "신입생" not in r.text  # attachment text is not part of the detail (only the AI summary and excerpts)


def test_another_users_objects_answer_404_like_missing_ones(world, db):
    pid = first_project(world)
    item = next(iter(world.ca.items.values()))
    with db.connect() as c:
        job = c.execute(text("select id, revision from portfolio_jobs where item_id = :i"), {"i": item}).one()
    b = world.b
    assert b.c.get(f"/api/portfolio/projects/{pid}").status_code == 404
    assert b.c.get(f"/api/portfolio/projects/{uuid.uuid4()}").status_code == 404
    assert post(b, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()}).status_code == 404
    assert post(b, f"/api/portfolio/jobs/{job.id}/retry", {"expected_revision": job.revision}).status_code == 404
    assert post(b, f"/api/portfolio/items/{item}", method="delete").status_code == 404
    assert post(b, f"/api/portfolio/projects/{pid}", method="delete").status_code == 404
    assert post(b, "/api/portfolio/query", {"question": "무엇을 했나요?", "project_id": pid}).status_code == 404
    with db.connect() as c:
        assert c.execute(text("select count(*) from portfolio_items where user_id = :u"), {"u": world.a.id}).scalar() \
            == len(world.ca.items)


def test_state_changes_need_csrf_and_sign_in(world, app):
    pid = first_project(world)
    item = next(iter(world.ca.items.values()))
    for path, body in ((f"/api/portfolio/projects/{pid}/summary", {"action_id": act()}),
                       ("/api/portfolio/summary", {"action_id": act()}),
                       ("/api/portfolio/query", {"question": "무엇을 했나요?"}),
                       ("/api/portfolio/import", {"action_id": act()})):
        assert post(world.a, path, body, token=None).status_code == 403
    assert post(world.a, f"/api/portfolio/items/{item}", token=None, method="delete").status_code == 403
    from fastapi.testclient import TestClient

    anon = TestClient(app)
    assert anon.get("/api/portfolio").status_code == 401
    assert anon.get(f"/api/portfolio/projects/{pid}").status_code == 401


def test_bad_bodies_are_refused(world):
    pid = first_project(world)
    assert post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": "x"}).status_code == 422
    assert post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act(), "extra": 1}).status_code == 400
    assert post(world.a, "/api/portfolio/query", {"question": ""}).status_code == 422


# ---- project summary ------------------------------------------------------------------------------------------------------


def test_project_summary_is_cited_resolved_searchable_and_idempotent(world, ai, db, app):
    pid = first_project(world)
    action = act()
    r = post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": action})
    assert r.status_code == 202
    assert len(ai.of("ProjectSummaryOut")) == 1 and len(ai.of("embed")) == 1
    prompt = ai.of("ProjectSummaryOut")[0][-1]["content"]
    assert "베타팀" not in prompt and "실패작업비밀" not in prompt  # owner's READY material only
    again = post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": action})
    assert again.status_code == 202 and len(ai.of("ProjectSummaryOut")) == 1  # the same click: no second run
    d = world.a.c.get(f"/api/portfolio/projects/{pid}").json()
    s = d["summary"]
    assert s["state"] == "READY" and not s["stale"]
    assert s["did"] and all(e["citations"] and " · " in e["citations"][0]["label"] for e in s["did"] + s["made"])
    assert not keys_of(d) & FORBIDDEN_KEYS
    assert sum(i["cited"] for i in d["items"]) >= 1
    with app.state.engine.connect() as conn:
        node = conn.execute(text("select source_block_ids, embedding is not null as emb from portfolio_nodes "
                                 "where kind = 'project_summary' and user_id = :u"), {"u": world.a.id}).one()
    assert node.emb and all("/" in x for x in node.source_block_ids)
    with app.state.portfolio_ask._sf() as sess:
        rows = repo.search_scope(sess, user_id=world.a.id, project_id=pid)
    assert any(r.kind == "project_summary" for r in rows)


def test_a_summary_citing_an_unknown_id_fails_whole_and_keeps_the_last_good_one(world, ai):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    good = world.a.c.get(f"/api/portfolio/projects/{pid}").json()["summary"]
    ai.scripts["ProjectSummaryOut"].append({"did": [{"text": "지어낸 문장이에요.", "sources": ["c999"]}]})
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    s = world.a.c.get(f"/api/portfolio/projects/{pid}").json()["summary"]
    assert s["state"] == "FAILED" and s["error_code"] == "CITATION_INVALID"
    assert s["did"] == good["did"]  # content of the last good run stays


@pytest.mark.parametrize("bad", [
    {"did": [{"text": "근거 없는 문장이에요.", "sources": []}]},
    {"did": [{"text": "근거가 너무 많아요.", "sources": ["c1", "c2", "c3", "c4"]}]},
    {"did": [{"text": "같은 근거 두 번이에요.", "sources": ["c1", "c1"]}]},
    {"did": [{"text": "   ", "sources": ["c1"]}]},
    {"did": [], "made": [], "learned": []},
])
def test_project_summary_server_checks(bad):
    out = SM.ProjectSummaryOut.model_validate(bad)
    with pytest.raises(SM.SummaryError) as e:
        SM.check_project(out, {"c1", "c2", "c3", "c4"})
    assert e.value.code == "CITATION_INVALID"


def test_a_new_ready_item_marks_the_summary_stale_without_regenerating(world, ai, db):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    calls = len(ai.calls)
    with db.begin() as c:
        c.execute(text("update portfolio_jobs set state = 'READY', failed_stage = null, error_code = null "
                       "where user_id = :u and state = 'FAILED'"), {"u": world.a.id})
    over = world.a.c.get("/api/portfolio").json()
    stale = [p for p in over["projects"] if p["summary"] and p["summary"]["stale"]]
    d = world.a.c.get(f"/api/portfolio/projects/{pid}").json()
    assert (stale and stale[0]["id"] == pid) or d["summary"]["stale"]
    assert len(ai.calls) == calls  # nothing ran automatically


def test_nothing_ready_cannot_be_summarized(world, db):
    with db.begin() as c:
        c.execute(text("update portfolio_jobs set state = 'FAILED', failed_stage = 'EMBEDDING', "
                       "error_code = 'X' where user_id = :u"), {"u": world.a.id})
    r = post(world.a, f"/api/portfolio/projects/{first_project(world)}/summary", {"action_id": act()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_SUMMARIZE"


def test_summary_requests_are_rate_limited(world, monkeypatch, app):
    from moim.auth import throttle as th

    monkeypatch.setattr(SM, "SUMMARY_USER", th.Rule("portfolio-summary-user", 2, 3600, 3600))
    pid = first_project(world)
    codes = [post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()}).status_code
             for _ in range(3)]
    assert codes[:2] == [202, 202] and codes[2] == 429


def test_an_interrupted_run_becomes_failed_and_keeps_content(world, app, db):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    with db.begin() as c:
        c.execute(text("update portfolio_summaries set state = 'RUNNING' where user_id = :u"), {"u": world.a.id})
    assert app.state.portfolio_summaries.fail_interrupted() == 1
    s = world.a.c.get(f"/api/portfolio/projects/{pid}").json()["summary"]
    assert s["state"] == "FAILED" and s["error_code"] == "SERVER_RESTARTED" and s["did"]


# ---- portfolio summary ----------------------------------------------------------------------------------------------------


def test_portfolio_summary_intro_and_skills_counted_by_the_server(world, ai):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    r = post(world.a, "/api/portfolio/summary", {"action_id": act()})
    assert r.status_code == 202
    prompt = ai.of("PortfolioSummaryOut")[0][-1]["content"]
    assert re.search(r'<document id="p\d+" project="캠퍼스 길찾기 앱"', prompt)  # its project summary is the input
    s = world.a.c.get("/api/portfolio").json()["portfolio_summary"]
    assert s["state"] == "READY" and s["intro"] and s["intro"][0]["citations"]
    names = [k["name"] for k in s["skills"]]
    assert len([n for n in names if "".join(n.split()) == "사용자조사"]) == 1  # same name merged
    assert all(isinstance(k["documents"], int) and k["documents"] >= 1 for k in s["skills"])
    assert s["basis_counts"]["items"] >= 1


def test_portfolio_summary_checks():
    ids = {"p1", "p2"}
    with pytest.raises(SM.SummaryError):
        SM.check_portfolio(SM.PortfolioSummaryOut.model_validate({"intro": [], "skills": []}), ids)
    with pytest.raises(SM.SummaryError):
        SM.check_portfolio(SM.PortfolioSummaryOut.model_validate(
            {"intro": [{"text": "문장", "sources": ["p1"]}], "skills": [{"name": "가" * 21, "sources": ["p1"]}]}), ids)
    with pytest.raises(SM.SummaryError):
        SM.check_portfolio(SM.PortfolioSummaryOut.model_validate(
            {"intro": [{"text": "문장", "sources": ["p1"]}], "skills": [{"name": "조사", "sources": ["zz"]}]}), ids)
    SM.check_portfolio(SM.PortfolioSummaryOut.model_validate(
        {"intro": [{"text": "문장", "sources": ["p1"]}], "skills": [{"name": "조사", "sources": ["p1", "p2"]}]}), ids)


# ---- deletions prune derived text (D-I1 I4) ---------------------------------------------------------------------------------


def test_deleting_an_item_removes_summary_text_resting_on_it(world, db, app):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    post(world.a, "/api/portfolio/summary", {"action_id": act()})
    with db.connect() as c:
        content = c.execute(text("select content from portfolio_summaries where project_id = :p"), {"p": pid}).scalar()
    cited_items = {r[0] for g in SM.GROUPS for e in content[g] for r in e["refs"]}
    victim = sorted(cited_items)[0]
    assert post(world.a, f"/api/portfolio/items/{victim}", method="delete").status_code == 200
    with db.connect() as c:
        rows = c.execute(text("select content from portfolio_summaries where user_id = :u"), {"u": world.a.id}).scalars().all()
        nodes = c.execute(text("select count(*) from portfolio_nodes where kind = 'project_summary' and project_id = :p"),
                          {"p": pid}).scalar()
    assert victim not in json.dumps(rows)
    assert nodes == 0
    assert post(world.a, f"/api/portfolio/items/{victim}", method="delete").status_code == 404


def test_deleting_a_project_takes_its_summary_and_prunes_the_portfolio_summary(world, db):
    pid = first_project(world)
    post(world.a, f"/api/portfolio/projects/{pid}/summary", {"action_id": act()})
    post(world.a, "/api/portfolio/summary", {"action_id": act()})
    with db.connect() as c:
        gone = set(c.execute(text("select id from portfolio_items where project_id = :p"), {"p": pid}).scalars().all())
    assert post(world.a, f"/api/portfolio/projects/{pid}", method="delete").status_code == 200
    with db.connect() as c:
        left = c.execute(text("select scope, content from portfolio_summaries where user_id = :u"), {"u": world.a.id}).all()
    assert [r.scope for r in left] == ["PORTFOLIO"]
    refs = [ref[0] for v in left[0].content.values() if isinstance(v, list) for e in v for ref in e["refs"]]
    assert gone and not set(refs) & gone


# ---- questions ----------------------------------------------------------------------------------------------------------


def test_an_answer_numbers_its_evidence_with_server_cut_excerpts(world, ai):
    # search path for user A: embed, rerank, answer (the fixture grades the first candidate 2 and cites it)
    r = post(world.a, "/api/portfolio/query", {"question": "사용성 테스트에서 과제 완료 시간이 얼마나 줄었어?"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] in ("ANSWERED", "PARTIAL")
    assert d["sentences"] and all(s["cites"] for s in d["sentences"])
    assert [e["n"] for e in d["evidence"]] == list(range(1, len(d["evidence"]) + 1))
    for e in d["evidence"]:
        assert 0 < len(e["excerpt"]) <= 200 and not e["excerpt"].startswith("[")  # no index header
        assert "투두에 첨부한 자료다" not in e["excerpt"]  # no LLM context line
    assert d["files"] >= 1 and d["suggestions"]
    assert not keys_of(d) & FORBIDDEN_KEYS


def test_a_refusal_offers_what_the_scope_holds(world, ai):
    ai.scripts["Relevance"].append(lambda docs: {"judgments": [{"id": i, "relevance": 0} for i, _ in docs]})
    r = post(world.a, "/api/portfolio/query", {"question": "서버 배포는 어떻게 했나요?"})
    d = r.json()
    assert d["status"] == "REFUSED" and d["message"] == "포트폴리오 기록에서 찾지 못했어요"
    assert d["sentences"] == [] and d["evidence"] == []
    assert d["available"] and len(d["available"]) <= 3 and d["suggestions"]
    assert not ai.of("Answer")  # INCORRECT: no generation call


def test_a_refused_project_answer_lists_that_projects_titles_only(world, ai):
    ai.scripts["Answer"].append({"refused": True, "sentences": [], "missing": None})
    pid = first_project(world)
    d = post(world.a, "/api/portfolio/query", {"question": "서버 배포는 어떻게 했나요?", "project_id": pid}).json()
    assert d["status"] == "REFUSED"
    with world.a.c.app.state.portfolio_ask._sf() as s:
        mine = {i.task_title for i in repo.ready_items(s, user_id=world.a.id, project_id=pid)}
    assert d["available"] and set(d["available"]) <= mine


def test_an_ai_failure_is_an_error_not_an_answer(world, ai):
    from wemeet_ai.m0.client import AIError

    ai.scripts["embed"].append(AIError("UPSTREAM_ERROR", "fx"))
    r = post(world.a, "/api/portfolio/query", {"question": "무엇을 만들었나요?"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "ANSWER_FAILED"


# ---- import (D-I1 I6) -----------------------------------------------------------------------------------------------------


def test_import_estimate_and_its_limit():
    assert estimate_import_calls([("r1", ["DOCX"]), ("r2", ["PNG", "PDF"])]) == 2 + (2 + 3)
    assert estimate_import_calls([("r", ["PNG"] * 20)]) == 2 + 8  # vision capped per submission
    big = [(f"r{i}", ["PDF", "PNG"]) for i in range(20)]
    assert estimate_import_calls(big) >= IMPORT_CALL_LIMIT


def test_import_with_nothing_to_import(world):
    r = post(world.a, "/api/portfolio/import", {"action_id": act()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_IMPORT"
    assert world.a.c.get("/api/portfolio").json()["import"] == {"records": 0, "estimated_calls": 0, "allowed": False}
