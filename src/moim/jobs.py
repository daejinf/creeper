"""Persisted AI job state: PENDING -> RUNNING -> SUCCEEDED | FAILED.

No worker process, lease or crash model. FastAPI runs a job as a background task in the same process; the
state lives in the `ai_jobs` table. On server start anything still PENDING/RUNNING is marked FAILED
(SERVER_RESTARTED) so the user can retry it. Every transition is a compare-and-set on `revision`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .models import ai_jobs

log = logging.getLogger("moim.jobs")

PENDING, RUNNING, SUCCEEDED, FAILED = "PENDING", "RUNNING", "SUCCEEDED", "FAILED"
GENERIC_FAILURE = "작업을 처리하지 못했습니다. 다시 시도해주세요."
RESTART_FAILURE = "서버가 다시 시작되어 작업이 중단되었습니다. 다시 시도해주세요."
Handler = Callable[[dict], dict]


class JobError(Exception):
    """A handler's expected failure. `message` is shown to the user, so it must not contain private input."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class OperationConflict(Exception):
    """Same operation_id was reused with a different kind or input."""


class RevisionConflict(Exception):
    """The job is not in the expected state/revision (someone else changed it first)."""


@dataclass(frozen=True)
class Job:
    id: str
    operation_id: str
    kind: str
    state: str
    revision: int
    attempt: int
    input: dict
    result: dict | None
    error_code: str | None
    error_message: str | None


def _job(row) -> Job:
    return Job(row.id, row.operation_id, row.kind, row.state, row.revision, row.attempt, row.input,
               row.result, row.error_code, row.error_message)


def input_hash(kind: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "input": payload}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sessions = session_factory

    def _transition(self, job_id: str, revision: int, from_state: str, **values) -> Job:
        stmt = (
            update(ai_jobs)
            .where(ai_jobs.c.id == job_id, ai_jobs.c.revision == revision, ai_jobs.c.state == from_state)
            .values(revision=ai_jobs.c.revision + 1, updated_at=func.now(), **values)
            .returning(*ai_jobs.c)
        )
        with self._sessions.begin() as session:
            row = session.execute(stmt).one_or_none()
        if row is None:
            raise RevisionConflict(f"job {job_id} is not {from_state} at revision {revision}")
        return _job(row)

    def get(self, job_id: str) -> Job | None:
        with self._sessions() as session:
            row = session.execute(select(ai_jobs).where(ai_jobs.c.id == job_id)).one_or_none()
        return _job(row) if row else None

    def submit(self, operation_id: str, kind: str, payload: dict) -> tuple[Job, bool]:
        """Store the input and return (job, created). A repeated operation_id never creates a second job."""
        digest = input_hash(kind, payload)
        stmt = (
            insert(ai_jobs)
            .values(id=str(uuid4()), operation_id=operation_id, kind=kind, input_hash=digest, state=PENDING, input=payload)
            .on_conflict_do_nothing(index_elements=["operation_id"])
            .returning(*ai_jobs.c)
        )
        with self._sessions.begin() as session:
            row = session.execute(stmt).one_or_none()
            if row is not None:
                return _job(row), True
            existing = session.execute(select(ai_jobs).where(ai_jobs.c.operation_id == operation_id)).one()
        if existing.input_hash != digest:
            raise OperationConflict(operation_id)
        return _job(existing), False

    def claim(self, job_id: str, revision: int) -> Job:
        return self._transition(job_id, revision, PENDING, state=RUNNING, started_at=func.now())

    def complete(self, job_id: str, revision: int, result: dict) -> Job:
        return self._transition(job_id, revision, RUNNING, state=SUCCEEDED, result=result, finished_at=func.now())

    def fail(self, job_id: str, revision: int, code: str, message: str) -> Job:
        return self._transition(job_id, revision, RUNNING, state=FAILED, error_code=code[:64],
                                error_message=message[:500], finished_at=func.now())

    def retry(self, job_id: str, revision: int) -> Job:
        """FAILED -> PENDING as a new attempt. Only the current revision of a FAILED job can be retried."""
        return self._transition(job_id, revision, FAILED, state=PENDING, attempt=ai_jobs.c.attempt + 1, result=None,
                                error_code=None, error_message=None, started_at=None, finished_at=None)

    def fail_interrupted(self) -> int:
        """Server start: work left PENDING/RUNNING lost its background task, so mark it FAILED (retryable)."""
        stmt = (
            update(ai_jobs)
            .where(ai_jobs.c.state.in_((PENDING, RUNNING)))
            .values(state=FAILED, error_code="SERVER_RESTARTED", error_message=RESTART_FAILURE,
                    revision=ai_jobs.c.revision + 1, updated_at=func.now(), finished_at=func.now())
        )
        with self._sessions.begin() as session:
            return session.execute(stmt).rowcount


def run_job(store: JobStore, handlers: Mapping[str, Handler], job_id: str) -> None:
    """Run one PENDING job to a stored final state. Safe to call twice: only one caller can claim it."""
    job = store.get(job_id)
    if job is None or job.state != PENDING:
        return
    try:
        running = store.claim(job.id, job.revision)
    except RevisionConflict:
        return
    log.info("job_event job_id=%s kind=%s state=%s attempt=%d", running.id, running.kind, RUNNING, running.attempt)
    handler = handlers.get(running.kind)
    try:
        if handler is None:
            raise JobError("UNKNOWN_KIND", GENERIC_FAILURE)
        result = handler(running.input)
        if not isinstance(result, dict):
            raise TypeError("handler result must be a dict")
        final = store.complete(running.id, running.revision, result)
    except JobError as exc:
        final = _finish_failed(store, running, exc.code, exc.message)
    except RevisionConflict:
        log.warning("job_event job_id=%s kind=%s state=LOST reason=revision_conflict", running.id, running.kind)
        return
    except Exception as exc:  # the type name only: exception text may hold private input
        log.error("job_event job_id=%s kind=%s state=ERROR error_type=%s", running.id, running.kind, type(exc).__name__)
        final = _finish_failed(store, running, "HANDLER_ERROR", GENERIC_FAILURE)
    if final is not None:
        log.info("job_event job_id=%s kind=%s state=%s attempt=%d", final.id, final.kind, final.state, final.attempt)


def _finish_failed(store: JobStore, running: Job, code: str, message: str) -> Job | None:
    try:
        return store.fail(running.id, running.revision, code, message)
    except RevisionConflict:
        return None
