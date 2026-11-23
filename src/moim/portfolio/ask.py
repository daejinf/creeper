"""Portfolio question answering (stage 8C; RAG_DESIGN 3.1-3.8, 5).

One question, one owner. The flow:

  scope (3.1)      the owner's READY nodes only (`repo.search_scope`), then the project / completion-time filters
  small (3.2)      the whole generation prompt over every node in scope is sized first; at most SMALL_CORPUS_TOKENS
                   (cl100k estimate) and it goes straight to the answer: no question embedding, no search, no rerank
  candidates (3.3) question embedding (1 call) → `M @ q` top 50, and app-side BM25 (k1 1.2, b 0.75, IDF over the
                   scope) top 50
  fusion (3.4)     RRF k=60, one entry per node id, top 25
  gate (3.5)       one LLM call grades every candidate 0/1/2. Any 2 → CORRECT; best 1 → AMBIGUOUS (answer only what
                   the records show); all 0 → INCORRECT: the fixed refusal, and NO generation call. At most 6 evidence
                   nodes (relevance, then RRF order) and their prev/next chunks when the neighbour was graded ≥1 or
                   the chunk is shorter than 150 tokens (Small2Big). A chosen node and its attached neighbours are ONE
                   evidence document with one id; citing it names every place in it
  answer (3.6)     one LLM call, evidence in a U order (best first, second best last), the question before and after.
                   Output `{refused, sentences[{text, citations[]}], missing}`; the server checks every citation is
                   an evidence id and every sentence has 1-3 of them, or the whole answer is CITATION_INVALID. Each
                   citation is resolved to `file · place` in the attachments (a summary node to its evidence blocks)

Nothing about a question is stored (3.7): no table, no question or answer text in any log. What remains is one M0
`provider_call` line per Timely call, one `portfolio_ask` line here (path, gate, counts, timings) and the rate-limit
counters (`auth_throttle`, keyed by a hash of the user id). Every Timely failure is an `AskError` with the M0 code;
there is no retry, no other provider and no made-up answer.
"""
from __future__ import annotations

import logging
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Annotated, Callable

from pydantic import BaseModel, Field

from wemeet_ai.m0.client import AIError, schema_instruction
from wemeet_ai.m0.tracing import scope

from ..auth import throttle as th
from ..execution.files import UUID_RE
from ..timeutil import DISPLAY_TZ
from . import repo
from .service import where
from .tokens import bm25_tokens, estimate
from .understand import DATA_RULE, document

log = logging.getLogger("moim.portfolio")

# RAG_DESIGN 3.2-3.5 starting values
SMALL_CORPUS_TOKENS = 6_000
VECTOR_TOP = 50
BM25_TOP = 50
BM25_K1 = 1.2
BM25_B = 0.75
RRF_K = 60
RERANK_TOP = 25
MAX_EVIDENCE = 6
NEIGHBOR_SMALL_TOKENS = 150
MAX_CITATIONS = 3

MAX_QUESTION_CHARS = 300
EXCERPT_CHARS = 200  # stage 8D (RAG_DESIGN 3.6): the evidence excerpt the server cuts from the owner's own text
REFUSAL = "포트폴리오 기록에서 찾지 못했어요"

# Questions per user (RAG_DESIGN 3.7: the counters are the only thing kept). One question is up to 3 Timely calls.
ASK_USER = th.Rule("portfolio-ask-user", 20, 10 * 60, 10 * 60)
ASK_USER_DAY = th.Rule("portfolio-ask-day", 200, 24 * 60 * 60, 60 * 60)

RERANK_POLICY = (
    "당신은 한 사용자의 포트폴리오 자료 조각이 질문에 답하는 데 쓸모 있는지 판정하는 도구입니다. " + DATA_RULE + "\n"
    "주어진 모든 <document id=\"…\"> 마다 relevance를 정확히 하나씩 매기세요.\n"
    "- 2: 질문에 직접 답하는 정보가 있다.\n"
    "- 1: 질문과 관련 있지만 부분적이다(답의 일부, 배경).\n"
    "- 0: 질문과 무관하다.\n"
    "reason_short는 판정 이유를 한국어 20자 안팎으로 씁니다. 자료의 문장을 옮기지 마세요."
)

