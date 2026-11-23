"""Portfolio ingest jobs (RAG_DESIGN 2.7) and the owner's own deletions (D-I1 I4).

State machine: QUEUED → EXTRACTING → DESCRIBING → UNDERSTANDING → EMBEDDING → READY, or FAILED(failed_stage,
error_code) from any stage. Each stage commits its own result before the next one starts. `다시 시도` (`retry`) puts a
FAILED job back to QUEUED with `resume_stage = failed_stage` and a new revision; the run then starts from that stage
and keeps the earlier stages' results. There is no automatic retry. On server start, `fail_interrupted` turns every
QUEUED or running job into FAILED(SERVER_RESTARTED) at the stage it was in (no automatic re-run).

EXTRACTING is deterministic (stage 8A: `extract`, `chunk`, `tokens`). DESCRIBING, UNDERSTANDING and EMBEDDING are
the Timely stages of 8B (`understand.PortfolioAI`, wired in `main.create_app`). A stage without a handler fails as
FAILED(STAGE_UNAVAILABLE). A failing stage records its call counts and usage so far in `stage_meta`. Deterministic
tests inject fake stage handlers or a FIXTURE client; they are never LIVE.

A job runs like the other background work of the app (FastAPI `BackgroundTasks`, or the startup executor): `run`
takes the owner and the job id, and every read and write goes through `repo` with that owner.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from ..account.ledger import DeletionLedger, LedgerUnavailable
from ..auth.errors import AuthError
from ..execution.files import Storage
from ..models import PORTFOLIO_STAGES, portfolio_jobs
from ..timeutil import utcnow
from . import chunk as C
from . import extract as X
from . import repo
from .tokens import bm25_tokens

log = logging.getLogger("moim.portfolio")

QUEUED, READY, FAILED = "QUEUED", "READY", "FAILED"
EXTRACTING, DESCRIBING, UNDERSTANDING, EMBEDDING = PORTFOLIO_STAGES
RUNNING = PORTFOLIO_STAGES
MAX_SUBMISSION_CHARS = 400_000  # all files of one submission together
MSG_NOT_FOUND = "찾을 수 없어요."
MSG_NOT_RETRYABLE = "지금은 다시 시도할 수 없어요. 새로고침해서 상태를 확인해 주세요."
MSG_UNAVAILABLE = "지금은 처리할 수 없어요. 잠시 후 다시 시도해 주세요."


class StageError(Exception):
    """A stage failed with a fixed code (never a message that could hold file content)."""

    def __init__(self, code: str, meta: dict | None = None):
        super().__init__(code)
        self.code = code
        self.meta = meta or {}  # call counts and usage so far (stage 8B); never content


class PortfolioError(AuthError):
    """Same JSON shape as AuthError."""


@dataclass(frozen=True)
class StageInput:
    """What a stage handler gets: the owner and the job's ids. It reads and writes through `repo` with `user_id`."""

    session_factory: sessionmaker
    user_id: str
    job_id: str
    item_id: str
    project_id: str


StageHandler = Callable[[StageInput], dict | None]


def _next(stage: str) -> str:
    i = PORTFOLIO_STAGES.index(stage)
    return PORTFOLIO_STAGES[i + 1] if i + 1 < len(PORTFOLIO_STAGES) else READY


