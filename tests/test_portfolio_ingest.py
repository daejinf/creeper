"""Stage 8A deterministic contract: portfolio data model, ingest trigger, attachment extraction and chunking (D-I1).

No Timely call anywhere. The DESCRIBING / UNDERSTANDING / EMBEDDING stages are FIXTURE handlers (`FakeStages`), never
LIVE; without them a job stops at DESCRIBING as FAILED(STAGE_UNAVAILABLE), which is what the product does until 8B.
FastAPI background tasks run before the test client returns, so a job has finished when the request returns.
"""
from __future__ import annotations

import inspect
import io
import json
import random
import re
import struct
import uuid
import zipfile
from datetime import timedelta
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from alembic import command
from alembic.config import Config
from docx import Document
from sqlalchemy import text

from moim.account.ledger import DeletionLedger
from moim.config import load_settings
from moim.execution import files as F
from moim.main import create_app
from moim.portfolio import chunk as C
from moim.portfolio import extract as X
from moim.portfolio import ingest, repo
from moim.portfolio.service import StageError, where
from moim.portfolio.tokens import bm25_tokens, estimate
from portfolio_fixtures import EN_PROSE, KO_PROSE, MIXED, OTHER_MEMBER_TEXT, PRIVATE_CHAT, PRIVATE_STATE
from test_execution import (  # noqa: F401
    INTERVIEW_ANSWER, ExecAI, confirmed_team, docx, ex, hwp, hwpx, jpost, mine, png, review_for, submit, with_member,
    zip_doc,
)
from test_groups import db, fake, grp_db, scalar, user_row  # noqa: F401
from test_role_assignment import ROOT, SECRET_CONSTRAINT, rows, seed  # noqa: F401

PORTFOLIO_TABLES = ("portfolio_projects", "portfolio_items", "portfolio_documents", "portfolio_nodes", "portfolio_jobs",
                    "portfolio_summaries")
EMPTY = {"projects": 0, "items": 0, "documents": 0, "nodes": 0, "jobs": 0, "summaries": 0}


# ---- file fixtures --------------------------------------------------------------------------------------------------