ANSWER_POLICY = (
    "당신은 사용자 본인의 포트폴리오 기록(본인이 과제 투두를 끝내며 올린 첨부 자료)만 근거로 본인의 질문에 답하는 도구입니다. "
    + DATA_RULE + "\n"
    "- 주어진 <document id=\"…\"> 자료에 있는 사실만 씁니다. 자료에 없는 사실, 수치, 평가, 추측을 쓰지 마세요.\n"
    "- 답은 한국어 문장 목록(sentences)입니다. 모든 문장에 그 문장을 뒷받침하는 자료 id를 citations로 1~3개 답니다. "
    "가장 적은 수의 충분한 근거만 고르세요. 한 문장에 근거가 4개 이상 필요하면 문장을 나누세요. 근거 없는 도입·맺음 "
    "문장은 쓰지 마세요.\n"
    "- 링크, 마크다운, 코드, 도구 호출을 쓰지 마세요. 자료 속 주소를 방문하라고 안내하지 마세요.\n"
    "- 자료로 답할 수 없으면 refused를 true로 하고 sentences는 빈 목록으로 둡니다. 자료로 확인되지 않는 부분은 missing에 "
    "한 문장으로 씁니다(없으면 null)."
)

AMBIGUOUS_NOTE = ("이번 자료는 질문과 부분적으로만 관련 있습니다. 자료로 확인되는 부분만 답하고, 확인되지 않는 부분은 "
                  "missing에 반드시 씁니다.")


class Judgment(BaseModel):
    id: str = Field(max_length=8)
    relevance: int = Field(ge=0, le=2)
    reason_short: str = Field(default="", max_length=80)


class Relevance(BaseModel):
    judgments: list[Judgment] = Field(max_length=RERANK_TOP)


CitationId = Annotated[str, Field(max_length=8)]


class Sentence(BaseModel):
    text: str = Field(max_length=400)
    # The model is shown the rule (1-3) in the JSON schema; parsing accepts up to 8 so that `check_answer` reports a
    # broken rule as CITATION_INVALID (RAG_DESIGN 3.6) rather than M0's SCHEMA_INVALID.
    citations: list[CitationId] = Field(max_length=8, json_schema_extra={"minItems": 1, "maxItems": MAX_CITATIONS})


class Answer(BaseModel):
    refused: bool
    sentences: list[Sentence] = Field(default_factory=list, max_length=12)
    missing: str | None = Field(default=None, max_length=300)


