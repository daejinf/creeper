"""Stage 8D portfolio API (D-I1, D-I2; RAG_DESIGN 3.6, 10).

Every route needs a signed-in user; the owner comes from the session only and every read goes through `repo` with it,
so another user's project, item or job answers 404 exactly like a missing one. State changes are POST/DELETE with
CSRF. `만들기` (summaries) and `가져오기` carry an `action_id` (the same id twice never starts two runs); `다시 시도`
carries the job revision it saw. Nothing returned holds hidden criteria, another user's data, embeddings, server
paths, prompts, index headers or LLM contexts. Questions and answers are not stored.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..auth.deps import require_user, verify_csrf
from ..auth.sessions import SessionUser
from ..execution.files import UUID_RE
from ..timeutil import DISPLAY_TZ, utcnow
from . import ingest, repo
from . import summary as SM
from .ask import AskError, PortfolioAsk
from .service import FAILED, READY, PortfolioError, PortfolioService

log = logging.getLogger("moim.portfolio.routes")

NO_STORE = {"Cache-Control": "no-store"}
ACTION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
IMPORT_CALL_LIMIT = 100  # D-A8 / D-I1 I6: an import expected to need this many Timely calls or more does not run
VISION_PER_ITEM = 8

MSG_INVALID = "요청 형식이 올바르지 않아요."
MSG_NOT_FOUND = "찾을 수 없어요."
MSG_IMPORT_LARGE = "한 번에 가져오기에는 기록이 많아요. 관리자 확인 뒤에 가져올 수 있어요."
MSG_NOTHING_TO_IMPORT = "가져올 수 있는 이전 기록이 없어요."
MSG_ASK = {
    "QUESTION_INVALID": (422, "질문은 1~300자로 입력해 주세요."),
    "NOT_FOUND": (404, MSG_NOT_FOUND),
    "RATE_LIMITED": (429, "질문이 너무 많아요. 잠시 후 다시 시도해 주세요."),
}
MSG_ASK_FAILED = "답을 만들지 못했어요. 다시 시도해 주세요."

# Fixed suggested questions (RAG_DESIGN 3.6, D-I2): constants, no AI.
SUGGEST_PORTFOLIO = ["가장 큰 성과는?", "협업에서 맡은 일은?", "사용한 도구는?"]
SUGGEST_PROJECT = ["가장 어려웠던 점은?", "숫자로 보인 성과는?"]
SUGGEST_FOLLOWUP = ["왜 그렇게 했나요?", "다음엔 무엇을 고칠까요?"]
SUGGEST_REFUSED = ["어떤 자료를 만들었나요?", "맡은 일은 무엇이었나요?"]
DOC_KIND = {"PNG": "이미지", "JPEG": "이미지", "WEBP": "이미지"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionBody(_Body):
    action_id: str = Field(max_length=64)


class RetryBody(_Body):
    expected_revision: int = Field(ge=1, le=1_000_000)


class QueryBody(_Body):
    question: str = Field(max_length=2000)
    project_id: str | None = Field(default=None, max_length=36)


def _action(action_id: str) -> str:
    if not ACTION_RE.fullmatch(action_id or ""):
        raise PortfolioError(422, "INVALID", MSG_INVALID)
    return action_id


def _iso_date(dt) -> str | None:
    return dt.astimezone(DISPLAY_TZ).date().isoformat() if dt else None


def _file_kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].upper() if "." in (name or "") else ""
    return ext[:5] or "FILE"


def estimate_import_calls(records: list[tuple[str, list[str]]]) -> int:
    """An upper estimate of the Timely calls an import needs: per record, understanding 1 + embedding 1 + vision for
    each image file and up to 2 per PDF/PPTX (picture pages, slide images), at most 8 vision calls (RAG_DESIGN 6)."""
    total = 0
    for _, kinds in records:
        vision = sum(1 if k in ("PNG", "JPEG", "WEBP") else 2 if k in ("PDF", "PPTX") else 0 for k in kinds)
        total += 2 + min(VISION_PER_ITEM, vision)
    return total


def make_router(portfolio: PortfolioService, summaries: SM.SummaryService, asker: PortfolioAsk, session_factory,
                clock=utcnow) -> APIRouter:
    router = APIRouter()

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    # ---- reading --------------------------------------------------------------------------------------------------------

    def _overview(uid: str) -> dict:
        with session_factory() as s:
            projects = repo.list_projects(s, user_id=uid)
            items = repo.item_overview(s, user_id=uid)
            rows = {(r.scope, r.project_id): r for r in repo.list_summaries(s, user_id=uid)}
            port = rows.get(("PORTFOLIO", None))
            refs = SM.refs_of(port)
            places = repo.block_places(s, user_id=uid, item_ids=sorted({r[0] for r in refs}))
            importable = repo.importable_records(s, user_id=uid)
        ready_ids = sorted(i.id for i in items if i.state == READY)
        proj_gen = {}
        for p in projects:
            r = rows.get(("PROJECT", p.id))
            if any(i.project_id == p.id and i.state == READY for i in items):
                proj_gen[p.id] = r.generated_at.isoformat() if r is not None and r.content and r.generated_at else None
        out_projects = []
        shapes = ("c", "a", "q")
        for n, p in enumerate(projects):
            its = [i for i in items if i.project_id == p.id]
            r = rows.get(("PROJECT", p.id))
            mine_ready = sorted(i.id for i in its if i.state == READY)
            stale = bool(r is not None and r.content and sorted((r.basis or {}).get("items", [])) != mine_ready)
            out_projects.append({
                "id": p.id, "name": p.name, "role": p.role_name, "shape": shapes[n % 3],
                "items": len(its), "ready": len(mine_ready), "failed": sum(1 for i in its if i.state == FAILED),
                "running": sum(1 for i in its if i.state not in (READY, FAILED)),
                "places": sum(int(i.chunks or 0) for i in its if i.state == READY),
                "done": len(its),
                "summary": None if r is None else {"state": r.state, "stale": stale, "has_content": bool(r.content)},
            })
        estimate = estimate_import_calls(importable)
        return {
            "portfolio_summary": SM.public(port, places, ready_ids, proj_gen),
            "projects": out_projects,
            "ready_items": len(ready_ids),
            "import": {"records": len(importable), "estimated_calls": estimate,
                       "allowed": 0 < len(importable) and estimate < IMPORT_CALL_LIMIT},
            "suggestions": SUGGEST_PORTFOLIO,
        }

    @router.get("/api/portfolio")
    def overview(user: SessionUser = Depends(require_user)):
        return json_ok(_overview(user.user_id))

    @router.get("/api/portfolio/projects/{project_id}")
    def project(project_id: str, user: SessionUser = Depends(require_user)):
        uid = user.user_id
        with session_factory() as s:
            p = repo.get_project(s, user_id=uid, project_id=project_id)
            if p is None:
                raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
            items = [i for i in repo.item_overview(s, user_id=uid) if i.project_id == p.id]
            row = repo.get_summary(s, user_id=uid, project_id=p.id)
            docs = repo.documents_of(s, user_id=uid, item_ids=[i.id for i in items])
            refs = SM.refs_of(row)
            places = repo.block_places(s, user_id=uid, item_ids=sorted({r[0] for r in refs}))
        ready = sorted(i.id for i in items if i.state == READY)
        summary = SM.public(row, places, ready)
        cited: dict[str, int] = {}
        for g in SM.GROUPS:
            for e in (summary or {}).get(g, []):
                for c in e["citations"]:
                    cited[c["document_id"]] = cited.get(c["document_id"], 0) + 1
        out_items = []
        for i in items:
            ds = [d for d in docs if d.item_id == i.id]
            out_items.append({
                "id": i.id, "title": i.task_title, "completed_on": _iso_date(i.completed_at),
                "state": "READY" if i.state == READY else "FAILED" if i.state == FAILED else "RUNNING",
                "job_id": i.job_id if i.state == FAILED else None, "revision": i.revision if i.state == FAILED else None,
                "files": [{"name": d.file_name, "kind": _file_kind(d.file_name),
                           "blocks": len(d.blocks or [])} for d in ds],
                "cited": sum(cited.get(d.id, 0) for d in ds),
            })
        dates = [i.completed_at for i in items]
        return json_ok({
            "id": p.id, "name": p.name, "role": p.role_name,
            "period": [_iso_date(min(dates)), _iso_date(max(dates))] if dates else None,
            "done": len(items), "summary": summary, "items": out_items, "suggestions": SUGGEST_PROJECT,
        })

    # ---- jobs -----------------------------------------------------------------------------------------------------------

    @router.post("/api/portfolio/jobs/{job_id}/retry", dependencies=[Depends(verify_csrf)])
    def retry(job_id: str, body: RetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        if not UUID_RE.fullmatch(job_id or ""):
            raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
        out = portfolio.retry(user.user_id, job_id, body.expected_revision)
        background.add_task(portfolio.run, user.user_id, job_id)
        return json_ok({"state": out["state"], "revision": out["revision"]}, 202)

    # ---- summaries ------------------------------------------------------------------------------------------------------

    def _summary(project_id, body: ActionBody, background: BackgroundTasks, user: SessionUser) -> JSONResponse:
        out = summaries.start(user.user_id, project_id, _action(body.action_id))
        if out["started"]:
            background.add_task(summaries.run, user.user_id, project_id, out["revision"])
        return json_ok({"state": out["state"]}, 202)

    @router.post("/api/portfolio/projects/{project_id}/summary", dependencies=[Depends(verify_csrf)])
    def project_summary(project_id: str, body: ActionBody, background: BackgroundTasks,
                        user: SessionUser = Depends(require_user)):
        return _summary(project_id, body, background, user)

    @router.post("/api/portfolio/summary", dependencies=[Depends(verify_csrf)])
    def portfolio_summary(body: ActionBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        return _summary(None, body, background, user)

    # ---- questions ------------------------------------------------------------------------------------------------------

    @router.post("/api/portfolio/query", dependencies=[Depends(verify_csrf)])
    async def query(body: QueryBody, user: SessionUser = Depends(require_user)):
        filters = {"project_id": body.project_id} if body.project_id else {}
        try:
            result = await run_in_threadpool(asker.ask, user.user_id, body.question, filters)
        except AskError as exc:
            status, message = MSG_ASK.get(exc.code, (502, MSG_ASK_FAILED))
            raise PortfolioError(status, exc.code if exc.code in MSG_ASK else "ANSWER_FAILED", message,
                                 retry_after=exc.retry_after or None) from None
        pub = result.public()
        evidence: list[dict] = []
        index: dict[tuple, int] = {}
        sentences = []
        for snt in pub["sentences"]:
            nums = []
            for c in snt["citations"]:
                key = (c["document_id"], c["where"])
                if key not in index:
                    index[key] = len(evidence) + 1
                    evidence.append({"n": index[key], "label": c["label"], "file_name": c["file_name"],
                                     "where": c["where"], "kind": _file_kind(c["file_name"]), "excerpt": c["excerpt"]})
                if index[key] not in nums:
                    nums.append(index[key])
            sentences.append({"text": snt["text"], "cites": nums})
        payload = {"status": pub["status"], "message": pub["message"], "missing": pub["missing"],
                   "sentences": sentences, "evidence": evidence,
                   "files": len({e["file_name"] for e in evidence}),
                   "suggestions": SUGGEST_FOLLOWUP if sentences else SUGGEST_REFUSED}
        if pub["status"] == "REFUSED":  # what this scope does hold: the owner's READY todo titles (deterministic)
            with session_factory() as s:  # the project was already checked to be the owner's by `ask`
                titles = [i.task_title for i in repo.ready_items(s, user_id=user.user_id, project_id=body.project_id)]
            payload["available"] = list(dict.fromkeys(titles))[:3]
        return json_ok(payload)

    # ---- import (D-I1 I6) -----------------------------------------------------------------------------------------------

    @router.post("/api/portfolio/import", dependencies=[Depends(verify_csrf)])
    def do_import(body: ActionBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        _action(body.action_id)
        uid = user.user_id
        with session_factory() as s:
            records = repo.importable_records(s, user_id=uid)
        if not records:
            raise PortfolioError(409, "NOTHING_TO_IMPORT", MSG_NOTHING_TO_IMPORT)
        estimate = estimate_import_calls(records)
        if estimate >= IMPORT_CALL_LIMIT:
            log.info("portfolio_import refused user_id=%s records=%d estimated_calls=%d", uid, len(records), estimate)
            raise PortfolioError(409, "IMPORT_TOO_LARGE", MSG_IMPORT_LARGE,
                                 extra={"records": len(records), "estimated_calls": estimate})
        with session_factory.begin() as s:
            for rid, _ in records:  # idempotent: a record that already has an item is skipped by the unique key
                ingest.enqueue_record(s, user_id=uid, record_id=rid, now=clock())
            pairs = ingest.take_new(s)
        if pairs:
            background.add_task(portfolio.run_many, pairs)
        log.info("portfolio_import user_id=%s records=%d jobs=%d estimated_calls=%d", uid, len(records), len(pairs),
                 estimate)
        return json_ok({"queued": len(pairs)}, 202)

    # ---- the owner's deletions (D-I1 I4) --------------------------------------------------------------------------------

    @router.delete("/api/portfolio/items/{item_id}", dependencies=[Depends(verify_csrf)])
    def delete_item(item_id: str, user: SessionUser = Depends(require_user)):
        if not UUID_RE.fullmatch(item_id or ""):
            raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
        portfolio.delete_item(user.user_id, item_id)
        return json_ok({"deleted": True})

    @router.delete("/api/portfolio/projects/{project_id}", dependencies=[Depends(verify_csrf)])
    def delete_project(project_id: str, user: SessionUser = Depends(require_user)):
        if not UUID_RE.fullmatch(project_id or ""):
            raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
        portfolio.delete_project(user.user_id, project_id)
        return json_ok({"deleted": True})

    return router
