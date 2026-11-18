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


def make_router(
    settings: Settings,
    groups: GroupService,
    goals: GoalService,
    candidates: CandidateService,
    ranking: RankingService,
    templates: Jinja2Templates,
) -> APIRouter:
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
    def submit(group_id: str, body: PreferenceBody, user: SessionUser = Depends(require_user)):
        stored = ranking.submit_preferences(user.user_id, group_id, body.action_id, body.candidate_revision, body.ranks)
        return json_ok(screen(user, group_id), 201 if stored else 200)

    return router