class PortfolioService:
    def __init__(self, session_factory: sessionmaker[Session], storage: Storage,
                 stages: dict[str, StageHandler] | None = None, clock=utcnow, ledger: DeletionLedger | None = None):
        self._sf = session_factory
        self.storage = storage
        self._stages: dict[str, StageHandler] = {EXTRACTING: self._extract, **(stages or {})}
        self._clock = clock
        self.ledger = ledger or DeletionLedger(None)

    # ---- running ------------------------------------------------------------------------------------------------------

    def run(self, user_id: str, job_id: str) -> str | None:
        """Background: claim a QUEUED job and run its stages in order. Returns the final state (None if not claimed)."""
        with self._sf.begin() as s:
            job = repo.get_job(s, user_id=user_id, job_id=job_id, lock=True)
            if job is None or job.state != QUEUED:
                return None
            item = repo.get_item(s, user_id=user_id, item_id=job.item_id)
            stage = job.resume_stage or EXTRACTING
            rev = job.revision
            meta = dict(job.stage_meta or {})
            repo.update_job(s, user_id=user_id, job_id=job_id, revision=rev, from_state=QUEUED, state=stage,
                            resume_stage=None, started_at=self._clock(), updated_at=self._clock(), finished_at=None)
        inp = StageInput(self._sf, user_id, job_id, item.id, item.project_id)
        while stage != READY:
            handler = self._stages.get(stage)
            started = time.perf_counter()
            try:
                if handler is None:
                    raise StageError("STAGE_UNAVAILABLE")
                out = handler(inp) or {}
            except StageError as exc:
                meta[stage] = {"ms": int((time.perf_counter() - started) * 1000), **exc.meta}
                self._fail(user_id, job_id, rev, stage, exc.code, meta)
                return FAILED
            except Exception as exc:  # never the message: it may hold file content
                log.error("portfolio_event job_id=%s stage=%s state=FAILED error_type=%s", job_id, stage,
                          type(exc).__name__)
                meta[stage] = {"ms": int((time.perf_counter() - started) * 1000)}
                self._fail(user_id, job_id, rev, stage, "HANDLER_ERROR", meta)
                return FAILED
            meta[stage] = {"ms": int((time.perf_counter() - started) * 1000), **out}
            nxt = _next(stage)
            with self._sf.begin() as s:
                values = dict(state=nxt, stage_meta=meta, updated_at=self._clock())
                if nxt == READY:
                    values["finished_at"] = self._clock()
                if not repo.update_job(s, user_id=user_id, job_id=job_id, revision=rev, from_state=stage, **values):
                    return None  # deleted (undo, own deletion, account deletion) or superseded meanwhile
            log.info("portfolio_event job_id=%s stage=%s state=%s", job_id, stage, nxt)
            stage = nxt
        return READY

    def run_many(self, pairs) -> None:
        for user_id, job_id in pairs:
            self.run(user_id, job_id)

    def _fail(self, user_id: str, job_id: str, rev: int, stage: str, code: str, meta: dict | None = None) -> None:
        extra = {"stage_meta": meta} if meta is not None else {}
        with self._sf.begin() as s:
            repo.update_job(s, user_id=user_id, job_id=job_id, revision=rev, from_state=stage, state=FAILED,
                            failed_stage=stage, error_code=code[:64], finished_at=self._clock(),
                            updated_at=self._clock(), **extra)
        log.info("portfolio_event job_id=%s stage=%s state=FAILED error=%s", job_id, stage, code)

    def retry(self, user_id: str, job_id: str, expected_revision: int) -> dict:
        """The owner's `다시 시도`: from the failed stage, as a new revision."""
        with self._sf.begin() as s:
            job = repo.get_job(s, user_id=user_id, job_id=job_id, lock=True)
            if job is None:
                raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
            if job.state != FAILED or job.revision != expected_revision:
                raise PortfolioError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
            repo.update_job(s, user_id=user_id, job_id=job_id, revision=job.revision, from_state=FAILED,
                            state=QUEUED, resume_stage=job.failed_stage, failed_stage=None, error_code=None,
                            new_revision=job.revision + 1, updated_at=self._clock(), finished_at=None)
            return {"state": QUEUED, "resume_stage": job.failed_stage, "revision": job.revision + 1}

    def fail_interrupted(self) -> int:
        """Server start: jobs lost with the previous process become a retryable failure at the stage they were in. The
        one cross-user write here; it reads nothing back but the count."""
        now = self._clock()
        n = 0
        with self._sf.begin() as s:
            for stage in RUNNING:
                n += s.execute(update(portfolio_jobs).where(portfolio_jobs.c.state == stage).values(
                    state=FAILED, failed_stage=stage, error_code="SERVER_RESTARTED", finished_at=now,
                    updated_at=now)).rowcount
            n += s.execute(update(portfolio_jobs).where(portfolio_jobs.c.state == QUEUED).values(
                state=FAILED, failed_stage=func.coalesce(portfolio_jobs.c.resume_stage, EXTRACTING),
                error_code="SERVER_RESTARTED", resume_stage=None, finished_at=now, updated_at=now)).rowcount
        return n

    # ---- EXTRACTING (stage 8A) ----------------------------------------------------------------------------------------

    def _extract(self, inp: StageInput) -> dict:
        uid = inp.user_id
        with self._sf() as s:
            item = repo.get_item(s, user_id=uid, item_id=inp.item_id)
            project = repo.get_project(s, user_id=uid, project_id=inp.project_id)
            if item is None or project is None:
                raise StageError("ITEM_GONE")
            files = repo.source_files(s, user_id=uid, record_id=item.source_record_id)
        if not files:
            raise StageError("SOURCE_GONE")  # the record was undone or its group deleted before the job ran
        documents = []
        left = MAX_SUBMISSION_CHARS
        for pos, f in enumerate(files, start=1):
            try:
                data = self.storage.read(f.group_id, f.id)
            except (OSError, ValueError):
                raise StageError("SOURCE_GONE") from None
            cap = max(0, min(X.MAX_DOC_CHARS, left))
            ex = X.extract_file(f"d{pos}", f.original_name, data, cap=cap)
            del data
            if ex.truncated_reason == "CHAR_LIMIT" and cap < X.MAX_DOC_CHARS:
                ex.truncated_reason = "SUBMISSION_LIMIT"  # the submission's total cap, not this file's own
            left -= ex.char_count
            if ex.status == "NAME_ONLY":
                text = f"파일: {f.original_name}" + (f"\n메모: {item.memo}" if item.memo else "")
                ex.blocks = [{"block_id": f"d{pos}b1", "kind": "meta", "locator": {"file": f.original_name},
                              "text": text, "vision": False}]
            doc_id = str(uuid.uuid4())
            documents.append(dict(id=doc_id, position=pos, source_file_id=f.id, file_name=f.original_name,
                                  kind=ex.kind, status=ex.status, error_code=ex.error_code,
                                  truncated=ex.truncated_reason is not None, truncated_reason=ex.truncated_reason,
                                  char_count=ex.char_count, blocks=ex.blocks))
        nodes = build_chunk_nodes(project.name, item.role_name, item.task_title, documents)
        with self._sf.begin() as s:
            job = repo.get_job(s, user_id=uid, job_id=inp.job_id, lock=True)
            if job is None or job.state != EXTRACTING:
                raise StageError("SUPERSEDED")
            repo.replace_extraction(s, user_id=uid, item_id=item.id, project_id=project.id, documents=documents,
                                    nodes=nodes)
        return {"documents": len(documents), "chunks": len(nodes),
                "vision_targets": sum(1 for d in documents for b in d["blocks"] if b.get("vision")),
                "truncated": sum(1 for d in documents if d["truncated"]),
                "rejected": sum(1 for d in documents if d["status"] == "REJECTED")}

    # ---- the owner's own deletions (D-I1 I4; D-G1 ledger) -------------------------------------------------------------

    def delete_item(self, user_id: str, item_id: str, record: bool = True) -> int:
        return self._delete("PORTFOLIO_ITEM_DELETED", user_id, item_id, repo.delete_item, "item_id", record)

    def delete_project(self, user_id: str, project_id: str, record: bool = True) -> int:
        return self._delete("PORTFOLIO_PROJECT_DELETED", user_id, project_id, repo.delete_project, "project_id",
                            record)

    def _delete(self, kind: str, user_id: str, target: str, fn, arg: str, record: bool) -> int:
        entry = None
        try:
            with self._sf.begin() as s:
                n = fn(s, user_id=user_id, **{arg: target})
                if not n:
                    raise PortfolioError(404, "NOT_FOUND", MSG_NOT_FOUND)
                if record:  # D-G1, fail closed: the last step before the commit
                    entry = self.ledger.record(kind, user_id, target_id=target)
        except LedgerUnavailable:
            raise PortfolioError(503, "DELETION_UNAVAILABLE", MSG_UNAVAILABLE) from None
        except BaseException:
            self.ledger.cancel(entry)
            raise
        log.info("portfolio_event kind=%s user_id=%s", kind, user_id)
        return n


