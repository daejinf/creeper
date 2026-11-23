"""Stage 8C measurement (RAG_DESIGN 3.3, 8): load and search time of one question by nodes per user.

Runs ONLY against the disposable test database (`MOIM_TEST_DATABASE_URL`, guarded by `tests/dbsafety.py`: localhost,
port 54329, `moim_test`, `MOIM_ENV=test`). It resets that schema, migrates it, writes synthetic users with N nodes
each (Korean index text of about 250 cl100k tokens, its BM25 tokens, a random unit 1536-d float32 embedding), then
times what `PortfolioAsk` does per question without any Timely call:

  load    `repo.search_scope` (owner + READY first) and turning the rows into nodes
  search  `np.frombuffer` → `M @ q` top 50, BM25 over the scope top 50, RRF top 25

No AI call is made (the question vector is random). Prints one line per N with the median and max of R runs.

    $env:MOIM_ENV='test'; .\\.venv\\Scripts\\python.exe tools\\measure_portfolio_search.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, insert, text  # noqa: E402

import dbsafety  # noqa: E402
from moim.db import make_session_factory  # noqa: E402
from moim.models import portfolio_documents, portfolio_items, portfolio_jobs, portfolio_nodes, portfolio_projects  # noqa: E402
from moim.portfolio import ask as A  # noqa: E402
from moim.portfolio import repo  # noqa: E402
from moim.portfolio.tokens import bm25_tokens, estimate  # noqa: E402

SIZES = (100, 500, 1000, 2000, 5000)
RUNS = 7
WORDS = ("설문 응답 분석 화면 설계 검색 기능 구현 사용성 테스트 결과 보고서 발표 자료 지도 타일 캐시 접근성 점검 버튼 "
         "색 대비 자막 편집 콘티 장면 영상 음악 일정 회의 결정 역할 투두 마감 개선 피드백 측정 시간 단축 응답자 학년 "
         "만족도 의견 건물 입구 층별 시설 즐겨찾기 초성 자모 함수 번들 크기 프레임워크").split()


def body(rng) -> str:
    out, n = [], 0
    while n < 245:
        w = WORDS[rng.integers(len(WORDS))]
        out.append(w + ("다." if rng.random() < 0.12 else ""))
        n = estimate(" ".join(out))
    return " ".join(out)


def seed_user(engine, n_nodes: int, rng) -> str:
    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("insert into users (id, username, display_name, password_hash, terms_accepted_at) "
                       "values (:i, :u, :u, 'x', now())"), {"i": uid, "u": f"m{n_nodes}{uid[:6]}".replace("-", "")[:16]})
        pid = str(uuid.uuid4())
        c.execute(insert(portfolio_projects).values(id=pid, user_id=uid, source_group_id=str(uuid.uuid4()),
                                                    name="측정 프로젝트", description="", role_name="측정"))
        per_item = 20
        rows = []
        for k in range(0, n_nodes, per_item):
            iid, did = str(uuid.uuid4()), str(uuid.uuid4())
            c.execute(insert(portfolio_items).values(
                id=iid, user_id=uid, project_id=pid, source_record_id=str(uuid.uuid4()),
                source_task_id=str(uuid.uuid4()), task_title=f"투두 {k}", due_date=now.date(), role_name="측정",
                memo=None, file_count=1, completed_by="SELF", review_decision=None, completed_at=now))
            c.execute(insert(portfolio_jobs).values(id=str(uuid.uuid4()), user_id=uid, item_id=iid, state="READY"))
            c.execute(insert(portfolio_documents).values(
                id=did, user_id=uid, item_id=iid, position=1, source_file_id=str(uuid.uuid4()), file_name="측정.pdf",
                kind="PDF", status="EXTRACTED", char_count=0, blocks=[]))
            for p in range(min(per_item, n_nodes - k)):
                idx = f"[측정 프로젝트 / 측정 / 투두 {k} / 측정.pdf / {p + 1}쪽]\n" + body(rng)
                v = rng.standard_normal(1536).astype(np.float32)
                v /= np.linalg.norm(v)
                rows.append(dict(id=str(uuid.uuid4()), user_id=uid, project_id=pid, item_id=iid, document_id=did,
                                 kind="chunk", position=p + 1, locator={"file": "측정.pdf", "page": p + 1},
                                 text=idx, context=None, index_text=idx, bigrams=bm25_tokens(idx),
                                 token_estimate=estimate(idx), source_block_ids=["d1b1"],
                                 embedding=v.astype("<f4").tobytes(), embed_model=A_MODEL, embed_dim=1536))
        c.execute(insert(portfolio_nodes), rows)
    return uid


A_MODEL = "text-embedding-3-small"


class _Client:
    def __init__(self, rng):
        self.rng = rng

    def embed(self, texts, *, model=None, stats=None):
        from types import SimpleNamespace
        v = self.rng.standard_normal(1536).astype(np.float32)
        return SimpleNamespace(vectors=(v / np.linalg.norm(v)).reshape(1, -1), model=A_MODEL, dim=1536,
                               usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0), latency_ms=0)


def main() -> None:
    url = os.environ.get("MOIM_TEST_DATABASE_URL")
    if not url:
        raise SystemExit("MOIM_TEST_DATABASE_URL is required")
    dbsafety.reset_test_schema(url)  # refuses anything but the disposable test database
    os.environ["MOIM_DATABASE_URL"] = url
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = create_engine(url)
    sf = make_session_factory(engine)
    rng = np.random.default_rng(8)
    client = _Client(rng)
    question = "사용성 테스트에서 과제 완료 시간을 얼마나 줄였어?"
    print(f"numpy {np.__version__}; {RUNS} runs per size; ms = median / max")
    for n in SIZES:
        uid = seed_user(engine, n, rng)
        loads, searches = [], []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            with sf() as s:
                rows = repo.search_scope(s, user_id=uid)
            nodes = [A.Node(r.id, r.project_id, r.item_id, r.document_id, r.kind, r.locator or {}, r.index_text,
                            list(r.bigrams or []), r.token_estimate, r.prev_id, r.next_id,
                            list(r.source_block_ids or []), r.file_name, r.embedding, r.embed_model, r.embed_dim)
                     for r in rows]
            loads.append((time.perf_counter() - t0) * 1000)
            meta: dict = {}
            t1 = time.perf_counter()  # includes the (instant, fake) question embedding
            A.PortfolioAsk(sf, lambda: client, None)._candidates(A._Calls(), client, question, nodes, meta)
            searches.append((time.perf_counter() - t1) * 1000)
        total = [a + b for a, b in zip(loads, searches)]
        print(f"nodes={n:5d} load={statistics.median(loads):7.1f}/{max(loads):7.1f} "
              f"search={statistics.median(searches):6.1f}/{max(searches):6.1f} "
              f"total={statistics.median(total):7.1f}/{max(total):7.1f} candidates={meta.get('candidates')}")
    engine.dispose()
    dbsafety.reset_test_schema(url)


if __name__ == "__main__":
    main()