def pdf_doc(pages: list[str]) -> bytes:
    doc = fitz.open()
    for body in pages:
        page = doc.new_page()
        if body:
            page.insert_textbox(fitz.Rect(50, 50, 550, 800), body, fontname="korea", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def docx_doc() -> bytes:
    d = Document()
    d.add_heading("1. 개요", level=1)
    d.add_paragraph(KO_PROSE[0])
    d.add_heading("2. 결과", level=1)
    d.add_paragraph(KO_PROSE[2])
    t = d.add_table(rows=3, cols=2)
    for r, (k, v) in enumerate([("항목", "값"), ("응답 수", "120"), ("만족도", "4.2")]):
        t.cell(r, 0).text, t.cell(r, 1).text = k, v
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def pptx_doc(picture=True) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "일정 앱 발표"
    s1.placeholders[1].text = "문제 정의와 해결 방법을 소개합니다."
    s1.notes_slide.notes_text_frame.text = "발표 노트: 인터뷰 다섯 건"
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "설문 결과"
    tbl = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    tbl.cell(0, 0).text, tbl.cell(0, 1).text, tbl.cell(1, 0).text, tbl.cell(1, 1).text = "항목", "값", "응답", "120"
    if picture:
        s2.shapes.add_picture(io.BytesIO(png(60, 40)), Inches(5), Inches(2))
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def xlsx_doc(rows_n=5, sheets=("매출",)) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name in sheets:
        ws = wb.create_sheet(name)
        ws.append(["월", "매출", "비고"])
        for i in range(rows_n):
            ws.append([f"{i + 1}월", 1000 + i, "정상"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def md_doc() -> bytes:
    return ("# 회고\n\n" + KO_PROSE[3] + "\n\n## 배운 점\n\n" + KO_PROSE[5] + "\n").encode()


def blob(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ---- unit: tokens -----------------------------------------------------------------------------------------------------


def test_bm25_tokens_are_hangul_bigrams_and_lowercase_words():
    assert bm25_tokens("React로 만든 일정앱, API 120ms!") == ["react", "로", "만든", "일정", "정앱", "api", "120ms"]
    assert bm25_tokens("가") == ["가"] and bm25_tokens("  ,.  ") == [] and bm25_tokens("") == []
    assert bm25_tokens("Figma와 피그마") == ["figma", "와", "피그", "그마"]


def test_korean_chars_per_token_measurement():
    """RAG_DESIGN 2.3/9: measure cl100k on the fixtures; the runtime estimate must never be below the real count."""
    tiktoken = pytest.importorskip("tiktoken")
    try:  # the BPE file is downloaded on first use (then cached); a clean offline machine cannot measure
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # noqa: BLE001 - network, cache or proxy errors all mean "cannot measure here"
        pytest.skip(f"cannot load cl100k_base (no network or cache): {type(exc).__name__}")
    ko_chars = sum(len(s) for s in KO_PROSE)
    ko_tokens = sum(len(enc.encode(s)) for s in KO_PROSE)
    ko_hangul = sum(len(re.findall("[가-힣]", s)) for s in KO_PROSE)
    ratio = ko_chars / ko_tokens
    print(f"\nKO chars/token={ratio:.3f} tokens/hangul={ko_tokens / ko_hangul:.3f} (chars={ko_chars}, tokens={ko_tokens})")
    assert 0.8 <= ratio <= 1.3  # recorded value 1.03 (2026-11-23)
    for s in KO_PROSE + EN_PROSE + MIXED:
        real = len(enc.encode(s))
        assert real <= estimate(s) <= real * 1.4, (real, estimate(s))


# ---- unit: extraction ---------------------------------------------------------------------------------------------------


def test_pdf_pages_become_blocks_and_figure_pages_are_vision_targets():
    ex = X.extract_file("d1", "보고서.pdf", pdf_doc([KO_PROSE[0], "", "그림 1"]))
    assert ex.status == "EXTRACTED" and ex.kind == "PDF" and ex.truncated_reason is None
    kinds = [(b["kind"], b["locator"].get("page"), b["vision"]) for b in ex.blocks]
    assert kinds == [("text", 1, False), ("image", 2, True), ("image", 3, True)]
    assert "일정 관리" in ex.blocks[0]["text"] and ex.blocks[2]["text"] == "그림 1"
    assert all(b["locator"]["file"] == "보고서.pdf" and b["block_id"].startswith("d1b") for b in ex.blocks)


def test_docx_headings_are_sections_and_tables_are_tsv():
    ex = X.extract_file("d2", "결과.docx", docx_doc())
    assert [(b["kind"], b["locator"].get("section")) for b in ex.blocks] == [
        ("text", "1. 개요"), ("text", "2. 결과"), ("table", "2. 결과")]
    assert ex.blocks[2]["text"] == "항목\t값\n응답 수\t120\n만족도\t4.2" and ex.blocks[2]["locator"]["table"] == 1


def test_markdown_headings_and_plain_text():
    ex = X.extract_file("d1", "회고.md", md_doc())
    assert [b["locator"].get("section") for b in ex.blocks] == ["회고", "배운 점"]
    t = X.extract_file("d1", "메모.txt", KO_PROSE[1].encode())
    assert len(t.blocks) == 1 and t.blocks[0]["kind"] == "text" and "section" not in t.blocks[0]["locator"]


def test_pptx_slides_keep_text_table_notes_and_mark_inserted_images():
    ex = X.extract_file("d1", "발표.pptx", pptx_doc())
    assert ex.status == "EXTRACTED"
    slides = [b for b in ex.blocks if b["kind"] == "slide"]
    assert [b["locator"]["slide"] for b in slides] == [1, 2]
    assert "일정 앱 발표" in slides[0]["text"] and "[노트] 발표 노트" in slides[0]["text"]
    assert "항목\t값\n응답\t120" in slides[1]["text"]
    imgs = [b for b in ex.blocks if b["kind"] == "image"]
    assert len(imgs) == 1 and imgs[0]["vision"] and imgs[0]["locator"]["slide"] == 2 and imgs[0]["locator"]["image"] == 1


def test_xlsx_sheets_with_header_rows_and_a_recorded_row_cut():
    ex = X.extract_file("d1", "데이터.xlsx", xlsx_doc(rows_n=3, sheets=("매출", "비용")))
    assert [(b["kind"], b["locator"]["sheet"], b["locator"]["rows"]) for b in ex.blocks] == [
        ("table", "매출", [1, 4]), ("table", "비용", [1, 4])]
    assert ex.blocks[0]["text"].split("\n")[0] == "월\t매출\t비고" and ex.truncated_reason is None
    big = X.extract_file("d1", "큰표.xlsx", xlsx_doc(rows_n=X.MAX_ROWS + 20))
    assert big.truncated_reason == "ROW_LIMIT" and len(big.blocks[0]["text"].split("\n")) == X.MAX_ROWS


def test_images_and_hwp_files():
    im = X.extract_file("d1", "화면.png", png(40, 30))
    assert im.blocks == [{"block_id": "d1b1", "kind": "image", "locator": {"file": "화면.png"}, "text": "", "vision": True}]
    for name, data in (("보고서.hwp", hwp()), ("보고서.hwpx", hwpx())):
        h = X.extract_file("d1", name, data)
        assert h.status == "NAME_ONLY" and h.blocks == [] and h.error_code is None


def test_bombs_disguised_and_broken_files_are_rejected_by_the_upload_checks():
    bomb = zip_doc({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"0" * (5 * 1024 * 1024)})
    assert X.extract_file("d1", "폭탄.docx", bomb).error_code == "ARCHIVE_LIMIT"
    assert X.extract_file("d1", "가짜.pdf", png(10, 10)).error_code == "MISMATCH"
    assert X.extract_file("d1", "가짜.xlsx", docx()).error_code == "MISMATCH"
    assert X.extract_file("d1", "깨짐.pdf", b"%PDF-1.4 broken").error_code == "CORRUPT"
    macro = with_member(xlsx_doc(), "xl/vbaProject.bin", b"x")
    assert X.extract_file("d1", "매크로.xlsx", macro).error_code == "MACRO"
    bad = zip_doc({"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>"})  # passes the check
    r = X.extract_file("d1", "빈.pptx", bad)
    assert r.status == "REJECTED" and r.error_code == "PARSE_FAILED" and r.blocks == []


def test_the_per_file_character_cap_is_recorded():
    long = ("가나다라마바사. " * 30000).encode()
    ex = X.extract_file("d1", "긴글.txt", long, cap=1000)
    assert ex.truncated_reason == "CHAR_LIMIT" and ex.char_count <= 1000


# ---- unit: chunking -----------------------------------------------------------------------------------------------------


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_chunks_keep_sentences_whole_never_overlap_and_stay_under_the_cap():
    body = "\n\n".join(KO_PROSE * 4)
    blocks = [{"block_id": "d1b1", "kind": "text", "locator": {"file": "a.txt"}, "text": body, "vision": False}]
    chunks = C.chunk_blocks(blocks)
    assert len(chunks) > 3
    assert all(c.tokens <= C.MAX and not c.forced for c in chunks)
    assert sum(c.tokens for c in chunks) / len(chunks) >= C.MIN
    # overlap 0: the chunks are the block, in order, exactly once
    assert "".join(_squash(c.text) for c in chunks) == _squash(body)
    sents = {s for p in body.split("\n\n") for s in C.sentences(p)}
    for c in chunks:  # every chunk is a run of whole sentences
        for s in re.split(r"(?<=[.!?。！？…])\s+", c.text):
            assert s.strip() in sents
    assert [c.locator["part"] for c in chunks] == list(range(1, len(chunks) + 1))


def test_small_tables_and_slides_stay_whole_and_big_tables_split_between_rows():
    table = "\n".join(["월\t매출\t비고"] + [f"{i}월\t{1000 + i}\t정상 운영 기간" for i in range(8)])
    slide = "제목\n" + KO_PROSE[0]
    chunks = C.chunk_blocks([
        {"block_id": "b1", "kind": "table", "locator": {"file": "x"}, "text": table, "vision": False},
        {"block_id": "b2", "kind": "slide", "locator": {"file": "x", "slide": 1}, "text": slide, "vision": False},
        {"block_id": "b3", "kind": "image", "locator": {"file": "x"}, "text": "", "vision": True},
    ])
    assert [c.text for c in chunks] == [table, slide] and "part" not in chunks[0].locator
    big = "\n".join(["월\t매출\t비고"] + [f"{i}월\t{100000 + i}\t정상 운영 기간이었고 특이사항 없음" for i in range(120)])
    parts = C.chunk_blocks([{"block_id": "b1", "kind": "table", "locator": {"file": "x"}, "text": big, "vision": False}])
    assert len(parts) > 1 and all(p.tokens <= C.MAX for p in parts)
    assert parts[0].header is None and all(p.header == "월\t매출\t비고" for p in parts[1:])
    assert "\n".join(p.text for p in parts) == big  # rows exactly once; the header is not repeated in the texts


def test_only_a_single_oversized_sentence_is_force_split():
    long_sentence = "가" * 2000
    parts = C.chunk_blocks([{"block_id": "b", "kind": "text", "locator": {}, "text": long_sentence, "vision": False}])
    assert len(parts) > 1 and all(p.forced and p.tokens <= C.MAX for p in parts)
    assert "".join(p.text for p in parts) == long_sentence


def test_locator_labels():
    assert where({"page": 3}) == "3쪽" and where({"slide": 2, "image": 1}) == "슬라이드 2 이미지 1"
    assert where({"sheet": "매출", "rows": [1, 40], "part": 2}) == "시트 매출 1~40행 (2)"
    assert where({"section": "2. 결과"}) == "2. 결과" and where({}) == "본문"


# ---- repository scope ---------------------------------------------------------------------------------------------------


def test_every_repository_function_requires_the_owner_id():
    fns = [f for n, f in inspect.getmembers(repo, inspect.isfunction)
           if f.__module__ == repo.__name__ and not n.startswith("_")]
    assert len(fns) >= 15
    for f in fns:
        p = inspect.signature(f).parameters.get("user_id")
        assert p is not None and p.kind is p.KEYWORD_ONLY and p.default is p.empty, f.__name__
        with pytest.raises(TypeError):
            f(None)  # no owner given
        for bad in (None, "", "not-a-uuid", "' or 1=1 --"):
            with pytest.raises(repo.ScopeError):
                f(None, user_id=bad, **{k: None for k in inspect.signature(f).parameters
                                         if k not in ("s", "user_id") and
                                         inspect.signature(f).parameters[k].default is inspect.Parameter.empty})


# ---- app fixtures -----------------------------------------------------------------------------------------------------


class FakeStages:
    """FIXTURE handlers for the 8B stages (never LIVE): they only write through `repo` with the job's owner."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, str] = {}

    def _step(self, stage, inp):
        self.calls.append((stage, inp.job_id))
        if stage in self.fail:
            raise StageError(self.fail.pop(stage))

    def describing(self, inp):
        self._step("DESCRIBING", inp)
        return {"described": 0}

    def understanding(self, inp):
        self._step("UNDERSTANDING", inp)
        with inp.session_factory.begin() as s:
            for n in repo.list_nodes(s, user_id=inp.user_id, item_id=inp.item_id):
                repo.update_node(s, user_id=inp.user_id, node_id=n.id, context="맥락")
        return {}

    def embedding(self, inp):
        self._step("EMBEDDING", inp)
        with inp.session_factory.begin() as s:
            for n in repo.list_nodes(s, user_id=inp.user_id, item_id=inp.item_id):
                repo.update_node(s, user_id=inp.user_id, node_id=n.id, embedding=struct.pack("<4f", 1, 0, 0, 0),
                                 embed_model="fixture", embed_dim=4)
        return {}

    def handlers(self):
        return {"DESCRIBING": self.describing, "UNDERSTANDING": self.understanding, "EMBEDDING": self.embedding}


@pytest.fixture()
def ai():
    return ExecAI()


@pytest.fixture()
def stages():
    return FakeStages()


@pytest.fixture()
def uploads(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture()
def ledger_dir(tmp_path):
    d = tmp_path / "ledger"
    d.mkdir()
    return d


@pytest.fixture()
def app(db, fake, ai, seed, uploads, ledger_dir, stages):
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
                             rng_factory=lambda: random.Random(seed["value"]), upload_dir=uploads,
                             deletion_ledger_dir=ledger_dir, portfolio_stages=stages.handlers())
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def mk(db, app):
    return lambda username, display=None: user_row(db, app, username, display)


def hold(ai):
    ai.scripts["CompletionReview"].append(lambda body: review_for(body, "NOT_MET", "보완이 필요해요"))


def vote(u, gid, rid, agree):
    return jpost(u, f"/api/groups/{gid}/records/{rid}/vote", {"agree": agree})


def jobs(db, user_id=None):
    q = "select * from portfolio_jobs" + (" where user_id=:u" if user_id else "") + " order by created_at"
    return rows(db, q, u=user_id)


def items(db, user_id=None):
    q = "select * from portfolio_items" + (" where user_id=:u" if user_id else "") + " order by created_at"
    return rows(db, q, u=user_id)


def counts(db, user_id):
    return {t.removeprefix("portfolio_"): scalar(db, f"select count(*) from {t} where user_id=:u", u=user_id)
            for t in PORTFOLIO_TABLES}


def all_portfolio(db, user_id) -> str:
    return blob([[r._asdict() for r in rows(db, f"select * from {t} where user_id=:u", u=user_id)] for t in PORTFOLIO_TABLES])


# ---- ingest trigger ---------------------------------------------------------------------------------------------------


def test_an_ai_pass_with_attachments_makes_exactly_one_job_that_runs_to_ready(db, app, mk, ai, stages):
    gid, (a, b) = confirmed_team(mk, 2)
    t = mine(a, gid)
    r = submit(a, gid, t, files=[("보고서.pdf", pdf_doc([KO_PROSE[0], ""])), ("결과.docx", docx_doc())],
               memo="최종 보고서를 올려요")
    assert r.status_code == 201 and "_portfolio_jobs" not in r.json()
    rec = rows(db, "select * from execution_records where id=:r", r=r.json()["id"])[0]
    assert (rec.completion, rec.completed_by) == ("DONE", "AI")
    js = jobs(db)
    assert len(js) == 1 and js[0].user_id == a.id and js[0].state == "READY" and js[0].revision == 1
    assert [s for s, _ in stages.calls] == ["DESCRIBING", "UNDERSTANDING", "EMBEDDING"]
    assert set(js[0].stage_meta) == {"EXTRACTING", "DESCRIBING", "UNDERSTANDING", "EMBEDDING"}
    assert js[0].stage_meta["EXTRACTING"]["documents"] == 2 and js[0].stage_meta["EXTRACTING"]["vision_targets"] == 1
    it = items(db)[0]
    todo = rows(db, "select title, due_date from todo_confirmed_items where task_id=:t", t=t["task_id"])[0]
    proj = rows(db, "select * from portfolio_projects")[0]
    brief = rows(db, "select name, content from project_briefs where group_id=:g order by revision desc", g=gid)[0]
    role = rows(db, "select role_name from role_assignment_members where group_id=:g and user_id=:u", g=gid, u=a.id)[0]
    assert (it.source_record_id, it.source_task_id, it.task_title, it.due_date) == (rec.id, t["task_id"], todo.title, todo.due_date)
    assert (it.completed_by, it.review_decision, it.memo, it.file_count) == ("AI", "PASS", "최종 보고서를 올려요", 2)
    assert (proj.name, proj.description, proj.role_name, proj.source_group_id) == (brief.name, brief.content, role.role_name, gid)
    docs = rows(db, "select * from portfolio_documents order by position")
    assert [(d.file_name, d.kind, d.status) for d in docs] == [("보고서.pdf", "PDF", "EXTRACTED"), ("결과.docx", "DOCX", "EXTRACTED")]
    nodes = rows(db, "select * from portfolio_nodes order by position")
    assert nodes and all(n.kind == "chunk" and n.context == "맥락" and n.embed_dim == 4 for n in nodes)
    assert nodes[0].index_text.startswith(f"[{brief.name} / {role.role_name} / {todo.title} / 보고서.pdf / 1쪽]")
    assert nodes[0].bigrams == bm25_tokens(nodes[0].index_text)
    by_doc: dict[str, list] = {}
    for n in nodes:
        by_doc.setdefault(n.document_id, []).append(n)
    for ns in by_doc.values():  # prev/next within one document
        assert ns[0].prev_id is None and ns[-1].next_id is None
        for x, y in zip(ns, ns[1:]):
            assert (x.next_id, y.prev_id) == (y.id, x.id)
    blocks = {b["block_id"] for d in docs for b in d.blocks}
    assert all(set(n.source_block_ids) <= blocks for n in nodes)


def test_team_self_and_manual_completions_also_collect_and_only_the_submitter_gets_a_job(db, app, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    hold(ai)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    assert jobs(db) == []  # VOTING: nothing yet
    v = vote(b, gid, rid, True)
    assert v.json()["completion"] == "VOTING" and "_portfolio_jobs" not in v.json()
    r = vote(c, gid, rid, True)
    assert r.json()["completion"] == "DONE" and "_portfolio_jobs" not in r.json()
    assert [(j.user_id, j.state) for j in jobs(db)] == [(a.id, "READY")]
    assert items(db)[0].completed_by == "TEAM" and items(db)[0].review_decision == "REVIEW_NEEDED"
    # SELF: everyone else left; an HWP the AI cannot read completes at once
    for u in (b, c):
        assert jpost(u, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200
    r = submit(a, gid, mine(a, gid, 1), files=[("보고서.hwp", hwp())], memo="한글 문서 메모")
    assert r.json()["completion"] == "DONE"
    it = [i for i in items(db) if i.completed_by == "SELF"][0]
    assert it.review_decision is None
    doc = rows(db, "select * from portfolio_documents where item_id=:i", i=it.id)[0]
    assert (doc.kind, doc.status, doc.blocks[0]["kind"]) == ("HWP", "NAME_ONLY", "meta")
    node = rows(db, "select * from portfolio_nodes where item_id=:i", i=it.id)[0]
    assert "보고서.hwp" in node.text and "한글 문서 메모" in node.text
    # MANUAL (a stage 5 checkbox record): collected through the same copy, only for the owner's own record
    with app.state.engine.begin() as conn:
        conn.execute(text("update execution_records set completed_by='MANUAL' where id=:r"), {"r": rid})
        conn.execute(text("delete from portfolio_items where source_record_id=:r"), {"r": rid})
    sf = app.state.portfolio._sf
    with sf.begin() as s:
        assert ingest.enqueue_record(s, user_id=b.id, record_id=rid, now=ingest_now()) is None  # not b's record
        job = ingest.enqueue_record(s, user_id=a.id, record_id=rid, now=ingest_now())
        assert job and ingest.take_new(s) == [(a.id, job)]
    app.state.portfolio.run(a.id, job)
    assert rows(db, "select completed_by from portfolio_items where source_record_id=:r", r=rid)[0].completed_by == "MANUAL"
    assert {j.user_id for j in jobs(db)} == {a.id}


def ingest_now():
    from moim.timeutil import utcnow

    return utcnow()


def test_a_completion_settled_by_a_leaving_member_starts_the_job_of_the_submitter(db, app, mk, ai):
    gid, (a, b, c) = confirmed_team(mk, 3)
    hold(ai)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    vote(b, gid, rid, True)
    assert jpost(c, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200  # b's vote is now a majority
    assert [(j.user_id, j.state) for j in jobs(db)] == [(a.id, "READY")]


def test_no_job_without_attachments_or_before_done(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), memo="메모만 공유")  # a note
    hold(ai)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]  # VOTING
    assert rows(db, "select completion from execution_records where id=:r", r=rid)[0].completion == "VOTING"
    assert vote(b, gid, rid, False).json()["completion"] == "REJECTED"
    hold(ai)
    rid2 = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    hold(ai)
    submit(a, gid, mine(a, gid), files=[("w.png", png())])  # replaces rid2's vote: WITHDRAWN
    assert rows(db, "select completion from execution_records where id=:r", r=rid2)[0].completion == "WITHDRAWN"
    assert jobs(db) == [] and items(db) == []
    # a record without attachments never collects, even when completed
    with app.state.portfolio._sf.begin() as s:
        fake_rec = SimpleNamespace(id=str(uuid.uuid4()), task_id=mine(a, gid)["task_id"], file_count=0, group_id=gid,
                                   user_id=a.id)
        assert ingest.on_done(s, fake_rec, "MANUAL", ingest_now()) is None
        assert ingest.take_new(s) == []


def test_undo_deletes_the_copy_and_a_repeated_completion_is_idempotent(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    rid = submit(a, gid, mine(a, gid), files=[("w.png", png())]).json()["id"]
    assert len(items(db)) == 1
    with app.state.portfolio._sf.begin() as s:
        assert ingest.enqueue_record(s, user_id=a.id, record_id=rid, now=ingest_now()) is None  # same record again
    assert len(items(db)) == 1 and len(jobs(db)) == 1
    r = jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": True})
    assert r.status_code == 200
    assert counts(db, a.id) == EMPTY


def test_private_data_and_hidden_criteria_never_reach_the_copy(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    with app.state.engine.begin() as conn:  # plant more private data of a: MemberState and a conversation turn
        n = conn.execute(text("update role_member_states set state = state || jsonb_build_object('note', cast(:x as text))"
                              " where user_id=:u"), {"x": PRIVATE_STATE, "u": a.id}).rowcount
        m = conn.execute(text("update role_interview_turns set user_message = :x where user_id=:u and user_message is not null"),
                         {"x": PRIVATE_CHAT, "u": a.id}).rowcount
    assert n >= 1 and m >= 1
    submit(b, gid, mine(b, gid), files=[("other.txt", OTHER_MEMBER_TEXT.encode())])  # the other member's attachment
    submit(a, gid, mine(a, gid), files=[("mine.txt", ("내 보고서 본문입니다. " + KO_PROSE[0]).encode())],
           memo="내 메모")
    crit = rows(db, "select deliverable, completion_criteria, evidence_types from todo_confirmed_items")
    reviews = rows(db, "select criteria_results, guidance from execution_reviews")
    dump = all_portfolio(db, a.id)
    assert "내 보고서 본문입니다" in dump and "내 메모" in dump
    forbidden = [OTHER_MEMBER_TEXT, PRIVATE_STATE, PRIVATE_CHAT, INTERVIEW_ANSWER, SECRET_CONSTRAINT]
    forbidden += [r.deliverable for r in crit] + [c for r in crit for c in r.completion_criteria]
    forbidden += [x["reason"] for r in reviews for x in (r.criteria_results or [])]
    forbidden += [r.guidance for r in reviews if r.guidance]
    for f in forbidden:
        assert f not in dump, f
    for word in ("deliverable", "completion_criteria", "evidence_types", "criteria_results", "guidance"):
        assert word not in dump
    # b's own copy holds only b's file
    bdump = blob([r._asdict() for r in rows(db, "select * from portfolio_nodes where user_id=:u", u=b.id)])
    assert OTHER_MEMBER_TEXT in bdump and "내 보고서" not in bdump


def test_extraction_of_mixed_formats_through_a_real_submission(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    files = [("발표.pptx", pptx_doc()), ("데이터.xlsx", xlsx_doc(rows_n=4)), ("회고.md", md_doc()),
             ("화면.png", png(50, 40)), ("문서.hwpx", hwpx())]
    rid = submit(a, gid, mine(a, gid), files=files).json()["id"]
    state = rows(db, "select completion from execution_records where id=:r", r=rid)[0].completion
    assert state in ("DONE", "VOTING")
    if state == "VOTING":
        vote(b, gid, rid, True)
    docs = rows(db, "select * from portfolio_documents order by position")
    assert [(d.kind, d.status) for d in docs] == [("PPTX", "EXTRACTED"), ("XLSX", "EXTRACTED"), ("MD", "EXTRACTED"),
                                                  ("PNG", "EXTRACTED"), ("HWPX", "NAME_ONLY")]
    ids = [b["block_id"] for d in docs for b in d.blocks]
    assert len(ids) == len(set(ids))
    locs = [n.locator for n in rows(db, "select locator from portfolio_nodes order by position")]
    assert {"file": "발표.pptx", "slide": 1} in locs and {"file": "데이터.xlsx", "sheet": "매출", "rows": [1, 5]} in locs
    assert {"file": "회고.md", "section": "배운 점"} in locs
    assert not any(l.get("file") == "화면.png" for l in locs)  # vision target: described in 8B, no chunk yet
    assert jobs(db)[0].state == "READY"


# ---- job state machine --------------------------------------------------------------------------------------------------


@pytest.fixture()
def plain_app(db, fake, ai, seed, uploads):
    """The product wiring (since 8B with the Timely stage handlers; `tests/test_portfolio_understanding.py`)."""
    application = create_app(load_settings(), goal_client_factory=lambda: fake, ai_client_factory=lambda: ai,
                             rng_factory=lambda: random.Random(seed["value"]), upload_dir=uploads)
    yield application
    application.state.engine.dispose()


def test_a_stage_without_a_handler_fails_honestly_after_extraction(db, plain_app, fake, ai):
    plain_app.state.portfolio._stages.pop("DESCRIBING")
    mk2 = lambda username, display=None: user_row(db, plain_app, username, display)  # noqa: E731
    gid, (a, b) = confirmed_team(mk2, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    j = jobs(db)[0]
    assert (j.state, j.failed_stage, j.error_code) == ("FAILED", "DESCRIBING", "STAGE_UNAVAILABLE")
    assert scalar(db, "select count(*) from portfolio_nodes") >= 1  # the extraction result was kept


def test_a_failed_stage_is_retried_from_that_stage_with_earlier_results_kept(db, app, mk, ai, stages):
    stages.fail["UNDERSTANDING"] = "SCHEMA_INVALID"
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    j = jobs(db)[0]
    assert (j.state, j.failed_stage, j.error_code) == ("FAILED", "UNDERSTANDING", "SCHEMA_INVALID")
    node_ids = [n.id for n in rows(db, "select id from portfolio_nodes")]
    svc = app.state.portfolio
    with pytest.raises(Exception) as e:
        svc.retry(b.id, j.id, j.revision)  # not b's job: indistinguishable from a missing one
    assert e.value.status == 404
    with pytest.raises(Exception) as e:
        svc.retry(a.id, j.id, j.revision + 5)
    assert e.value.status == 409
    out = svc.retry(a.id, j.id, j.revision)
    assert out == {"state": "QUEUED", "resume_stage": "UNDERSTANDING", "revision": 2}
    stages.calls.clear()
    assert svc.run(b.id, j.id) is None  # someone else cannot run it
    assert svc.run(a.id, j.id) == "READY"
    assert [s for s, _ in stages.calls] == ["UNDERSTANDING", "EMBEDDING"]  # extraction and describing not redone
    assert [n.id for n in rows(db, "select id from portfolio_nodes")] == node_ids
    assert svc.run(a.id, j.id) is None  # READY: nothing to claim


def test_server_restart_fails_running_jobs_and_retry_resumes_there(db, app, mk, ai, stages):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    j = jobs(db)[0]
    with app.state.engine.begin() as conn:  # as if the process died in the middle of DESCRIBING / before starting
        conn.execute(text("update portfolio_jobs set state='DESCRIBING', failed_stage=null, error_code=null"))
    assert app.state.portfolio.fail_interrupted() == 1
    j = jobs(db)[0]
    assert (j.state, j.failed_stage, j.error_code) == ("FAILED", "DESCRIBING", "SERVER_RESTARTED")
    app.state.portfolio.retry(a.id, j.id, j.revision)
    with app.state.engine.begin() as conn:
        conn.execute(text("update portfolio_jobs set state='QUEUED'"))
    assert app.state.portfolio.fail_interrupted() == 1  # a queued job lost too: failed at its resume stage
    j = jobs(db)[0]
    assert (j.state, j.failed_stage) == ("FAILED", "DESCRIBING")
    app.state.portfolio.retry(a.id, j.id, j.revision)
    stages.calls.clear()
    assert app.state.portfolio.run(a.id, j.id) == "READY"
    assert [s for s, _ in stages.calls] == ["DESCRIBING", "UNDERSTANDING", "EMBEDDING"]


def test_a_job_whose_source_disappeared_fails_with_source_gone(db, app, mk, ai, uploads):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    j = jobs(db)[0]
    with app.state.engine.begin() as conn:
        conn.execute(text("update portfolio_jobs set state='FAILED', failed_stage='EXTRACTING', error_code='X'"))
    for p in uploads.rglob("*"):
        if p.is_file():
            p.unlink()
    app.state.portfolio.retry(a.id, j.id, j.revision)
    assert app.state.portfolio.run(a.id, j.id) == "FAILED"
    assert (jobs(db)[0].failed_stage, jobs(db)[0].error_code) == ("EXTRACTING", "SOURCE_GONE")


# ---- lifecycle (D-I1 I4, D-D1 D6/D7, D-G1) ---------------------------------------------------------------------------


def test_leaving_or_deleting_the_group_keeps_the_copy(db, app, mk, ai):
    gid, (lead, b, c) = confirmed_team(mk, 3)
    submit(b, gid, mine(b, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    submit(lead, gid, mine(lead, gid), files=[("메모.txt", KO_PROSE[2].encode())])
    before_b, before_lead = counts(db, b.id), counts(db, lead.id)
    assert before_b["items"] == 1 and before_lead["items"] == 1
    assert jpost(b, f"/api/groups/{gid}/leave", {"confirm": True}).status_code == 200
    assert counts(db, b.id) == before_b
    assert jpost(lead, f"/api/groups/{gid}/delete", {"confirm": True}).status_code == 200
    assert scalar(db, "select count(*) from groups where id=:g", g=gid) == 0
    assert counts(db, b.id) == before_b and counts(db, lead.id) == before_lead
    assert rows(db, "select source_group_id from portfolio_projects where user_id=:u", u=lead.id)[0].source_group_id == gid


def test_account_deletion_removes_every_portfolio_row_in_the_same_transaction(db, app, mk, ai):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    submit(b, gid, mine(b, gid), files=[("메모.txt", KO_PROSE[2].encode())])
    assert counts(db, a.id)["nodes"] >= 1
    app.state.lifecycle.delete_account(a.id)  # the route's password check is covered by the stage 6 tests
    assert counts(db, a.id) == EMPTY
    assert counts(db, b.id)["items"] == 1  # nobody else's copy is touched


def test_own_deletions_are_ledgered_and_replayed_after_a_restore(db, app, mk, ai, ledger_dir):
    gid, (a, b) = confirmed_team(mk, 2)
    submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())])
    submit(a, gid, mine(a, gid, 1), files=[("메모.txt", KO_PROSE[2].encode())])
    svc = app.state.portfolio
    its = items(db, a.id)
    pid = its[0].project_id
    snap = {t: [r._asdict() for r in rows(db, f"select * from {t} where user_id=:u", u=a.id)] for t in PORTFOLIO_TABLES}
    with pytest.raises(Exception) as e:
        svc.delete_item(b.id, its[0].id)  # not b's
    assert e.value.status == 404
    assert svc.delete_item(a.id, its[0].id) == 1
    assert counts(db, a.id)["items"] == 1
    assert svc.delete_project(a.id, pid) == 1
    assert counts(db, a.id) == EMPTY
    entries = DeletionLedger(ledger_dir).entries()
    assert [(e["kind"], e["user_id"], e["target_id"]) for e in entries] == [
        ("PORTFOLIO_ITEM_DELETED", a.id, its[0].id), ("PORTFOLIO_PROJECT_DELETED", a.id, pid)]
    raw = "".join(p.read_text(encoding="utf-8") for p in ledger_dir.iterdir())
    assert "메모" not in raw and "txt" not in raw  # internal ids only
    _restore(app, snap)  # a backup from before the deletions comes back
    assert counts(db, a.id)["items"] == 2
    out = app.state.lifecycle.replay(entries)
    assert (out["applied"], out["already"]) == (2, 0)  # both came back with the restore; both go again
    assert counts(db, a.id) == EMPTY
    assert app.state.lifecycle.replay(entries)["already"] == 2


def test_a_replayed_undo_and_account_deletion_remove_the_restored_copy(db, app, mk, ai, ledger_dir):
    gid, (a, b) = confirmed_team(mk, 2)
    rid = submit(a, gid, mine(a, gid), files=[("메모.txt", KO_PROSE[1].encode())]).json()["id"]
    rec_rows = {t: [r._asdict() for r in rows(db, f"select * from {t} where {c}=:r", r=rid)]
                for t, c in (("execution_records", "id"), ("execution_files", "record_id"), ("execution_reviews", "record_id"))}
    snap = {t: [r._asdict() for r in rows(db, f"select * from {t} where user_id=:u", u=a.id)] for t in PORTFOLIO_TABLES}
    assert jpost(a, f"/api/groups/{gid}/records/{rid}/undo", {"confirm_delete": True}).status_code == 200
    assert counts(db, a.id)["items"] == 0
    _restore(app, rec_rows, order=("execution_records", "execution_files", "execution_reviews"))
    _restore(app, snap)
    assert counts(db, a.id)["items"] == 1
    out = app.state.lifecycle.replay(DeletionLedger(ledger_dir).entries())
    assert out["applied"] == 1 and counts(db, a.id) == EMPTY
    # account deletion replay: the copy that came back with a restore goes again
    submit(a, gid, mine(a, gid, 1), files=[("메모.txt", KO_PROSE[2].encode())])
    snap2 = {t: [r._asdict() for r in rows(db, f"select * from {t} where user_id=:u", u=a.id)] for t in PORTFOLIO_TABLES}
    app.state.lifecycle.delete_account(a.id)
    assert counts(db, a.id) == EMPTY
    with app.state.engine.begin() as conn:  # the user row comes back with the restore too
        conn.execute(text("insert into users (id, username, display_name, password_hash, notify_opt_in,"
                          " terms_accepted_at, created_at) values (:i, :n, 'x', '!', false, now(), now())"),
                     {"i": a.id, "n": "r" + a.id[:8]})
    _restore(app, snap2)
    assert counts(db, a.id)["items"] == 1
    entries = [e for e in DeletionLedger(ledger_dir).entries() if e["kind"] == "ACCOUNT_DELETED"]
    assert app.state.lifecycle.replay(entries)["applied"] == 1
    assert counts(db, a.id) == EMPTY


def _restore(app, snap: dict, order=PORTFOLIO_TABLES):
    from moim import models

    with app.state.engine.begin() as conn:
        for t in order:
            for r in snap.get(t, []):
                conn.execute(getattr(models, t).insert().values(**r))


# ---- migration ----------------------------------------------------------------------------------------------------------


def test_migration_creates_only_portfolio_tables_with_owner_cascades_and_no_group_fk(db, app):
    with app.state.engine.connect() as conn:
        fks = conn.execute(text(
            "select tc.table_name, ccu.table_name as ref, rc.delete_rule from information_schema.table_constraints tc"
            " join information_schema.constraint_column_usage ccu on ccu.constraint_name = tc.constraint_name"
            " join information_schema.referential_constraints rc on rc.constraint_name = tc.constraint_name"
            " where tc.constraint_type = 'FOREIGN KEY' and tc.table_name like 'portfolio_%'")).all()
        exts = conn.execute(text("select extname from pg_extension")).scalars().all()
    refs = {r.ref for r in fks}
    assert refs <= {"users", "portfolio_projects", "portfolio_items", "portfolio_documents"}
    assert all(r.delete_rule == "CASCADE" for r in fks)
    assert {r.table_name for r in fks if r.ref == "users"} == set(PORTFOLIO_TABLES)
    assert set(exts) == {"plpgsql"}  # no extension (pgvector, pg_trgm) was created
    cfg = Config(str(ROOT / "alembic.ini"))
    app.state.engine.dispose()
    command.downgrade(cfg, "0013")
    with app.state.engine.connect() as conn:
        left = conn.execute(text("select count(*) from information_schema.tables where table_name like 'portfolio_%'")).scalar()
        kept = conn.execute(text("select count(*) from information_schema.tables where table_name = 'execution_records'")).scalar()
    app.state.engine.dispose()
    assert (left, kept) == (0, 1)
    command.upgrade(cfg, "head")