class AskError(Exception):
    """A question that got no answer. `code` is an M0 error code, CITATION_INVALID, SCHEMA_INVALID, RATE_LIMITED,
    QUESTION_INVALID or NOT_FOUND. `retry_after` is set for RATE_LIMITED."""

    def __init__(self, code: str, retry_after: int = 0, reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.reason = reason  # CITATION_INVALID only: which rule broke (no content)


@dataclass
class Node:
    id: str
    project_id: str
    item_id: str
    document_id: str | None
    kind: str
    locator: dict
    text: str
    index_text: str
    tokens: list[str]
    token_estimate: int
    prev_id: str | None
    next_id: str | None
    source_block_ids: list[str]
    file_name: str | None
    embedding: bytes | None
    embed_model: str | None
    embed_dim: int | None


@dataclass
class Citation:
    label: str          # "파일명 · 위치"
    file_name: str
    where: str
    project_id: str
    item_id: str
    document_id: str | None
    excerpt: str = field(default="", compare=False)   # stage 8D: server-cut from the attachment text, never AI output


@dataclass
class AskResult:
    status: str                       # ANSWERED | PARTIAL | REFUSED
    message: str | None = None        # the fixed refusal
    sentences: list[dict] = field(default_factory=list)   # [{"text", "citations": [Citation]}]
    missing: str | None = None
    meta: dict = field(default_factory=dict)              # path, gate, calls, timings, usage: never shown, no text

    def public(self) -> dict:
        """What the 8D API may return: the answer and where each sentence comes from. No meta."""
        return {"status": self.status, "message": self.message, "missing": self.missing,
                "sentences": [{"text": s["text"], "citations": [
                    {"label": c.label, "file_name": c.file_name, "where": c.where, "project_id": c.project_id,
                     "item_id": c.item_id, "document_id": c.document_id, "excerpt": c.excerpt}
                    for c in s["citations"]]}
                    for s in self.sentences]}


# ---- pure parts (deterministic, unit-tested) ---------------------------------------------------------------------------


def bm25(query: list[str], docs: list[list[str]], k1: float = BM25_K1, b: float = BM25_B) -> list[float]:
    """Okapi BM25 of every doc for the query. IDF is over `docs` (the question's scope), the non-negative
    `ln(1 + (N - df + 0.5) / (df + 0.5))`; each distinct query term counts once."""
    n = len(docs)
    if not n:
        return []
    terms = list(dict.fromkeys(query))
    lengths = [len(d) for d in docs]
    avgdl = (sum(lengths) / n) or 1.0
    tfs = [Counter(d) for d in docs]
    df = {t: sum(1 for c in tfs if t in c) for t in terms}
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in terms}
    scores = []
    for c, dl in zip(tfs, lengths):
        s = 0.0
        for t in terms:
            f = c.get(t, 0)
            if f:
                s += idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def top(scores, k: int, positive_only: bool = False) -> list[int]:
    """Indexes of the k best scores, ties by index (scope order)."""
    idx = [i for i, s in enumerate(scores) if not positive_only or s > 0]
    return sorted(idx, key=lambda i: (-scores[i], i))[:k]


def rrf(rankings: list[list[str]], k: int = RRF_K, limit: int = RERANK_TOP) -> list[tuple[str, float]]:
    """Reciprocal rank fusion (Cormack et al. 2009): `Σ 1 / (k + rank)`, rank from 1, one entry per id. Ties go to
    the better single rank, then to the earlier list."""
    score: dict[str, float] = {}
    best: dict[str, tuple[int, int]] = {}
    for li, ranking in enumerate(rankings):
        for rank, node_id in enumerate(dict.fromkeys(ranking), start=1):
            score[node_id] = score.get(node_id, 0.0) + 1.0 / (k + rank)
            best[node_id] = min(best.get(node_id, (rank, li)), (rank, li))
    ordered = sorted(score, key=lambda x: (-score[x], best[x]))
    return [(x, score[x]) for x in ordered[:limit]]


def gate(relevance: dict[str, int]) -> str:
    top_grade = max(relevance.values(), default=0)
    return "CORRECT" if top_grade == 2 else "AMBIGUOUS" if top_grade == 1 else "INCORRECT"


def select_evidence(order: list[str], relevance: dict[str, int], nodes: dict[str, Node]) -> list[list[str]]:
    """Up to MAX_EVIDENCE groups `[prev?, chosen, next?]`, best first. The chosen nodes have relevance ≥1, by
    relevance then RRF order. A prev/next chunk joins when it was graded ≥1 itself, or the chosen chunk is shorter
    than NEIGHBOR_SMALL_TOKENS (Small2Big). A node is used once."""
    rank = {x: i for i, x in enumerate(order)}
    chosen = sorted((x for x in order if relevance.get(x, 0) >= 1), key=lambda x: (-relevance[x], rank[x]))
    chosen = chosen[:MAX_EVIDENCE]
    used = set(chosen)
    groups = []
    for x in chosen:
        n = nodes[x]
        group = [x]
        for side in ("prev_id", "next_id"):
            nb = getattr(n, side)
            if nb and nb in nodes and nb not in used and \
                    (relevance.get(nb, 0) >= 1 or n.token_estimate < NEIGHBOR_SMALL_TOKENS):
                used.add(nb)
                group = [nb] + group if side == "prev_id" else group + [nb]
        groups.append(group)
    return groups


