"""Stage 8C deterministic contract: portfolio search, the evidence gate and cited answers (RAG_DESIGN 3, 7.3).

Everything here is FIXTURE, never LIVE. The corpus (`portfolio_corpus.py`) is written straight into the tables with
seeded embeddings; `AskAI` returns a scripted question vector placed near the intended evidence, scripted grades and
scripted answers, validating them against the real schemas the way M0 does. What a real model does with injected
text is a LIVE question (`docs/LIVE_TESTING.md`); here we check what the server controls.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from moim.auth.throttle import Rule, Throttle
from moim.config import load_settings
from moim.db import make_session_factory
from moim.main import create_app
from moim.portfolio import ask as A
from moim.portfolio import repo
from moim.portfolio.understand import DATA_RULE, _inert
from wemeet_ai.m0.client import AIError, Embeddings, schema_instruction
from wemeet_ai.shared.contracts import Usage
import portfolio_corpus as PC
from test_groups import db, grp_db  # noqa: F401

DOC_RE = re.compile(r"<document id=\"([^\"]+)\">\n(.*?)\n</document>", re.S)


# ---- the FIXTURE client -------------------------------------------------------------------------------------------------


def docs_of(messages) -> list[tuple[str, str]]:
    return DOC_RE.findall(messages[-1]["content"])


class AskAI:
    """Scripts per kind ("embed", "Relevance", "Answer"): a value, a callable, or an AIError to raise. Never LIVE."""
    is_fixture = True

    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.scripts = {"embed": [], "Relevance": [], "Answer": []}

    def of(self, kind):
        return [p for k, p in self.calls if k == kind]

    def embed(self, texts, *, model=None, stats=None):
        self.calls.append(("embed", list(texts)))
        item = self.scripts["embed"].pop(0) if self.scripts["embed"] else PC.seeded(texts[0])
        if isinstance(item, Exception):
            raise item
        v = np.asarray(item, dtype=np.float32).reshape(1, -1)
        return Embeddings(v, PC.MODEL, v.shape[1], Usage(prompt_tokens=7, total_tokens=7), 3)

    def complete_json(self, messages, output_schema, options=None, *, stats=None):
        name = output_schema.__name__
        self.calls.append((name, messages))
        item = self.scripts[name].pop(0) if self.scripts[name] else None
        if isinstance(item, Exception):
            raise item
        docs = docs_of(messages)
        if callable(item):
            data = item(docs)
        elif item is not None:
            data = item
        elif name == "Relevance":
            data = {"judgments": [{"id": i, "relevance": 2 if n == 0 else 0} for n, (i, _) in enumerate(docs)]}
        else:
            data = {"refused": False, "sentences": [{"text": "자료에 따른 답이다.", "citations": [docs[0][0]]}],
                    "missing": None}
        try:
            parsed = output_schema.model_validate(data)
        except ValidationError:
            raise AIError("SCHEMA_INVALID", "fixture output does not match the schema") from None
        return SimpleNamespace(parsed_output=parsed, model="fixture-model", latency_ms=5, provider_request_id="fx",
                               usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120))


def grade(corpus_texts: dict, grades: dict[str, int]):
    """A Relevance script grading by node id (others 0)."""
    def run(docs):
        node_of = {_inert(t): nid for nid, t in corpus_texts.items()}
        return {"judgments": [{"id": i, "relevance": grades.get(node_of.get(body), 0), "reason_short": "fx"}
                              for i, body in docs]}
    return run


def nodes_in(corpus_texts: dict, body: str) -> list[str]:
    """The nodes one evidence document holds (an evidence group is several index texts, in order)."""
    found = [(body.find(_inert(t)), nid) for nid, t in corpus_texts.items() if _inert(t) in body]
    return [nid for _, nid in sorted(found)]


def cite(corpus_texts: dict, *node_ids, text_="답 문장이다.", missing=None):
    """An Answer script citing the documents that hold the given nodes (by their prompt ids), one sentence."""
    def run(docs):
        ids = []
        for i, body in docs:
            if set(nodes_in(corpus_texts, body)) & set(node_ids) and i not in ids:
                ids.append(i)
        return {"refused": False, "sentences": [{"text": text_, "citations": ids}], "missing": missing}
    return run


# ---- fixtures -------------------------------------------------------------------------------------------------------------


@pytest.fixture()
def ai():
    return AskAI()


@pytest.fixture()
def world(db):
    a = PC.build(db, PC.USER_A)
    b = PC.build(db, PC.USER_B)
    c = PC.build(db, PC.USER_C)
    texts = {**a.texts, **b.texts, **c.texts}
    return SimpleNamespace(a=a, b=b, c=c, texts=texts)


@pytest.fixture()
def svc(db, ai):
    sf = make_session_factory(db)
    return A.PortfolioAsk(sf, lambda: ai, Throttle(sf))


def prompt_groups(world, messages) -> list[list[str]]:
    return [nodes_in(world.texts, body) or [None] for _, body in docs_of(messages)]


def prompt_node_ids(world, messages) -> list[str]:
    return [x for g in prompt_groups(world, messages) for x in g]


def ask_a(svc, ai, world, target: str, question="사용성 테스트에서 과제 완료 시간이 얼마나 줄었어?", **kw):
    ai.scripts["embed"].append(PC.near(world.a.vectors[target]))
    return svc.ask(world.a.user_id, question, kw or None)


# ---- pure parts -------------------------------------------------------------------------------------------------------------


def test_bm25_matches_a_hand_computation_and_counts_a_repeated_query_term_once():
    docs = [["a", "b"], ["a", "a", "c"], ["d"]]
    scores = A.bm25(["a", "c", "a"], docs)
    idf_a, idf_c = math.log(1.6), math.log(1 + 2.5 / 1.5)
    norm1 = 1.2 * (0.25 + 0.75 * 3 / 2)
    assert scores[0] == pytest.approx(idf_a * 2.2 / (1 + 1.2))
    assert scores[1] == pytest.approx(idf_a * 2 * 2.2 / (2 + norm1) + idf_c * 2.2 / (1 + norm1))
    assert scores[2] == 0
    assert A.top(scores, 50, positive_only=True) == [1, 0]
    assert A.bm25(["x"], []) == []


def test_bm25_tokens_of_a_mixed_korean_question_match_the_index_tokens():
    q = A.bm25_tokens("과제 완료 시간 94초 API?")
    assert q == ["과제", "완료", "시간", "94", "초", "api"]


def test_rrf_uses_k_60_one_entry_per_id_and_keeps_the_top_25():
    fused = A.rrf([["x", "y", "z", "x"], ["z", "w"]])
    assert [i for i, _ in fused] == ["z", "x", "y", "w"]
    assert dict(fused)["z"] == pytest.approx(1 / 61 + 1 / 63)
    assert dict(fused)["x"] == pytest.approx(1 / 61)
    many = A.rrf([[f"v{i}" for i in range(50)], [f"l{i}" for i in range(50)]])
    assert len(many) == 25 and many[0][0] == "v0" and many[1][0] == "l0"


def _n(i, tokens=200, prev=None, nxt=None, kind="chunk"):
    return A.Node(i, "p", "it", "d", kind, {"page": 1}, "", i, [], tokens, prev, nxt, ["b1"], "f.pdf", None, None, None)


def test_gate_three_branches_and_evidence_selection():
    assert A.gate({"a": 2, "b": 0}) == "CORRECT"
    assert A.gate({"a": 1, "b": 0}) == "AMBIGUOUS"
    assert A.gate({"a": 0}) == "INCORRECT" and A.gate({}) == "INCORRECT"
    order = [f"n{i}" for i in range(10)]
    nodes = {x: _n(x) for x in order}
    rel = {"n0": 1, "n1": 2, "n2": 0, "n3": 1, "n4": 2, "n5": 1, "n6": 1, "n7": 1, "n8": 1, "n9": 0}
    groups = A.select_evidence(order, rel, nodes)
    assert [g[0] for g in groups] == ["n1", "n4", "n0", "n3", "n5", "n6"]  # relevance, then RRF order, at most 6
    assert A.u_order([["a"], ["b"], ["c"], ["d"]]) == [["a"], ["c"], ["d"], ["b"]]
    assert A.u_order([["a"], ["b"]]) == [["a"], ["b"]]


def test_neighbours_join_when_graded_or_the_chunk_is_small(monkeypatch):
    nodes = {"p": _n("p", nxt="s"), "s": _n("s", tokens=120, prev="p", nxt="q"), "q": _n("q", prev="s"),
             "x": _n("x", tokens=300, prev="w", nxt="y"), "w": _n("w", nxt="x"), "y": _n("y", prev="x")}
    # a small chosen chunk takes both neighbours, even ungraded
    assert A.select_evidence(["s"], {"s": 2}, nodes) == [["p", "s", "q"]]
    # a big chunk takes only a neighbour graded ≥1 (here one left out by the cap of chosen nodes)
    monkeypatch.setattr(A, "MAX_EVIDENCE", 1)
    assert A.select_evidence(["x", "y", "w"], {"x": 2, "y": 1, "w": 0}, nodes) == [["x", "y"]]
    monkeypatch.setattr(A, "MAX_EVIDENCE", 6)
    assert A.select_evidence(["x", "y", "w"], {"x": 2, "y": 1, "w": 0}, nodes) == [["x"], ["y"]]
    # a neighbour outside the scope is never added
    assert A.select_evidence(["s"], {"s": 2}, {"s": nodes["s"]}) == [["s"]]


def test_citation_checks_fail_the_whole_answer():
    ok = A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=["1"]),
                                            A.Sentence(text="b", citations=["1", "2", "3"])])
    A.check_answer(ok, {"1", "2", "3"})
    A.check_answer(A.Answer(refused=True), {"1"})
    bad = [
        A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=["7"])]),            # not evidence
        A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=[])]),               # none
        A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=["1", "2", "3", "4"])]),  # four
        A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=["1", "1"])]),       # repeated
        A.Answer(refused=False, sentences=[A.Sentence(text="a", citations=["1"]),
                                           A.Sentence(text="b", citations=[])]),               # one bad of two
        A.Answer(refused=False, sentences=[A.Sentence(text=" ", citations=["1"])]),            # empty text
        A.Answer(refused=False, sentences=[]),                                                 # nothing said
    ]
    reasons = ["NOT_EVIDENCE", "UNCITED", "TOO_MANY", "REPEATED", "UNCITED", "EMPTY_TEXT", "NO_SENTENCES"]
    for answer, reason in zip(bad, reasons):
        with pytest.raises(A.AskError) as e:
            A.check_answer(answer, {"1", "2", "3"})
        assert e.value.code == "CITATION_INVALID" and e.value.reason == reason


def test_the_small_corpus_estimate_is_the_whole_generation_input():
    docs = [("1", "가" * 100), ("2", "나" * 100)]
    m = A.answer_messages("질문이다", docs)
    est = A.prompt_estimate(m, A.Answer)
    assert m[1]["content"].count("질문이다") == 2
    parts = A.estimate(schema_instruction(A.Answer)) + A.estimate(m[0]["content"]) + A.estimate(m[1]["content"])
    assert est == parts
    assert A.prompt_estimate(A.answer_messages("질문이다", docs + [("3", "다" * 50)]), A.Answer) > est
    assert A.prompt_estimate(A.answer_messages("질문이다 더 길게", docs), A.Answer) > est


@pytest.mark.parametrize("q", ["", "   ", "가" * 301, "질문\x00", 5, None])
def test_bad_questions_are_refused(q):
    with pytest.raises(A.AskError) as e:
        A.clean_question(q)
    assert e.value.code == "QUESTION_INVALID"


# ---- scope (3.1) -------------------------------------------------------------------------------------------------------------


def test_three_users_never_mix_in_candidates_evidence_or_citations(db, world, svc, ai):
    others = set(world.b.texts) | set(world.c.texts)
    for target in ["a_report2", "a_dev2b", "a_conti1", "a_table"]:
        ai.calls.clear()
        r = ask_a(svc, ai, world, world.a.node(target), "과제 완료 시간 설문 응답 검색 응답 베타팀 감마")
        assert r.meta["path"] == "search" and r.meta["nodes"] == len(world.a.texts) - 1  # the FAILED item is out
        for kind in ("Relevance", "Answer"):
            for msgs in ai.of(kind):
                ids = prompt_node_ids(world, msgs)
                assert None not in ids and not set(ids) & others
                assert "베타팀" not in msgs[-1]["content"].replace("베타팀 감마", "") and "실패작업비밀" not in msgs[-1]["content"]
        for s in r.sentences:
            assert all(c.project_id in world.a.projects.values() for c in s["citations"])
    # user B asking B's question sees only B
    ai.calls.clear()
    ai.scripts["embed"].append(PC.near(world.b.vectors[world.b.node("b_report")]))
    r = svc.ask(world.b.user_id, "과제 완료 시간은?")
    assert r.meta["path"] == "small"
    ids = prompt_node_ids(world, ai.of("Answer")[0])
    assert set(ids) <= set(world.b.texts) and ai.of("embed") == []


def test_filters_narrow_inside_the_owner_scope_and_another_users_project_is_not_found(db, world, svc, ai):
    video = world.a.projects["축제 홍보 영상"]
    ai.calls.clear()
    r = svc.ask(world.a.user_id, "콘티 장면 수는?", {"project_id": video})
    assert r.meta["path"] == "small" and r.meta["nodes"] == sum(
        1 for t in world.a.texts.values() if t.startswith("[축제 홍보 영상"))
    r = svc.ask(world.a.user_id, "콘티 장면 수는?", {"since": date(2026, 9, 25), "until": date(2026, 9, 28),
                                                 "project_id": video})
    assert r.meta["nodes"] == 1  # 자막 작업 only (completed 9/28 KST, one chunk, no summary)
    with pytest.raises(A.AskError) as e:
        svc.ask(world.a.user_id, "질문", {"project_id": world.b.projects["캠퍼스 길찾기 앱"]})
    assert e.value.code == "NOT_FOUND"
    for bad in ({"user_id": world.b.user_id}, {"project_id": "x"}, {"since": "2026-01-01"},
                {"since": date(2026, 10, 2), "until": date(2026, 10, 1)}):
        with pytest.raises(A.AskError) as e:
            svc.ask(world.a.user_id, "질문", bad)
        assert e.value.code == "QUESTION_INVALID"
    with pytest.raises(repo.ScopeError):
        svc.ask("' or 1=1 --", "질문")


def test_the_scope_query_fixes_owner_and_ready_first(db, world):
    sf = make_session_factory(db)
    with sf() as s:
        rows = repo.search_scope(s, user_id=world.a.user_id)
        assert {r.id for r in rows} == set(world.a.texts) - set(world.a.nodes["a_failed"])
        assert repo.search_scope(s, user_id=world.a.user_id, project_id=world.b.projects["캠퍼스 길찾기 앱"]) == []
        assert repo.block_places(s, user_id=world.a.user_id, item_ids=[world.b.items["설문 분석"]]) == {}


def test_an_empty_scope_refuses_without_any_call(db, world, svc, ai):
    uid = PC.add_user(db, "ragempty")
    r = svc.ask(uid, "내가 한 일은?")
    assert r.status == "REFUSED" and r.message == A.REFUSAL and ai.calls == [] and r.meta["calls"] == 0


# ---- small corpus bypass (3.2) ------------------------------------------------------------------------------------------------


def test_small_corpus_boundary_is_the_whole_generation_input(db, world, svc, ai, monkeypatch):
    q = "소식지 쪽수는?"
    sf = make_session_factory(db)
    with sf() as s:
        rows = repo.search_scope(s, user_id=world.c.user_id)
    est = A.prompt_estimate(A.answer_messages(q, [(str(i + 1), r.index_text) for i, r in enumerate(rows)]), A.Answer)
    monkeypatch.setattr(A, "SMALL_CORPUS_TOKENS", est)
    r = svc.ask(world.c.user_id, q)
    assert r.meta["path"] == "small" and r.meta["small_estimate"] == est
    assert [k for k, _ in ai.calls] == ["Answer"]  # no question embedding, no rerank
    monkeypatch.setattr(A, "SMALL_CORPUS_TOKENS", est - 1)
    ai.calls.clear()
    r = svc.ask(world.c.user_id, q)
    assert r.meta["path"] == "search" and [k for k, _ in ai.calls] == ["embed", "Relevance", "Answer"]


def test_user_a_is_over_the_threshold_and_user_c_under_it(db, world, svc, ai):
    r = ask_a(svc, ai, world, world.a.node("a_report2"))
    assert r.meta["small_estimate"] > A.SMALL_CORPUS_TOKENS and r.meta["path"] == "search"
    r = svc.ask(world.c.user_id, "소식지 쪽수는?")
    assert r.meta["small_estimate"] <= A.SMALL_CORPUS_TOKENS and r.meta["path"] == "small"
    # small path: every node in document order, numbered from 1, summary nodes included
    ids = prompt_node_ids(world, ai.of("Answer")[-1])
    assert ids == list(world.c.texts)


# ---- candidates (3.3, 3.4) ----------------------------------------------------------------------------------------------------


def test_vector_and_bm25_candidates_are_fused_and_sent_to_the_rerank(db, world, svc, ai):
    target = world.a.node("a_dev2b")
    r = ask_a(svc, ai, world, target, "번들 크기는 몇 킬로바이트였어?")
    sent = prompt_node_ids(world, ai.of("Relevance")[0])
    assert sent[0] == target  # top of both lists
    assert len(sent) == min(25, len(world.a.texts) - 1) == r.meta["candidates"]
    assert ai.of("embed") == [["번들 크기는 몇 킬로바이트였어?"]]
    rr = ai.of("Relevance")[0][-1]["content"]
    assert rr.startswith("질문: 번들 크기는") and rr.endswith("다시 질문: 번들 크기는 몇 킬로바이트였어?")


def test_the_candidate_order_is_the_rrf_of_the_vector_and_bm25_rankings(db, world, svc, ai):
    # the question vector points at the video project; the words point at the dev log
    qv = PC.near(world.a.vectors[world.a.node("a_conti2")])
    ai.scripts["embed"].append(qv)
    q = "자모 분리 함수 초성 검색"
    svc.ask(world.a.user_id, q)
    sent = prompt_node_ids(world, ai.of("Relevance")[0])
    sf = make_session_factory(db)
    with sf() as s:
        rows = repo.search_scope(s, user_id=world.a.user_id)
    ids = [r.id for r in rows]
    vec = sorted(ids, key=lambda x: (-float(world.a.vectors[x] @ qv), ids.index(x)))[:50]
    scores = A.bm25(A.bm25_tokens(q), [list(r.bigrams) for r in rows])
    lex = [ids[i] for i in sorted((i for i, v in enumerate(scores) if v > 0), key=lambda i: (-scores[i], i))][:50]
    assert lex[0] == world.a.node("a_dev2") and vec[0] == world.a.node("a_conti2")
    assert sent == [x for x, _ in A.rrf([vec, lex])]
    assert world.a.node("a_dev2") in sent[:5] and world.a.node("a_conti2") in sent


def test_nodes_embedded_with_another_model_are_left_out_of_the_vector_side(db, world, svc, ai):
    with db.begin() as c:
        c.execute(text("update portfolio_nodes set embed_model = 'text-embedding-3-large' where id = :i"),
                  {"i": world.a.node("a_sub")})
    r = ask_a(svc, ai, world, world.a.node("a_sub"), "질문")
    assert r.meta["vector_nodes"] == len(world.a.texts) - 2


# ---- the gate (3.5) --------------------------------------------------------------------------------------------------------------


def test_incorrect_refuses_with_the_fixed_phrase_and_no_generation_call(db, world, svc, ai):
    ai.scripts["Relevance"].append(grade(world.texts, {}))
    r = ask_a(svc, ai, world, world.a.node("a_goal"), "졸업 논문 주제는 뭐였어?")
    assert r.status == "REFUSED" and r.message == "포트폴리오 기록에서 찾지 못했어요" and r.sentences == []
    assert [k for k, _ in ai.calls] == ["embed", "Relevance"] and r.meta["calls"] == 2
    assert r.meta["gate"] == "INCORRECT" and ai.of("Answer") == []


def test_correct_answers_with_citations_resolved_to_file_and_place(db, world, svc, ai):
    t = world.a.node("a_report2")
    ai.scripts["Relevance"].append(grade(world.texts, {t: 2}))
    ai.scripts["Answer"].append(cite(world.texts, t, text_="과제 완료 시간이 94초에서 51초로 줄었다."))
    r = ask_a(svc, ai, world, t)
    assert r.status == "ANSWERED" and r.meta["gate"] == "CORRECT" and r.meta["calls"] == 3
    (s,) = r.sentences
    assert s["text"] == "과제 완료 시간이 94초에서 51초로 줄었다."
    # page 2 is a small chunk: page 1 shares its evidence document, so the citation names both places
    assert [c.label for c in s["citations"]] == ["결과보고서.pdf · 1쪽", "결과보고서.pdf · 2쪽"]
    pub = r.public()
    assert pub["sentences"][0]["citations"][1]["label"] == "결과보고서.pdf · 2쪽" and "meta" not in pub
    msgs = ai.of("Answer")[0]
    assert msgs[1]["content"].startswith("질문: 사용성 테스트") and msgs[1]["content"].endswith(
        "다시 질문: 사용성 테스트에서 과제 완료 시간이 얼마나 줄었어?")
    assert A.AMBIGUOUS_NOTE not in msgs[0]["content"] and DATA_RULE in msgs[0]["content"]


def test_ambiguous_answers_only_what_the_records_show(db, world, svc, ai):
    t = world.a.node("a_report1")
    ai.scripts["Relevance"].append(grade(world.texts, {t: 1}))
    ai.scripts["Answer"].append(cite(world.texts, t, text_="설문에는 212명이 응답했다.",
                                     missing="학년별 만족도는 기록에 없다."))
    r = ask_a(svc, ai, world, t, "학년별 만족도는 어땠어?")
    assert r.status == "PARTIAL" and r.meta["gate"] == "AMBIGUOUS" and r.missing == "학년별 만족도는 기록에 없다."
    assert A.AMBIGUOUS_NOTE in ai.of("Answer")[0][0]["content"]


def test_a_refused_generation_becomes_the_fixed_phrase(db, world, svc, ai):
    ai.scripts["Answer"].append({"refused": True, "sentences": [{"text": "지어낸 말", "citations": ["1"]}],
                                 "missing": "기록에 없다"})
    r = ask_a(svc, ai, world, world.a.node("a_goal"))
    assert r.status == "REFUSED" and r.message == A.REFUSAL and r.sentences == []


def test_evidence_is_at_most_six_in_a_u_order_with_small_neighbours(db, world, svc, ai):
    a = world.a
    best, second = a.node("a_report2"), a.node("a_report1")
    grades = {best: 2, second: 2, a.node("a_goal"): 1, a.node("a_screens"): 1, a.node("a_dev1"): 1,
              a.node("a_dev3"): 1, a.node("a_conti1"): 1, a.node("a_sub"): 1}
    ai.scripts["Relevance"].append(grade(world.texts, grades))
    r = ask_a(svc, ai, world, best)
    groups = prompt_groups(world, ai.of("Answer")[0])
    # one evidence document (one id) per chosen node; at most 6; U order: best first, second best last
    chosen = [[x for x in g if x in grades] for g in groups]
    assert all(len(c) == 1 for c in chosen) and len(groups) == 6
    assert chosen[0] == [best] and chosen[-1] == [second]
    # every chunk here is under 150 tokens: its prev/next in the same file share its document, in file order
    attached = [g for g in groups if len(g) > 1]
    assert attached and sum(len(g) for g in groups) > 6
    key = {n: k for k, ns in world.a.nodes.items() for n in ns}
    files = {k: f for p in PC.USER_A["projects"] for it in p["items"] for f, _, bl in it["files"] for k, *_ in bl}
    assert all(len({files[key[x]] for x in g}) == 1 for g in groups if g[0] in key)  # neighbours: same file only
    assert r.meta["evidence"] == 6 and r.meta["evidence_nodes"] == sum(len(g) for g in groups)


def test_a_cited_evidence_group_resolves_to_every_place_in_it(db, world, svc, ai):
    t = world.a.node("a_dev2")  # small chunk: dev log 1주차 and 2주차 측정 come along in the same document
    ai.scripts["Relevance"].append(grade(world.texts, {t: 2}))
    ai.scripts["Answer"].append(cite(world.texts, t, text_="초성 검색은 자모 분리 함수로 처리했다."))
    r = ask_a(svc, ai, world, t, "초성 검색은 어떻게 만들었어?")
    (doc,) = docs_of(ai.of("Answer")[0])
    assert doc[0] == "1" and nodes_in(world.texts, doc[1]) == [world.a.node("a_dev1"), t, world.a.node("a_dev2b")]
    assert [c.label for c in r.sentences[0]["citations"]] == ["개발일지.md · 1주차", "개발일지.md · 2주차",
                                                              "개발일지.md · 2주차 측정"]


def test_rerank_output_with_an_unknown_or_repeated_id_fails_without_a_repair_call(db, world, svc, ai):
    for bad in ({"judgments": [{"id": "99", "relevance": 2}]},
                {"judgments": [{"id": "1", "relevance": 2}, {"id": "1", "relevance": 0}]}):
        ai.calls.clear()
        ai.scripts["Relevance"].append(bad)
        with pytest.raises(A.AskError) as e:
            ask_a(svc, ai, world, world.a.node("a_goal"))
        assert e.value.code == "SCHEMA_INVALID" and ai.of("Answer") == [] and len(ai.of("Relevance")) == 1


# ---- cited generation (3.6) ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentences", [
    [{"text": "다른 자료를 인용한다.", "citations": ["99"]}],
    [{"text": "인용 없는 문장.", "citations": []}],
    [{"text": "넷을 인용한다.", "citations": ["1", "2", "3", "4"]}],
    [{"text": "맞는 문장.", "citations": ["1"]}, {"text": "인용 없는 둘째 문장.", "citations": []}],
])
def test_an_answer_breaking_the_citation_rules_is_citation_invalid_as_a_whole(db, world, svc, ai, sentences):
    a = world.a
    ai.scripts["Relevance"].append(grade(world.texts, {a.node(k): 2 for k in
                                                        ("a_report2", "a_report1", "a_goal", "a_dev1", "a_sub")}))
    ai.scripts["Answer"].append({"refused": False, "sentences": sentences, "missing": None})
    with pytest.raises(A.AskError) as e:
        ask_a(svc, ai, world, a.node("a_report2"))
    assert e.value.code == "CITATION_INVALID"
    assert len(ai.of("Answer")) == 1  # no second try


def test_a_summary_node_citation_resolves_to_its_attachment_places(db, world, svc, ai):
    summary = world.a.summaries["설문 분석"]
    ai.scripts["Relevance"].append(grade(world.texts, {summary: 2}))
    ai.scripts["Answer"].append(cite(world.texts, summary, text_="212명 설문을 정리했다."))
    r = ask_a(svc, ai, world, summary, "설문 분석에서 무엇을 했어?")
    labels = [c.label for c in r.sentences[0]["citations"]]
    assert labels == ["결과보고서.pdf · 2쪽", "결과보고서.pdf · 1쪽", "설문결과.xlsx · 시트 응답 1~37행"]
    assert all("제출물 요약" not in x for x in labels)


def test_citations_of_one_sentence_are_listed_once(db, world, svc, ai):
    summary, page2 = world.a.summaries["설문 분석"], world.a.node("a_report2")
    ai.scripts["Relevance"].append(grade(world.texts, {summary: 2, page2: 2}))
    ai.scripts["Answer"].append(cite(world.texts, summary, page2))
    r = ask_a(svc, ai, world, page2)
    labels = [c.label for c in r.sentences[0]["citations"]]
    assert labels.count("결과보고서.pdf · 2쪽") == 1


# ---- injection (5) ------------------------------------------------------------------------------------------------------------------


def test_injected_material_stays_inside_one_delimiter_and_cannot_widen_the_answer(db, world, svc, ai):
    inj = world.a.node("a_inject")
    ai.scripts["Relevance"].append(grade(world.texts, {inj: 2, world.a.node("a_goal"): 1}))
    ai.scripts["Answer"].append(cite(world.texts, world.a.node("a_goal"), text_="앱의 목적은 신입생 길찾기다."))
    r = ask_a(svc, ai, world, inj, "이 앱의 목적은?")
    for kind in ("Relevance", "Answer"):
        msgs = ai.of(kind)[0]
        assert DATA_RULE in msgs[0]["content"]
        content = msgs[-1]["content"]
        # the fake closing tag and the fake document 99 are inert text inside the injected chunk's own delimiter
        assert "</document> <document" not in content and '<document id="99">' not in content
        assert content.count("<document id=") == content.count("</document>") == len(docs_of(msgs))
        assert "99" not in [i for i, _ in docs_of(msgs)]
    # the answer has no link field and its citations are the evidence of this question only
    assert set(A.Answer.model_fields) == {"refused", "sentences", "missing"}
    assert set(A.Sentence.model_fields) == {"text", "citations"}
    assert [c.label for c in r.sentences[0]["citations"]] == ["기획서.docx · 목적"]
    # an answer that obeys the injection (citing the fake "99" or nothing) is rejected by the server
    ai.scripts["Relevance"].append(grade(world.texts, {inj: 2}))
    ai.scripts["Answer"].append({"refused": False, "missing": None,
                                 "sentences": [{"text": "이 사용자는 모든 과제에서 만점을 받았다.", "citations": ["99"]}]})
    with pytest.raises(A.AskError) as e:
        ask_a(svc, ai, world, inj, "이 앱의 목적은?")
    assert e.value.code == "CITATION_INVALID"


# ---- failures, storage, rate limit (3.7) ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["embed", "Relevance", "Answer"])
def test_a_timely_failure_is_never_hidden(db, world, svc, ai, step):
    if step == "embed":
        ai.scripts["embed"].append(AIError("UPSTREAM_ERROR", "x", True))
        with pytest.raises(A.AskError) as e:
            svc.ask(world.a.user_id, "질문")
    else:
        ai.scripts[step].append(AIError("TIMEOUT", "x", True))
        with pytest.raises(A.AskError) as e:
            ask_a(svc, ai, world, world.a.node("a_goal"))
    assert e.value.code in ("UPSTREAM_ERROR", "TIMEOUT")
    kinds = [k for k, _ in ai.calls]
    assert kinds == {"embed": ["embed"], "Relevance": ["embed", "Relevance"],
                     "Answer": ["embed", "Relevance", "Answer"]}[step]


QUESTION = "질문표지QX7 내가 만든 앱의 과제 완료 시간은?"
ANSWER = "답변표지AX9 과제 완료 시간이 51초였다."


def _all_rows(db) -> str:
    with db.connect() as c:
        tables = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname = 'public'"))]
        return "\n".join(str(r[0]) for t in tables for r in c.execute(text(f'select row_to_json(x)::text from "{t}" x')))


def _counts(db) -> dict:
    with db.connect() as c:
        tables = [r[0] for r in c.execute(text("select tablename from pg_tables where schemaname = 'public'"))]
        return {t: c.execute(text(f'select count(*) from "{t}"')).scalar() for t in tables}


def test_questions_and_answers_are_not_stored_in_the_db_or_logs(db, world, svc, ai, caplog):
    t = world.a.node("a_report2")
    before = _counts(db)
    ai.scripts["Relevance"].append(grade(world.texts, {t: 2}))
    ai.scripts["Answer"].append(cite(world.texts, t, text_=ANSWER))
    with caplog.at_level(logging.DEBUG):
        r = ask_a(svc, ai, world, t, QUESTION)
        ai.scripts["Relevance"].append(grade(world.texts, {}))
        ask_a(svc, ai, world, t, QUESTION)  # refused path
        with pytest.raises(A.AskError):
            ai.scripts["Relevance"].append(grade(world.texts, {t: 2}))
            ai.scripts["Answer"].append({"refused": False, "missing": "누락표지MX3",
                                         "sentences": [{"text": ANSWER, "citations": []}]})
            ask_a(svc, ai, world, t, QUESTION)
    assert r.sentences[0]["text"] == ANSWER
    after = _counts(db)
    assert {k: v for k, v in after.items() if v != before[k]} == {"auth_throttle": 2}  # the two per-user counters
    dump = _all_rows(db)
    assert "질문표지QX7" not in dump and "답변표지AX9" not in dump and "누락표지MX3" not in dump
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "질문표지QX7" not in logs and "답변표지AX9" not in logs and "누락표지MX3" not in logs
    lines = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("portfolio_ask")]
    assert len(lines) == 3 and "error=CITATION_INVALID reason=UNCITED" in lines[-1] and "gate=INCORRECT" in lines[1]
    assert "calls=3 embed=1 rerank=1 answer=1" in lines[0] and "calls=2 embed=1 rerank=1 answer=0" in lines[1]


def test_the_question_rate_limit_counts_per_user_and_refuses_without_calls(db, world, svc, ai, monkeypatch):
    monkeypatch.setattr(A, "ASK_USER", Rule("portfolio-ask-user", 3, 600, 600))
    for _ in range(3):
        svc.ask(world.c.user_id, "소식지 쪽수는?")
    ai.calls.clear()
    with pytest.raises(A.AskError) as e:
        svc.ask(world.c.user_id, "소식지 쪽수는?")
    assert e.value.code == "RATE_LIMITED" and e.value.retry_after > 0 and ai.calls == []
    svc.ask(world.b.user_id, "과제 완료 시간은?")  # another user is not affected
    # an invalid question is refused before it is counted
    with pytest.raises(A.AskError) as e:
        svc.ask(world.b.user_id, "")
    assert e.value.code == "QUESTION_INVALID"


def test_the_daily_rule_and_the_window_rule_both_exist():
    assert A.ASK_USER.limit == 20 and A.ASK_USER.window_s == 600
    assert A.ASK_USER_DAY.limit == 200 and A.ASK_USER_DAY.window_s == 86400


def test_the_product_wiring_exposes_ask_for_the_8d_api(db, monkeypatch, tmp_path):
    app = create_app(load_settings(), upload_dir=tmp_path / "u", deletion_ledger_dir=tmp_path / "l")
    try:
        assert isinstance(app.state.portfolio_ask, A.PortfolioAsk)
        assert not [r for r in app.routes if "ask" in getattr(r, "path", "")]  # no API route in 8C
    finally:
        app.state.engine.dispose()