def build_chunk_nodes(project_name: str, role: str, title: str, documents: list[dict]) -> list[dict]:
    """The chunk nodes of a submission from its documents' blocks, in order, linked prev/next within one document.
    The index text is the fixed header, a split table's header row, and the chunk; stage 8B UNDERSTANDING puts the
    chunk's context right after the header line."""
    nodes: list[dict] = []
    position = 0
    for d in documents:
        doc_nodes = []
        for c in C.chunk_blocks(d["blocks"]):
            position += 1
            head = header(project_name, role, title, d["file_name"], c.locator)
            index_text = head + ("\n" + "열: " + c.header if c.header else "") + "\n" + c.text
            doc_nodes.append(dict(id=str(uuid.uuid4()), document_id=d["id"], kind="chunk", position=position,
                                  locator=c.locator, text=c.text, context=None, index_text=index_text,
                                  bigrams=bm25_tokens(index_text), token_estimate=c.tokens,
                                  source_block_ids=[c.block_id]))
        for a, b in zip(doc_nodes, doc_nodes[1:]):
            a["next_id"], b["prev_id"] = b["id"], a["id"]
        nodes.extend(doc_nodes)
    return nodes


def header(project: str, role: str, title: str, file_name: str | None, loc: dict | None) -> str:
    """The fixed metadata header of an index text. Without a file it is the submission summary's header."""
    if file_name is None:
        return f"[{project} / {role} / {title} / 제출물 요약]"
    return f"[{project} / {role} / {title} / {file_name} / {where(loc)}]"


def where(loc: dict) -> str:
    """A short Korean label of a locator for headers and citations: `3쪽`, `슬라이드 2`, `시트 매출 2~40행`."""
    if "summary" in loc:
        return "제출물 요약"
    if "page" in loc:
        s = f"{loc['page']}쪽"
    elif "slide" in loc:
        s = f"슬라이드 {loc['slide']}" + (f" 이미지 {loc['image']}" if "image" in loc else "")
    elif "sheet" in loc:
        rows = loc.get("rows") or [None, None]
        s = f"시트 {loc['sheet']}" + (f" {rows[0]}~{rows[1]}행" if rows[0] else "")
    elif "table" in loc:
        s = f"표 {loc['table']}"
    elif loc.get("section"):
        s = str(loc["section"])
    else:
        s = "본문"
    return s + (f" ({loc['part']})" if "part" in loc else "")
