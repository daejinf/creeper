"""Portfolio summaries (stage 8D; RAG_DESIGN 2.5.1-2.5.2, D-I2).

Two summaries, both made only when the owner presses `만들기`/`다시 만들기` (never automatically):

  project summary   one per project: 맡은 일 / 만든 결과 / 배운 점, from the `doc_summary` claims of the project's READY
                    items. One `complete_json` call, then one embedding: its `project_summary` node joins the search pool
  portfolio summary one per user: 1-3 intro sentences and 0-8 evidence-backed skills, from the project summaries (or the
                    claims of a project without one). One `complete_json` call, no node (it is derived only from material
                    already in the pool)

Every sentence and skill must cite input ids (claims or project-summary sentences); the server resolves them to the
attachment blocks those rest on. A broken rule fails the whole run as CITATION_INVALID and the last good content stays.
A skill's number is the count of distinct attachments its blocks are in, counted by the server. Inputs are the owner's
own READY material and public metadata only: never hidden criteria, conversations or anyone else's data (D-I1 I1-I2).

`basis` records what the content was made from; the API shows "오래됨" when it no longer matches. Nothing is
regenerated automatically. Every Timely failure is FAILED(M0 code); no retry, no other provider.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Annotated, Callable

from pydantic import BaseModel, Field

from wemeet_ai.m0.client import AIError
from wemeet_ai.m0.tracing import scope

from ..auth import throttle as th
from ..execution.files import UUID_RE
from ..timeutil import utcnow
from . import repo
from .service import PortfolioError, where
from .tokens import bm25_tokens, estimate
from .understand import DATA_RULE, document

log = logging.getLogger("moim.portfolio")

MAX_CITES = 3
MAX_SKILL_CITES = 8
GROUPS = ("did", "made", "learned")
GROUP_LABELS = {"did": "맡은 일", "made": "만든 결과", "learned": "배운 점"}
ACTION_MAX = 64

# Each run is up to 2 Timely calls; the owner's button is the only trigger.
SUMMARY_USER = th.Rule("portfolio-summary-user", 12, 60 * 60, 60 * 60)

MSG_NOTHING = "정리할 자료가 아직 없어요. 첨부가 정리되면 다시 눌러 주세요."
MSG_BUSY = "지금 정리하고 있어요. 잠시 후 새로고침해 주세요."
MSG_TOO_MANY = "요청이 너무 많아요. 잠시 후 다시 시도해 주세요."
MSG_INVALID = "요청 형식이 올바르지 않아요."
MSG_NOT_FOUND = "찾을 수 없어요."

PROJECT_POLICY = (
    "당신은 한 사람이 한 프로젝트에서 과제 투두를 끝내며 올린 첨부 자료의 요약 주장들을 읽고, 그 사람의 포트폴리오용 "
    "프로젝트 정리를 쓰는 도구입니다. " + DATA_RULE + "\n"
    "- did(맡은 일), made(만든 결과), learned(배운 점) 세 묶음으로 씁니다. 묶음마다 0~4문장, 모두 합쳐 1~10문장입니다.\n"
    "- id가 meta인 문서는 배경 정보라 sources에 쓰지 않습니다.\n"
    "- 모든 문장에 그 문장을 뒷받침하는 주장 id(cN)를 sources로 1~3개 답니다. 자료에 없는 사실, 수치, 평가, 추측을 쓰지 "
    "마세요. 근거가 없는 묶음은 빈 목록으로 둡니다.\n"
    "- 본인이 한 일과 팀이 한 일을 구분합니다. 자료가 팀의 결정이라고 하면 본인 일로 바꿔 쓰지 마세요.\n"
    "- 한국어로, 한 문장 200자 이내, 해요체로 씁니다. 링크, 마크다운, 코드를 쓰지 마세요."
)

PORTFOLIO_POLICY = (
    "당신은 한 사람의 여러 프로젝트 정리를 읽고 포트폴리오 첫 화면의 소개 문장과 역량 목록을 쓰는 도구입니다. "
    + DATA_RULE + "\n"
    "- intro: 이 사람을 소개하는 문장 1~3개. 여러 프로젝트에 공통으로 드러나는 일을 우선합니다.\n"
    "- skills: 자료로 확인되는 역량 0~8개. 이름은 20자 이내의 짧은 명사구(예: 사용자 조사, 데이터 시각화)입니다.\n"
    "- 모든 intro 문장에 근거 id를 sources로 1~3개, 모든 skill에 1~8개 답니다. 근거 id는 주어진 <document id>(cN·pN)만 씁니다.\n"
    "- 자료에 없는 사실, 수치, 평가, 추측, 칭찬을 쓰지 마세요. 역량은 자료에 그 일을 한 기록이 있을 때만 씁니다.\n"
    "- 한국어로, 한 문장 200자 이내, 해요체로 씁니다. 링크, 마크다운, 코드를 쓰지 마세요."
)

SourceId = Annotated[str, Field(max_length=8)]


class SummarySentence(BaseModel):
    text: str = Field(max_length=300)
    # The model is shown 1-3 in the schema; parsing accepts up to 8 so a broken rule becomes CITATION_INVALID.
    sources: list[SourceId] = Field(max_length=8, json_schema_extra={"minItems": 1, "maxItems": MAX_CITES})


class ProjectSummaryOut(BaseModel):
    did: list[SummarySentence] = Field(default_factory=list, max_length=6)
    made: list[SummarySentence] = Field(default_factory=list, max_length=6)
    learned: list[SummarySentence] = Field(default_factory=list, max_length=6)


class Skill(BaseModel):
    name: str = Field(max_length=40)
    sources: list[SourceId] = Field(max_length=12, json_schema_extra={"minItems": 1, "maxItems": MAX_SKILL_CITES})


class PortfolioSummaryOut(BaseModel):
    intro: list[SummarySentence] = Field(default_factory=list, max_length=5)
    skills: list[Skill] = Field(default_factory=list, max_length=12)


class SummaryError(Exception):
    """CITATION_INVALID with the broken rule (no content)."""

    def __init__(self, code: str, reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.reason = reason


# ---- pure checks (deterministic, unit-tested) ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _check_sources(sources: list[str], ids: set[str], low: int, high: int) -> None:
    cited = list(dict.fromkeys(sources))
    if len(cited) != len(sources):
        raise SummaryError("CITATION_INVALID", "REPEATED")
    if not low <= len(cited) <= high:
        raise SummaryError("CITATION_INVALID", "UNCITED" if not cited else "TOO_MANY")
    bad = [c for c in cited if c not in ids]
    if bad:  # which kind of id it was (letters only, never text) so that a repeat can be diagnosed
        kinds = sorted({"meta" if c == "meta" else re.sub(r"[^a-z]", "", c.lower())[:4] or "other" for c in bad})
        raise SummaryError("CITATION_INVALID", "NOT_INPUT:" + "+".join(kinds))


def check_project(out: ProjectSummaryOut, ids: set[str]) -> None:
    total = 0
    for g in GROUPS:
        sentences = getattr(out, g)
        if len(sentences) > 4:
            raise SummaryError("CITATION_INVALID", "TOO_MANY_SENTENCES")
        for s in sentences:
            if not _clean(s.text) or len(_clean(s.text)) > 200:
                raise SummaryError("CITATION_INVALID", "BAD_TEXT")
            _check_sources(s.sources, ids, 1, MAX_CITES)
            total += 1
    if not 1 <= total <= 10:
        raise SummaryError("CITATION_INVALID", "SENTENCE_COUNT")


def check_portfolio(out: PortfolioSummaryOut, ids: set[str]) -> None:
    if not 1 <= len(out.intro) <= 3:
        raise SummaryError("CITATION_INVALID", "SENTENCE_COUNT")
    if len(out.skills) > 8:
        raise SummaryError("CITATION_INVALID", "TOO_MANY_SKILLS")
    for s in out.intro:
        if not _clean(s.text) or len(_clean(s.text)) > 200:
            raise SummaryError("CITATION_INVALID", "BAD_TEXT")
        _check_sources(s.sources, ids, 1, MAX_CITES)
    for k in out.skills:
        if not _clean(k.name) or len(_clean(k.name)) > 20:
            raise SummaryError("CITATION_INVALID", "BAD_TEXT")
        _check_sources(k.sources, ids, 1, MAX_SKILL_CITES)


def merge_refs(*lists) -> list[list[str]]:
    out: list[list[str]] = []
    for lst in lists:
        for r in lst:
            r = [r[0], r[1]]
            if r not in out:
                out.append(r)
    return out


def merge_skills(skills: list[dict]) -> list[dict]:
    """Same name (spaces and case ignored) → one skill with the union of evidence, first spelling kept."""
    out: dict[str, dict] = {}
    for k in skills:
        key = "".join(k["name"].split()).lower()
        if key in out:
            out[key]["refs"] = merge_refs(out[key]["refs"], k["refs"])
        else:
            out[key] = {"name": k["name"], "refs": merge_refs(k["refs"])}
    return list(out.values())


def prune(content: dict | None, gone_items: set[str]) -> dict | None:
    """Drop every sentence or skill resting on a deleted item (its text came from that item's material)."""
    if not content:
        return content
    keep = lambda e: not any(r[0] in gone_items for r in e.get("refs", []))  # noqa: E731
    return {k: [e for e in v if keep(e)] if isinstance(v, list) else v for k, v in content.items()}


# ---- the service ---------------------------------------------------------------------------------------------------------


class SummaryService:
    """`start` claims a run (the owner's button, idempotent on `action_id`); `run` does it in the background."""

    def __init__(self, session_factory, client_factory: Callable[[], object], throttle: th.Throttle, clock=utcnow):
        self._sf = session_factory
        self._client_factory = client_factory
        self._throttle = throttle
        self._clock = clock

    # ---- claim ----------------------------------------------------------------------------------------------------------

    def start(self, user_id: str, project_id: str | None, action_id) -> dict:
        if not isinstance(action_id, str) or not 8 <= len(action_id) <= ACTION_MAX or not action_id.isascii() \
                or not all(c.isalnum() or c in "-_" for c in action_id):
            raise PortfolioError(422, "INVALID", MSG_INVALID)
        if project_id is not None and not (isinstance(project_id, str) and UUID_RE.fullmatch(project_id)):
            raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
        with self._sf.begin() as s:
            if project_id is not None and repo.get_project(s, user_id=user_id, project_id=project_id) is None:
                raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
            row = repo.get_summary(s, user_id=user_id, project_id=project_id)
            if row is not None and row.last_action_id == action_id:  # the same click again: no second run
                return {"state": row.state, "revision": row.revision, "started": False}
            if not repo.ready_items(s, user_id=user_id, project_id=project_id):
                raise PortfolioError(409, "NOTHING_TO_SUMMARIZE", MSG_NOTHING)
        locked = self._throttle.locked_for([(SUMMARY_USER, user_id)])
        if locked:
            raise PortfolioError(429, "RATE_LIMITED", MSG_TOO_MANY, retry_after=locked)
        with self._sf.begin() as s:
            outcome, rev = repo.start_summary(s, user_id=user_id, project_id=project_id, action_id=action_id,
                                              now=self._clock())
        if outcome == "BUSY":
            raise PortfolioError(409, "SUMMARY_RUNNING", MSG_BUSY)
        if outcome == "STARTED":
            self._throttle.hit(SUMMARY_USER, user_id)
        return {"state": "RUNNING", "revision": rev, "started": outcome == "STARTED"}

    def fail_interrupted(self) -> int:
        """Server start: a run lost with the previous process becomes FAILED(SERVER_RESTARTED); content stays."""
        from sqlalchemy import update

        from ..models import portfolio_summaries as t

        with self._sf.begin() as s:
            return s.execute(update(t).where(t.c.state == "RUNNING").values(
                state="FAILED", error_code="SERVER_RESTARTED", updated_at=self._clock())).rowcount

    # ---- run ------------------------------------------------------------------------------------------------------------

    def run(self, user_id: str, project_id: str | None, revision: int) -> str:
        started = time.perf_counter()
        meta: dict = {"calls": 0, "usage": {}, "ai_ms": 0}
        try:
            content, basis, node = (self._project(user_id, project_id, meta) if project_id is not None
                                    else self._portfolio(user_id, meta))
            code = None
        except AIError as exc:
            content = basis = node = None
            code = exc.code
        except SummaryError as exc:
            content = basis = node = None
            code = exc.code
            meta["reason"] = exc.reason
        except Exception as exc:  # never the message: it may hold material
            log.error("portfolio_summary user_id=%s error_type=%s", user_id, type(exc).__name__)
            content = basis = node = None
            code = "HANDLER_ERROR"
        meta["ms"] = int((time.perf_counter() - started) * 1000)
        with self._sf.begin() as s:
            ok = repo.finish_summary(s, user_id=user_id, project_id=project_id, revision=revision, content=content,
                                     basis=basis, error_code=code, meta=meta, now=self._clock())
            if ok and code is None and project_id is not None:
                repo.replace_project_summary_node(s, user_id=user_id, project_id=project_id, node=node)
        state = "READY" if code is None else "FAILED"
        log.info("portfolio_summary scope=%s state=%s calls=%d ms=%d%s%s", "PROJECT" if project_id else "PORTFOLIO",
                 state if ok else "SUPERSEDED", meta["calls"], meta["ms"], f" error={code}" if code else "",
                 f" reason={meta['reason']}" if meta.get("reason") else "")
        return state

    def _call(self, meta: dict, stage: str, fn, *args, **kwargs):
        meta["calls"] += 1
        with scope(None, str(uuid.uuid4()), str(uuid.uuid4()), None, "portfolio.summary." + stage):
            out = fn(*args, **kwargs)
        meta["ai_ms"] += int(getattr(out, "latency_ms", 0) or 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            meta["usage"][k] = meta["usage"].get(k, 0) + int(getattr(out.usage, k, 0) or 0)
        return out

    def _claims(self, s, user_id: str, items: list) -> list[tuple[dict, object]]:
        """Every claim of the given items' submission summaries, with its item."""
        by_id = {i.id: i for i in items}
        out = []
        for n in repo.summary_nodes(s, user_id=user_id, item_ids=list(by_id)):
            for c in (n.locator or {}).get("claims", []):
                out.append(({"aspect": c.get("aspect"), "text": _clean(c.get("text")),
                             "refs": [[n.item_id, b] for b in c.get("block_ids", [])]}, by_id[n.item_id]))
        return out

    def _project(self, user_id: str, project_id: str, meta: dict):
        import numpy as np

        with self._sf() as s:
            project = repo.get_project(s, user_id=user_id, project_id=project_id)
            items = repo.ready_items(s, user_id=user_id, project_id=project_id)
            if project is None or not items:
                raise SummaryError("NOTHING_TO_SUMMARIZE")
            claims = self._claims(s, user_id, items)
        if not claims:
            raise SummaryError("NOTHING_TO_SUMMARIZE")
        ids = {f"c{i + 1}": c for i, (c, _) in enumerate(claims)}
        meta_doc = document("meta", "\n".join([f"프로젝트: {project.name}", f"역할: {project.role_name}",
                                               "완료한 투두: " + ", ".join(dict.fromkeys(i.task_title for i in items))]))
        parts = [meta_doc] + [document(cid, c["text"], aspect=c["aspect"], todo=item.task_title)
                              for cid, (c, item) in zip(ids, claims)]
        messages = [{"role": "system", "content": PROJECT_POLICY}, {"role": "user", "content": "\n\n".join(parts)}]
        client = self._client_factory()
        out = self._call(meta, "project", client.complete_json, messages, ProjectSummaryOut,
                         {"reasoning_effort": "low"})
        p: ProjectSummaryOut = out.parsed_output
        check_project(p, set(ids))
        content = {g: [{"text": _clean(x.text), "refs": merge_refs(*(ids[c]["refs"] for c in x.sources))}
                       for x in getattr(p, g)] for g in GROUPS}
        text = "\n".join(f"{GROUP_LABELS[g]}: {e['text']}" for g in GROUPS for e in content[g])
        index_text = f"[{project.name} / {project.role_name} / 프로젝트 요약]\n" + text
        emb = self._call(meta, "embed", client.embed, [index_text])
        v = np.asarray(emb.vectors, dtype=np.float32).reshape(-1)
        if v.shape[0] != emb.dim or not np.isfinite(v).all() or abs(float(np.linalg.norm(v)) - 1.0) > 1e-3:
            raise AIError("INVALID_EMBEDDING", "summary embedding failed the checks")
        refs = merge_refs(*(e["refs"] for g in GROUPS for e in content[g]))
        node = dict(id=str(uuid.uuid4()), kind="project_summary", position=0, locator={"summary": "project"},
                    text=text, context=None, index_text=index_text, bigrams=bm25_tokens(index_text),
                    token_estimate=estimate(text), source_block_ids=[f"{a}/{b}" for a, b in refs],
                    embedding=v.astype("<f4").tobytes(), embed_model=emb.model[:64], embed_dim=int(emb.dim))
        basis = {"items": sorted(i.id for i in items)}
        return content, basis, node

    def _portfolio(self, user_id: str, meta: dict):
        with self._sf() as s:
            items = repo.ready_items(s, user_id=user_id)
            if not items:
                raise SummaryError("NOTHING_TO_SUMMARIZE")
            projects = {p.id: p for p in repo.list_projects(s, user_id=user_id)}
            summaries = {r.project_id: r for r in repo.list_summaries(s, user_id=user_id) if r.scope == "PROJECT"}
            claims = self._claims(s, user_id, items)
        ready_pids = list(dict.fromkeys(i.project_id for i in items))
        sources: dict[str, dict] = {}
        parts = []
        basis_projects = {}
        for pid in ready_pids:
            p = projects[pid]
            row = summaries.get(pid)
            content = row.content if row is not None and row.content else None
            basis_projects[pid] = row.generated_at.isoformat() if content and row.generated_at else None
            if content:
                for g in GROUPS:
                    for e in content.get(g, []):
                        sid = f"p{len(sources) + 1}"
                        sources[sid] = e
                        parts.append(document(sid, e["text"], project=p.name, role=p.role_name, part=GROUP_LABELS[g]))
            else:
                for c, item in claims:
                    if item.project_id == pid:
                        sid = f"c{len(sources) + 1}"
                        sources[sid] = c
                        parts.append(document(sid, c["text"], project=p.name, role=p.role_name, aspect=c["aspect"]))
        if not sources:
            raise SummaryError("NOTHING_TO_SUMMARIZE")
        messages = [{"role": "system", "content": PORTFOLIO_POLICY}, {"role": "user", "content": "\n\n".join(parts)}]
        client = self._client_factory()
        out = self._call(meta, "portfolio", client.complete_json, messages, PortfolioSummaryOut,
                         {"reasoning_effort": "low"})
        o: PortfolioSummaryOut = out.parsed_output
        check_portfolio(o, set(sources))
        content = {"intro": [{"text": _clean(x.text), "refs": merge_refs(*(sources[c]["refs"] for c in x.sources))}
                             for x in o.intro],
                   "skills": merge_skills([{"name": _clean(k.name), "refs": merge_refs(*(sources[c]["refs"]
                                                                                      for c in k.sources))}
                                           for k in o.skills])}
        basis = {"items": sorted(i.id for i in items), "projects": basis_projects}
        return content, basis, None


# ---- reading for the API (no AI) ------------------------------------------------------------------------------------------


def resolve_refs(refs: list, places: dict) -> list[dict]:
    """`[{label, file_name, where, document_id}]` of the blocks still there, each place once, in order."""
    out, seen = [], set()
    for r in refs:
        place = places.get((r[0], r[1]))
        if place is None:
            continue
        doc_id, name, loc = place
        w = where({k: v for k, v in loc.items() if k != "part"})
        if (doc_id, w) in seen:
            continue
        seen.add((doc_id, w))
        out.append({"label": f"{name} · {w}", "file_name": name, "where": w, "document_id": doc_id})
    return out


def public(row, places: dict, current_items: list[str], current_projects: dict | None = None) -> dict | None:
    """The API shape of a summary row: content with citations (an entry whose evidence is gone is hidden), the state
    of the latest run, and `stale` when the material changed since. No basis, meta, action id or refs."""
    if row is None:
        return None
    content = row.content or {}
    out: dict = {"state": row.state, "error_code": row.error_code,
                 "generated_at": row.generated_at.isoformat() if row.generated_at else None,
                 "has_content": bool(row.content)}
    basis = row.basis or {}
    stale = bool(row.content) and sorted(basis.get("items", [])) != sorted(current_items)
    if current_projects is not None and row.content:
        stale = stale or (basis.get("projects") or {}) != current_projects
    out["stale"] = stale
    if row.scope == "PROJECT":
        for g in GROUPS:
            out[g] = [{"text": e["text"], "citations": c} for e in content.get(g, [])
                      if (c := resolve_refs(e.get("refs", []), places))]
    else:
        out["intro"] = [{"text": e["text"], "citations": c} for e in content.get("intro", [])
                        if (c := resolve_refs(e.get("refs", []), places))]
        skills = []
        for k in content.get("skills", []):
            c = resolve_refs(k.get("refs", []), places)
            docs = {x["document_id"] for x in c}
            if docs:
                skills.append({"name": k["name"], "documents": len(docs)})
        out["skills"] = sorted(skills, key=lambda k: -k["documents"])
        out["basis_counts"] = {"projects": len(basis.get("projects") or {}), "items": len(basis.get("items") or [])}
    return out


def refs_of(row) -> list[tuple[str, str]]:
    """Every (item_id, block_id) a summary row cites (to load their places)."""
    if row is None or not row.content:
        return []
    out = []
    for v in row.content.values():
        if isinstance(v, list):
            for e in v:
                out.extend((r[0], r[1]) for r in e.get("refs", []))
    return out
