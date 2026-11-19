"""Stage 3 chat page and API: the group's shared role candidates and the member's private ranking submission (D-B3).

Every route is for a member of the group's ACTIVE cohort and for that member's own data only: the user id always comes
from the session, never from the URL or the body. A non-member and a missing group look identical (404).
State changes are POSTs with CSRF and a client-made action id, so a double click or a replay changes nothing twice.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from ..auth import csrf
from ..auth.deps import current_user, require_user, verify_csrf
from ..auth.errors import AuthError
from ..auth.sessions import SessionUser
from ..config import Settings
from ..groups.goals import GoalService
from ..groups.routes import dday_label
from ..groups.service import GroupError, GroupService, today_kst
from ..timeutil import utcnow
from .candidates import FAILED, CandidateService
from .ranking import RankingService

log = logging.getLogger("moim.interview.routes")

NO_STORE = {"Cache-Control": "no-store"}
MSG_NOT_RETRYABLE = "지금은 다시 시도할 수 없어요. 새로고침해서 상태를 확인해 주세요."


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateRetryBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    candidate_revision: int = Field(ge=1, le=1_000_000)
    expected_attempt: int = Field(ge=1, le=1_000_000)


class PreferenceBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    candidate_revision: int = Field(ge=1, le=1_000_000)
    ranks: list[str] = Field(min_length=1, max_length=8)


class ActionBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)


class AnswerBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    message: str | None = Field(default=None, max_length=1000)
    question_id: str | None = Field(default=None, max_length=64)
    option_id: str | None = Field(default=None, max_length=64)


class TurnRetryBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    seq: int = Field(ge=0, le=40)
    expected_attempt: int = Field(ge=1, le=1_000_000)


class CoverageRetryBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    assignment_revision: int = Field(ge=1, le=1_000_000)
    expected_attempt: int = Field(ge=1, le=1_000_000)


class ReassignRequestBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    assignment_revision: int = Field(ge=1, le=1_000_000)
    target_user_id: str = Field(min_length=1, max_length=36)


class ReassignRespondBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    accept: bool


class TodoStartBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)


class TodoEditBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    draft_revision: int = Field(ge=1, le=1_000_000)
    op: str = Field(max_length=8)
    key: str | None = Field(default=None, max_length=8)
    title: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=10)


class TodoReviseBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    message: str = Field(max_length=1000)


class TodoConfirmBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    draft_revision: int = Field(ge=1, le=1_000_000)


class TodoRetryBody(_Body):
    action_id: str = Field(min_length=8, max_length=64)
    seq: int = Field(ge=1, le=60)
    expected_attempt: int = Field(ge=1, le=1_000_000)


def make_router(
    settings: Settings,
    groups: GroupService,
    goals: GoalService,
    candidates: CandidateService,
    ranking: RankingService,
    templates: Jinja2Templates,
    coverage=None,
    role_interview=None,
    todos=None,
    reassign=None,
) -> APIRouter:
    """`coverage` (CoverageService), `role_interview` (RoleInterviewService), `todos` (TodoService) and `reassign`
    (ReassignmentService) are the stage 4 services."""
    router = APIRouter()
    secure = settings.secure_cookies

    def page(request: Request, template: str, status: int = 200, **ctx) -> Response:
        token = csrf.current_token(request, secure) or csrf.new_token()
        response = templates.TemplateResponse(request, template, {"csrf_token": token, **ctx}, status_code=status, headers=NO_STORE)
        csrf.set_cookie(response, token, secure)
        return response

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    def screen(user: SessionUser, group_id: str) -> dict:
        """The whole chat payload: one read for the candidates, the member's own ranking state and the submitted count."""
        g, brief, is_leader, _ = groups.current_brief(user.user_id, group_id)  # cohort members of an ACTIVE group only
        data = ranking.view(user.user_id, group_id)
        cand = candidates.view(group_id)
        # Any cohort member may retry a failed candidate set: it blocks everyone's ranking, and a retry is one logged call.
        cand["can_retry"] = cand["state"] == FAILED
        data["candidates"] = cand
        goal = goals.view(group_id)
        data["group"] = {
            "name": brief.name,
            "dday": dday_label(brief.end_date, today_kst(utcnow())),
            "end_date": brief.end_date.isoformat(),
            "goal": {"state": goal["state"], "text": goal["text"]},
        }
        data["ranking_available"] = bool(cand["state"] == "SUCCEEDED" and data["phase"] == "INTERVIEW")
        # Stage 4: the caller's own assignment and interview only (D-B1 B6), after the server assignment.
        data["role"] = role_interview.role_view(user.user_id, group_id, user.display_name) if data["phase"] == "ROLE_TODOS" else None
        return data

    # ---- page -------------------------------------------------------------------------------

    @router.get("/groups/{group_id}/chat", include_in_schema=False)
    def chat_page(group_id: str, request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse(f"/login?next={quote(request.url.path, safe='')}", status_code=303)
        try:
            gate = groups.cohort_has(user.user_id, group_id)
        except GroupError as exc:
            titles = {404: "찾을 수 없어요", 403: "접근할 수 없어요"}
            return page(request, "notice.html", exc.status, title=titles.get(exc.status, "잠시 문제가 생겼어요"), message=exc.message)
        if gate == "forming":
            return RedirectResponse("/invite", status_code=303)
        if gate == "cancelled":
            return RedirectResponse("/dashboard", status_code=303)
        if gate != "ok":
            return page(request, "notice.html", 403, title="접근할 수 없어요", message="이 그룹의 참여 구성원만 들어올 수 있어요.")
        return page(request, "chat.html", group_id=group_id, display_name=user.display_name)

    # ---- API --------------------------------------------------------------------------------

    @router.get("/api/groups/{group_id}/roles")
    def get_screen(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok(screen(user, group_id))

    @router.post("/api/groups/{group_id}/roles/candidates/retry", dependencies=[Depends(verify_csrf)])
    def retry_candidates(group_id: str, body: CandidateRetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        groups.current_brief(user.user_id, group_id)  # members of the ACTIVE cohort only
        outcome = candidates.retry(group_id, body.candidate_revision, body.expected_attempt, body.action_id)
        if outcome == "refused":
            raise AuthError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
        if outcome == "started":
            background.add_task(candidates.run, group_id, body.candidate_revision)
        return json_ok(screen(user, group_id), 202)

    @router.post("/api/groups/{group_id}/preferences", dependencies=[Depends(verify_csrf)])
    def submit(group_id: str, body: PreferenceBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        stored, assigned = ranking.submit_preferences(user.user_id, group_id, body.action_id, body.candidate_revision, body.ranks)
        if assigned is not None:  # this submission completed the cohort: the team coverage is made once, in the background
            background.add_task(coverage.run, group_id, assigned)
        return json_ok(screen(user, group_id), 201 if stored else 200)

    # ---- stage 4: post-assignment interview and team coverage ---------------------------------

    @router.post("/api/groups/{group_id}/role-interview/start", dependencies=[Depends(verify_csrf)])
    def interview_start(group_id: str, body: ActionBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev = role_interview.start(user.user_id, group_id, body.action_id)
        if outcome == "started":
            background.add_task(role_interview.run, group_id, user.user_id, rev, 0)
        return json_ok(screen(user, group_id), 202 if outcome == "started" else 200)

    @router.post("/api/groups/{group_id}/role-interview/answer", dependencies=[Depends(verify_csrf)])
    def interview_answer(group_id: str, body: AnswerBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev, seq = role_interview.answer(
            user.user_id, group_id, body.action_id, body.message, body.question_id, body.option_id
        )
        if outcome == "started":
            background.add_task(role_interview.run, group_id, user.user_id, rev, seq)
        return json_ok(screen(user, group_id), {"started": 202, "done": 201}.get(outcome, 200))

    @router.post("/api/groups/{group_id}/role-interview/retry", dependencies=[Depends(verify_csrf)])
    def interview_retry(group_id: str, body: TurnRetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev = role_interview.retry(user.user_id, group_id, body.seq, body.expected_attempt)
        if outcome == "refused":
            raise AuthError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
        if outcome == "started":
            background.add_task(role_interview.run, group_id, user.user_id, rev, body.seq)
        return json_ok(screen(user, group_id), 202)

    @router.post("/api/groups/{group_id}/coverage/retry", dependencies=[Depends(verify_csrf)])
    def coverage_retry(group_id: str, body: CoverageRetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        data = screen(user, group_id)  # cohort members only (404 otherwise)
        if data["phase"] != "ROLE_TODOS":
            raise AuthError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
        outcome = coverage.retry(group_id, body.assignment_revision, body.expected_attempt, body.action_id)
        if outcome == "refused":
            raise AuthError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
        if outcome == "started":
            background.add_task(coverage.run, group_id, body.assignment_revision)
        return json_ok(screen(user, group_id), 202)

    # ---- stage 4 session B: todo draft, edits, confirmation, group read ----------------------------------------

    @router.post("/api/groups/{group_id}/todos/start", dependencies=[Depends(verify_csrf)])
    def todos_start(group_id: str, body: TodoStartBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev = todos.start(user.user_id, group_id, body.action_id)
        if outcome == "started":
            background.add_task(todos.run, group_id, user.user_id, rev, 1)
        return json_ok(screen(user, group_id), 202 if outcome == "started" else 200)

    @router.post("/api/groups/{group_id}/todos/edit", dependencies=[Depends(verify_csrf)])
    def todos_edit(group_id: str, body: TodoEditBody, user: SessionUser = Depends(require_user)):
        outcome = todos.edit(user.user_id, group_id, body.action_id, body.draft_revision, body.op, body.key, body.title, body.due_date)
        return json_ok(screen(user, group_id), 201 if outcome == "done" else 200)

    @router.post("/api/groups/{group_id}/todos/revise", dependencies=[Depends(verify_csrf)])
    def todos_revise(group_id: str, body: TodoReviseBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev, seq = todos.revise(user.user_id, group_id, body.action_id, body.message)
        if outcome == "started":
            background.add_task(todos.run, group_id, user.user_id, rev, seq)
        return json_ok(screen(user, group_id), 202 if outcome == "started" else 200)

    @router.post("/api/groups/{group_id}/todos/confirm", dependencies=[Depends(verify_csrf)])
    def todos_confirm(group_id: str, body: TodoConfirmBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev, seq = todos.confirm(user.user_id, group_id, body.action_id, body.draft_revision)
        if outcome == "started":
            background.add_task(todos.run, group_id, user.user_id, rev, seq)
        return json_ok(screen(user, group_id), {"started": 202, "confirmed": 201}.get(outcome, 200))

    @router.post("/api/groups/{group_id}/todos/unlock", dependencies=[Depends(verify_csrf)])
    def todos_unlock(group_id: str, body: TodoStartBody, user: SessionUser = Depends(require_user)):
        outcome = todos.unlock(user.user_id, group_id, body.action_id)
        return json_ok(screen(user, group_id), 201 if outcome == "done" else 200)

    @router.post("/api/groups/{group_id}/todos/retry", dependencies=[Depends(verify_csrf)])
    def todos_retry(group_id: str, body: TodoRetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        outcome, rev = todos.retry(user.user_id, group_id, body.seq, body.expected_attempt)
        if outcome == "refused":
            raise AuthError(409, "NOT_RETRYABLE", MSG_NOT_RETRYABLE)
        if outcome == "started":
            background.add_task(todos.run, group_id, user.user_id, rev, body.seq)
        return json_ok(screen(user, group_id), 202)

    @router.get("/api/groups/{group_id}/todos")
    def group_todos(group_id: str, user: SessionUser = Depends(require_user)):
        """Every cohort member may read this: role name, todo title and due date of each confirmed member, nothing else.
        Rendering is stage 5."""
        return json_ok(todos.group_view(user.user_id, group_id))

    # ---- stage 4 session C: reassignment (server API only, no screen; D-B1 B4) ------------------------------------

    @router.post("/api/groups/{group_id}/reassignments", dependencies=[Depends(verify_csrf)])
    def reassign_request(group_id: str, body: ReassignRequestBody, user: SessionUser = Depends(require_user)):
        """The leader asks; nothing changes until the target consents. The body never carries a role."""
        stored, view = reassign.request(user.user_id, group_id, body.action_id, body.assignment_revision, body.target_user_id)
        return json_ok({"request": view}, 201 if stored else 200)

    @router.get("/api/groups/{group_id}/reassignments")
    def reassign_mine(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok(reassign.mine(user.user_id, group_id))

    @router.post("/api/groups/{group_id}/reassignments/{request_id}/respond", dependencies=[Depends(verify_csrf)])
    def reassign_respond(group_id: str, request_id: str, body: ReassignRespondBody, background: BackgroundTasks,
                         user: SessionUser = Depends(require_user)):
        """The target's own consent (accept) or refusal. Accepting writes the new assignment revision; its team
        coverage is made once in the background, like the first assignment."""
        outcome, rev = reassign.respond(user.user_id, group_id, request_id, body.action_id, body.accept)
        if outcome == "accepted":
            background.add_task(coverage.run, group_id, rev)
        return json_ok({"outcome": outcome, **screen(user, group_id)}, 201 if outcome in ("accepted", "declined") else 200)

    return router
