"""HTTP surface for AI jobs: 202 + status URL, polling, retry.

Authentication, object-level authorization and CSRF are NOT here yet: no handler is registered until a
product stage adds one together with its auth checks (stage 1+), so a stock app accepts no kind at all.
"""
from __future__ import annotations

import json
from typing import Mapping

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .jobs import Handler, Job, JobStore, OperationConflict, RevisionConflict, run_job

MAX_INPUT_BYTES = 64 * 1024


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    kind: str = Field(min_length=1, max_length=64)
    input: dict


class RetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


def public_view(job: Job) -> dict:
    """What the browser may see. The stored input is never returned."""
    return {"job_id": job.id, "kind": job.kind, "state": job.state, "revision": job.revision, "attempt": job.attempt,
            "result": job.result, "error_code": job.error_code, "error_message": job.error_message,
            "status_url": f"/api/jobs/{job.id}"}


def make_router(store: JobStore, handlers: Mapping[str, Handler]) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.post("", status_code=202)
    def submit(body: SubmitBody, background: BackgroundTasks, response: Response):
        if body.kind not in handlers:
            raise HTTPException(422, "unknown job kind")
        if len(json.dumps(body.input, ensure_ascii=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise HTTPException(422, "input is too large")
        try:
            job, created = store.submit(body.operation_id, body.kind, body.input)
        except OperationConflict:
            raise HTTPException(409, "operation_id was already used with different input") from None
        response.headers["Location"] = f"/api/jobs/{job.id}"
        if created:
            background.add_task(run_job, store, handlers, job.id)
        else:
            response.status_code = 200  # same operation: report the existing job, start nothing
        return public_view(job)

    @router.get("/{job_id}")
    def status(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return public_view(job)

    @router.post("/{job_id}/retry", status_code=202)
    def retry(job_id: str, body: RetryBody, background: BackgroundTasks, response: Response):
        if store.get(job_id) is None:
            raise HTTPException(404, "job not found")
        try:
            job = store.retry(job_id, body.revision)
        except RevisionConflict:
            raise HTTPException(409, "job is not a failed job at that revision") from None
        response.headers["Location"] = f"/api/jobs/{job.id}"
        background.add_task(run_job, store, handlers, job.id)
        return public_view(job)

    return router