def u_order(groups: list) -> list:
    """Best group first, second best last (Lost in the Middle; RAG_DESIGN 3.6)."""
    if len(groups) < 3:
        return list(groups)
    return [groups[0], *groups[2:], groups[1]]


def answer_messages(question: str, docs: list[tuple[str, str]], ambiguous: bool = False) -> list[dict]:
    """The generation request (without M0's schema message): the question before and after the documents."""
    policy = ANSWER_POLICY + ("\n" + AMBIGUOUS_NOTE if ambiguous else "")
    body = "\n\n".join(document(i, text) for i, text in docs)
    user = f"질문: {question}\n\n{body}\n\n다시 질문: {question}"
    return [{"role": "system", "content": policy}, {"role": "user", "content": user}]


def evidence_docs(evidence: dict[str, list[Node]]) -> list[tuple[str, str]]:
    """(id, text) of each evidence document: the index texts of one group, in document order, one after another."""
    return [(i, "\n\n".join(n.index_text for n in group)) for i, group in evidence.items()]


def prompt_estimate(messages: list[dict], output_schema: type[BaseModel]) -> int:
    """cl100k estimate of a whole `complete_json` input: M0's schema message plus every message."""
    return estimate(schema_instruction(output_schema)) + sum(estimate(m["content"]) for m in messages)


def check_answer(out: Answer, evidence_ids: set[str]) -> None:
    """RAG_DESIGN 3.6 server checks. Any violation fails the whole answer (nothing is dropped)."""
    if out.refused:
        return
    if not out.sentences:
        raise AskError("CITATION_INVALID", reason="NO_SENTENCES")
    for s in out.sentences:
        cited = list(dict.fromkeys(s.citations))
        reason = ("EMPTY_TEXT" if not s.text.strip() else "UNCITED" if not cited else
                  "TOO_MANY" if len(cited) > MAX_CITATIONS else "REPEATED" if len(cited) != len(s.citations) else
                  "NOT_EVIDENCE" if not set(cited) <= evidence_ids else None)
        if reason:
            raise AskError("CITATION_INVALID", reason=reason)


def block_refs(node: Node) -> list[tuple[str, str]]:
    """The (item_id, block_id) evidence of a summary node. A `doc_summary` cites blocks of its own item; a stage 8D
    `project_summary` stores each block as "item_id/block_id" (its sentences rest on several items)."""
    if node.kind == "doc_summary":
        return [(node.item_id, b) for b in node.source_block_ids]
    out = []
    for ref in node.source_block_ids:
        item_id, _, block_id = str(ref).partition("/")
        if item_id and block_id:
            out.append((item_id, block_id))
    return out


def excerpt(text: str) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= EXCERPT_CHARS else t[:EXCERPT_CHARS - 1] + "…"


def resolve(node: Node, places: dict, texts: dict | None = None) -> list[Citation]:
    """Where a cited node is in the attachments. A chunk is its own place (its excerpt is its own text); a summary node
    is the places of the blocks its claims rest on (in claim order, each place once; the excerpt is that block's
    text)."""
    texts = texts or {}
    if node.kind == "chunk":
        name = node.file_name or str(node.locator.get("file") or "")
        w = where(node.locator)
        return [Citation(f"{name} · {w}", name, w, node.project_id, node.item_id, node.document_id,
                         excerpt(node.text))]
    out: list[Citation] = []
    seen = set()
    for key in block_refs(node):
        place = places.get(key)
        if place is None:
            continue
        doc_id, name, loc = place
        w = where({k: v for k, v in loc.items() if k != "part"})
        if (doc_id, w) in seen:
            continue
        seen.add((doc_id, w))
        out.append(Citation(f"{name} · {w}", name, w, node.project_id, key[0], doc_id, excerpt(texts.get(key, ""))))
    return out


