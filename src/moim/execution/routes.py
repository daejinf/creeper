"""Stage 5 API: the group execution screen data, completion records with attachments, `되돌리기`, downloads and the
submitter's own M2 review status/retry, the team vote on a completion attempt (D-H1) and the dashboard data.

All routes need a signed-in current active member of the group (404 otherwise, a missing group included). State
changes are POSTs with CSRF. The upload is the only multipart route; its body cap is in `auth.middleware`.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from ..auth.deps import require_user, verify_csrf
from ..auth.sessions import SessionUser
from . import files as F
from .service import ExecutionError, ExecutionService

log = logging.getLogger("moim.execution.routes")

NO_STORE = {"Cache-Control": "no-store"}
FIELDS = {"action_id", "task_id", "confirmation_revision", "title", "memo", "marks_done", "files"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UndoBody(_Body):
    confirm_delete: bool = False


class ReviewRetryBody(_Body):
    expected_attempt: int = Field(ge=1, le=1_000_000)


class VoteBody(_Body):
    agree: StrictBool


def _bad() -> ExecutionError:
    return ExecutionError(400, "BAD_REQUEST", "요청 형식이 올바르지 않아요.")


def make_router(execution: ExecutionService) -> APIRouter:
    router = APIRouter()

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    def schedule(background: BackgroundTasks, record_id: str) -> None:
        attempt = execution.pending_attempt(record_id)
        if attempt is not None:
            background.add_task(execution.run_review, record_id, attempt)  # sync: run in the threadpool

    @router.get("/api/groups/{group_id}/execution")
    def view(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok(execution.view(user.user_id, group_id))

    @router.post("/api/groups/{group_id}/records", dependencies=[Depends(verify_csrf)])
    async def submit(group_id: str, request: Request, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        try:
            # One part more than allowed reaches the service, which answers TOO_MANY_FILES; beyond that the parser stops.
            form = await request.form(max_files=F.MAX_FILES + 1, max_fields=12, max_part_size=8 * 1024)
        except (MultiPartException, HTTPException, ValueError):
            raise _bad() from None
        try:
            if set(form.keys()) - FIELDS:
                raise _bad()
            def one(name: str) -> str | None:
                vals = form.getlist(name)
                if len(vals) > 1 or any(not isinstance(v, str) for v in vals):
                    raise _bad()
                return vals[0] if vals else None
            action_id = one("action_id") or ""
            task_id = one("task_id") or None
            raw_rev = one("confirmation_revision")
            try:
                rev = int(raw_rev) if raw_rev not in (None, "") else None
            except ValueError:
                raise _bad() from None
            marks = one("marks_done")
            if marks not in (None, "true", "false"):
                raise _bad()
            uploads = form.getlist("files")
            if any(not isinstance(u, UploadFile) for u in uploads):
                raise _bad()
            checked: list[F.CheckedFile] = []
            for u in uploads:
                data = await u.read(F.MAX_FILE_BYTES + 1)
                try:
                    checked.append(F.check_file(u.filename or "", data))
                except F.FileRejected as exc:
                    raise ExecutionError(422, exc.code, exc.message) from None
            view, created = await run_in_threadpool(
                execution.submit, user.user_id, group_id, action_id, task_id, rev, one("title"), one("memo"),
                marks == "true", checked)
        finally:
            await form.close()
        if view.get("review") and view["review"]["state"] == "PENDING":
            schedule(background, view["id"])
        return json_ok(view, 201 if created else 200)

    @router.post("/api/groups/{group_id}/records/{record_id}/undo", dependencies=[Depends(verify_csrf)])
    def undo(group_id: str, record_id: str, body: UndoBody, user: SessionUser = Depends(require_user)):
        n = execution.undo(user.user_id, group_id, record_id, body.confirm_delete)
        return json_ok({"deleted_files": n})

    @router.get("/api/groups/{group_id}/records/{record_id}/review")
    def review(group_id: str, record_id: str, user: SessionUser = Depends(require_user)):
        return json_ok(execution.review_status(user.user_id, group_id, record_id))

    @router.post("/api/groups/{group_id}/records/{record_id}/review/retry", dependencies=[Depends(verify_csrf)])
    def review_retry(group_id: str, record_id: str, body: ReviewRetryBody, background: BackgroundTasks,
                     user: SessionUser = Depends(require_user)):
        v = execution.retry_review(user.user_id, group_id, record_id, body.expected_attempt)
        schedule(background, record_id)
        return json_ok(v, 202)

    @router.post("/api/groups/{group_id}/records/{record_id}/vote", dependencies=[Depends(verify_csrf)])
    def vote(group_id: str, record_id: str, body: VoteBody, user: SessionUser = Depends(require_user)):
        return json_ok(execution.vote(user.user_id, group_id, record_id, body.agree))

    @router.get("/api/dashboard")
    def dashboard(user: SessionUser = Depends(require_user)):
        return json_ok(execution.dashboard(user.user_id))

    @router.get("/api/groups/{group_id}/files/{file_id}")
    def download(group_id: str, file_id: str, user: SessionUser = Depends(require_user)):
        path, name = execution.download(user.user_id, group_id, file_id)
        ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in name) or "file"
        return FileResponse(path, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        })

    return router