def clean_question(question) -> str:
    if not isinstance(question, str):
        raise AskError("QUESTION_INVALID")
    q = " ".join(question.split())
    if not q or len(q) > MAX_QUESTION_CHARS or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", question):
        raise AskError("QUESTION_INVALID")
    return q


def kst_range(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    """Completion-date filter (inclusive dates in KST) as UTC-aware bounds `[since, until + 1 day)`."""
    lo = datetime.combine(since, dtime.min, DISPLAY_TZ) if since else None
    hi = datetime.combine(until, dtime.min, DISPLAY_TZ) + timedelta(days=1) if until else None
    return lo, hi


# ---- the service ---------------------------------------------------------------------------------------------------------


class _Calls:
    def __init__(self):
        self.n = 0
        self.by_stage: dict[str, int] = {}
        self.usage: dict[str, int] = {}
        self.ai_ms = 0

    def run(self, stage: str, fn, *args, **kwargs):
        self.n += 1
        self.by_stage[stage] = self.by_stage.get(stage, 0) + 1
        with scope(None, str(uuid.uuid4()), str(uuid.uuid4()), None, "portfolio.ask." + stage):
            out = fn(*args, **kwargs)  # an AIError goes up as is
        self.ai_ms += int(getattr(out, "latency_ms", 0) or 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.usage[k] = self.usage.get(k, 0) + int(getattr(out.usage, k, 0) or 0)
        return out


class PortfolioAsk:
    """`ask(user_id, question, filters)` for the 8D API. `client_factory()` is the M0 `TimelyClient` in the product
    and a FIXTURE client in deterministic tests. `throttle` holds the per-user question counters."""

    def __init__(self, session_factory, client_factory: Callable[[], object], throttle: th.Throttle):
        self._sf = session_factory
        self._client_factory = client_factory
        self._throttle = throttle

    def ask(self, user_id: str, question: str, filters: dict | None = None) -> AskResult:
        started = time.perf_counter()
        meta: dict = {"path": None, "gate": None, "calls": 0}
        calls = _Calls()
        try:
            result = self._ask(user_id, question, filters or {}, meta, calls)
        except AIError as exc:
            meta["error"] = exc.code
            raise AskError(exc.code) from None
        except AskError as exc:
            meta["error"] = exc.code
            if exc.reason:
                meta["error_reason"] = exc.reason
            raise
        finally:
            meta.update(calls=calls.n, by_stage=calls.by_stage, usage=calls.usage, ai_ms=calls.ai_ms,
                        ms=int((time.perf_counter() - started) * 1000))
            self._log(meta)
        result.meta = meta
        return result

    def _ask(self, user_id: str, question: str, filters: dict, meta: dict, calls: _Calls) -> AskResult:
        if not isinstance(user_id, str) or not UUID_RE.fullmatch(user_id):
            raise repo.ScopeError("portfolio access needs the owner's user id")
        q = clean_question(question)
        project_id, since, until = self._filters(filters)
        locked = self._throttle.locked_for([(ASK_USER, user_id), (ASK_USER_DAY, user_id)])
        if locked:
            raise AskError("RATE_LIMITED", locked)
        self._throttle.hit(ASK_USER, user_id)
        self._throttle.hit(ASK_USER_DAY, user_id)

        t0 = time.perf_counter()
        with self._sf() as s:
            if project_id is not None and repo.get_project(s, user_id=user_id, project_id=project_id) is None:
                raise AskError("NOT_FOUND")
            lo, hi = kst_range(since, until)
            rows = repo.search_scope(s, user_id=user_id, project_id=project_id, since=lo, until=hi)
            nodes = [Node(r.id, r.project_id, r.item_id, r.document_id, r.kind, r.locator or {}, r.text or "",
                          r.index_text, list(r.bigrams or []), r.token_estimate, r.prev_id, r.next_id,
                          list(r.source_block_ids or []), r.file_name, r.embedding, r.embed_model, r.embed_dim)
                     for r in rows]
            refs = sorted({k for n in nodes if n.kind != "chunk" for k in block_refs(n)})
            places = repo.block_places(s, user_id=user_id, item_ids=sorted({k[0] for k in refs}))
            places = {"places": places, "texts": repo.block_texts(s, user_id=user_id, keys=refs)}
        meta["load_ms"] = int((time.perf_counter() - t0) * 1000)
        meta["nodes"] = len(nodes)
        if not nodes:
            meta["path"] = "empty"
            return AskResult("REFUSED", REFUSAL)
        by_id = {n.id: n for n in nodes}

        # 3.2: the whole generation prompt over everything in scope, in submission order
        ids_all = {str(i + 1): [n] for i, n in enumerate(nodes)}
        messages = answer_messages(q, evidence_docs(ids_all))
        meta["small_estimate"] = prompt_estimate(messages, Answer)
        if meta["small_estimate"] <= SMALL_CORPUS_TOKENS:
            meta["path"] = "small"
            return self._answer(calls, messages, ids_all, places, meta)

        meta["path"] = "search"
        client = self._client_factory()
        order = self._candidates(calls, client, q, nodes, meta)

        # 3.5 rerank + gate
        ids = {str(i + 1): x for i, x in enumerate(order)}
        rr_messages = [{"role": "system", "content": RERANK_POLICY},
                       {"role": "user", "content": f"질문: {q}\n\n" + "\n\n".join(
                           document(i, by_id[x].index_text) for i, x in ids.items()) + f"\n\n다시 질문: {q}"}]
        out = calls.run("rerank", client.complete_json, rr_messages, Relevance, {"reasoning_effort": "low"})
        rel = self._relevance(out.parsed_output, ids)
        meta["gate"] = gate(rel)
        meta["graded"] = {g: sum(1 for v in rel.values() if v == g) for g in (2, 1, 0)}
        if meta["gate"] == "INCORRECT":
            return AskResult("REFUSED", REFUSAL)
        groups = u_order(select_evidence(order, rel, by_id))
        meta["evidence"] = len(groups)
        meta["evidence_nodes"] = sum(len(g) for g in groups)
        # one evidence document (one citable id) per group: a chosen node with its attached neighbours
        evidence = {str(i + 1): [by_id[x] for x in g] for i, g in enumerate(groups)}
        messages = answer_messages(q, evidence_docs(evidence), ambiguous=meta["gate"] == "AMBIGUOUS")
        meta["answer_estimate"] = prompt_estimate(messages, Answer)
        return self._answer(calls, messages, evidence, places, meta, client)

    def _candidates(self, calls: _Calls, client, q: str, nodes: list[Node], meta: dict) -> list[str]:
        import numpy as np

        emb = calls.run("embed", client.embed, [q])
        qv = np.asarray(emb.vectors, dtype=np.float32).reshape(-1)
        meta["question_estimate"] = estimate(q)
        t0 = time.perf_counter()
        vec_nodes = [n for n in nodes if n.embedding and n.embed_model == emb.model and n.embed_dim == qv.shape[0]
                     and len(n.embedding) == 4 * qv.shape[0]]
        meta["vector_nodes"] = len(vec_nodes)
        vector: list[str] = []
        if vec_nodes:
            m = np.frombuffer(b"".join(n.embedding for n in vec_nodes), dtype="<f4").reshape(len(vec_nodes), -1)
            vector = [vec_nodes[i].id for i in top(m @ qv, VECTOR_TOP)]
        lexical = [nodes[i].id for i in top(bm25(bm25_tokens(q), [n.tokens for n in nodes]), BM25_TOP, True)]
        fused = rrf([vector, lexical])
        meta["search_ms"] = int((time.perf_counter() - t0) * 1000)
        meta["candidates"] = len(fused)
        return [x for x, _ in fused]

    @staticmethod
    def _relevance(out: Relevance, ids: dict[str, str]) -> dict[str, int]:
        """Grades by node id. An id that was not sent, or one graded twice, fails the call (no repair); a candidate
        the model left out counts as 0."""
        seen = set()
        rel = {x: 0 for x in ids.values()}
        for j in out.judgments:
            if j.id not in ids or j.id in seen:
                raise AskError("SCHEMA_INVALID")
            seen.add(j.id)
            rel[ids[j.id]] = j.relevance
        return rel

    def _answer(self, calls: _Calls, messages: list[dict], evidence: dict[str, list[Node]], places: dict, meta: dict,
                client=None) -> AskResult:
        client = client or self._client_factory()
        out = calls.run("answer", client.complete_json, messages, Answer, {"reasoning_effort": "low"})
        if meta.get("path") == "small":
            meta["prompt_tokens_actual"] = int(getattr(out.usage, "prompt_tokens", 0) or 0)
        else:
            meta["answer_prompt_tokens"] = int(getattr(out.usage, "prompt_tokens", 0) or 0)
        a: Answer = out.parsed_output
        check_answer(a, set(evidence))
        if a.refused:
            meta["refused_by"] = "answer"
            return AskResult("REFUSED", REFUSAL, missing=_line(a.missing))
        sentences = []
        for s in a.sentences:
            cites: list[Citation] = []
            for cid in s.citations:
                for node in evidence[cid]:  # a group resolves to every place in it, in document order
                    for c in resolve(node, places["places"], places["texts"]):
                        if c not in cites:
                            cites.append(c)
            sentences.append({"text": " ".join(s.text.split()), "citations": cites})
        meta["sentences"] = len(sentences)
        partial = meta.get("gate") == "AMBIGUOUS"
        return AskResult("PARTIAL" if partial else "ANSWERED", None, sentences, _line(a.missing))

    @staticmethod
    def _filters(filters: dict) -> tuple[str | None, date | None, date | None]:
        unknown = set(filters) - {"project_id", "since", "until"}
        project_id = filters.get("project_id")
        since, until = filters.get("since"), filters.get("until")
        if unknown or (project_id is not None and not (isinstance(project_id, str) and UUID_RE.fullmatch(project_id))):
            raise AskError("QUESTION_INVALID")
        for d in (since, until):
            if d is not None and (not isinstance(d, date) or isinstance(d, datetime)):
                raise AskError("QUESTION_INVALID")
        if since and until and since > until:
            raise AskError("QUESTION_INVALID")
        return project_id, since, until

    @staticmethod
    def _log(meta: dict) -> None:
        """One line per question: path, gate, counts, timings and usage. Never the question, the material or the answer."""
        u = meta.get("usage") or {}
        log.info("portfolio_ask path=%s gate=%s calls=%d embed=%d rerank=%d answer=%d nodes=%s candidates=%s "
                 "evidence=%s load_ms=%s search_ms=%s ai_ms=%s ms=%s prompt_tokens=%d completion_tokens=%d "
                 "small_estimate=%s%s",
                 meta.get("path") or "-", meta.get("gate") or "-", meta.get("calls", 0),
                 meta.get("by_stage", {}).get("embed", 0), meta.get("by_stage", {}).get("rerank", 0),
                 meta.get("by_stage", {}).get("answer", 0), meta.get("nodes", "-"), meta.get("candidates", "-"),
                 meta.get("evidence", "-"), meta.get("load_ms", "-"), meta.get("search_ms", "-"), meta.get("ai_ms", 0),
                 meta.get("ms", 0), u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                 meta.get("small_estimate", "-"), (f" error={meta['error']}" if meta.get("error") else "") +
                 (f" reason={meta['error_reason']}" if meta.get("error_reason") else ""))


def _line(text: str | None) -> str | None:
    t = " ".join((text or "").split())
    return t or None
