"""Stage 2 pages and API: dashboard, invite room, ProjectBrief form, group header, chat gate.

Pages redirect to the login screen when signed out; the JSON API answers 401. Every state change is a POST with CSRF.
A group that does not exist and a group the caller is not in look identical (404).
"""
from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from ..auth import csrf, throttle as th
from ..auth.deps import current_user, require_user, verify_csrf
from ..auth.errors import AuthError
from ..auth.routes import client_ip
from ..auth.sessions import SessionUser
from ..config import Settings
from ..timeutil import utcnow
from ..interview.candidates import CandidateService
from .goals import EMPTY, FAILED, GoalService
from .service import (
    ACTIVE,
    CANCELLED,
    FORMING,
    MAX_MEMBERS,
    MIN_MEMBERS_TO_CREATE,
    MSG_NEED_MEMBERS,
    GroupError,
    GroupService,
    today_kst,
)
from .validation import ActivateBody, UpdateBriefBody, validate_brief

log = logging.getLogger("moim.groups.routes")

NO_STORE = {"Cache-Control": "no-store"}
THUMBS = ("blue", "coral", "sun")
MSG_TOO_MANY = "시도가 너무 많아요. 잠시 후 다시 시도해주세요."


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyBody(_Body):
    pass


class JoinBody(_Body):
    code: str = Field(max_length=32)


class CancelBody(_Body):
    confirm: StrictBool = False


class GoalRetryBody(_Body):
    brief_revision: int = Field(ge=1, le=1_000_000)


def dday_label(end: date, today: date) -> str:
    d = (end - today).days
    return "D-Day" if d == 0 else f"D-{d}" if d > 0 else f"D+{-d}"


def member_view(m, me: str) -> dict:
    return {"name": m.name, "shape": m.shape, "is_me": m.user_id == me, "role": m.role}


def make_router(
    settings: Settings,
    groups: GroupService,
    goals: GoalService,
    candidates: CandidateService,
    throttle: th.Throttle,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()
    secure = settings.secure_cookies

    # ---- helpers ----------------------------------------------------------------------------

    def page(request: Request, template: str, status: int = 200, **ctx) -> Response:
        token = csrf.current_token(request, secure) or csrf.new_token()
        response = templates.TemplateResponse(
            request, template, {"csrf_token": token, **ctx}, status_code=status, headers=NO_STORE
        )
        csrf.set_cookie(response, token, secure)
        return response

    def notice(request: Request, status: int, title: str, message: str) -> Response:
        return page(request, "notice.html", status=status, title=title, message=message)

    def login_redirect(request: Request) -> RedirectResponse:
        target = request.url.path
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    def error_page(request: Request, exc: AuthError) -> Response:
        titles = {404: "찾을 수 없어요", 403: "접근할 수 없어요"}
        return notice(request, exc.status, titles.get(exc.status, "잠시 문제가 생겼어요"), exc.message)

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    def too_many(seconds: int) -> AuthError:
        return AuthError(429, "RATE_LIMITED", MSG_TOO_MANY, retry_after=seconds)

    def room_payload(user: SessionUser, group_id: str) -> dict:
        room = groups.room(user.user_id, group_id)
        count = len(room.members)
        payload = {
            "group_id": room.group_id,
            "status": room.status,
            "is_leader": room.is_leader,
            "members": [member_view(m, user.user_id) for m in room.members],
            "member_count": count,
            "max_members": MAX_MEMBERS,
            "can_create": room.is_leader and room.status == FORMING and count >= MIN_MEMBERS_TO_CREATE,
            "need_members_message": MSG_NEED_MEMBERS,
            "code": room.code,
            "code_expires_at": room.code_expires_at.isoformat() if room.code_expires_at else None,
            "code_expired": bool(room.code_expires_at and room.code_expires_at <= utcnow()),
            "next": f"/groups/{room.group_id}/chat" if room.status == ACTIVE else None,
        }
        return payload

    # ---- pages ------------------------------------------------------------------------------

    @router.get("/dashboard", include_in_schema=False)
    def dashboard(request: Request):
        user = current_user(request)
        if user is None:
            return login_redirect(request)
        today = today_kst(utcnow())
        cards = []
        for i, g in enumerate(groups.groups_for(user.user_id)):
            active = g.status == ACTIVE
            cards.append(
                {
                    "id": g.group_id,
                    "href": f"/groups/{g.group_id}" if active else "/invite",
                    "active": active,
                    "name": g.name if active else "새 그룹",
                    "badge": dday_label(g.end_date, today) if active else "초대 대기",
                    "thumb": THUMBS[i % len(THUMBS)],
                    "count": len(g.members),
                    "shapes": [m.shape for m in g.members],
                }
            )
        return page(
            request,
            "dashboard.html",
            display_name=user.display_name,
            cards=cards,
            active_count=sum(1 for c in cards if c["active"]),
        )

    @router.get("/invite", include_in_schema=False)
    def invite(request: Request):
        # Viewing never creates a group or a code: a creator without a room just sees the join form.
        user = current_user(request)
        if user is None:
            return login_redirect(request)
        group_id = groups.my_forming_room_id(user.user_id)
        if group_id is None:
            return page(request, "invite.html", mode="join", display_name=user.display_name, room=None, group_id=None)
        data = room_payload(user, group_id)
        return page(
            request,
            "invite.html",
            mode="leader" if data["is_leader"] else "waiting",
            display_name=user.display_name,
            room=data,
            group_id=group_id,
        )

    @router.get("/groups/{group_id}/create", include_in_schema=False)
    def create_page(group_id: str, request: Request):
        user = current_user(request)
        if user is None:
            return login_redirect(request)
        try:
            gate = groups.can_open_create(user.user_id, group_id)
        except GroupError as exc:
            return error_page(request, exc)
        if gate == "need_members":
            return RedirectResponse("/invite", status_code=303)
        if gate == "active":
            return RedirectResponse(f"/groups/{group_id}", status_code=303)
        if gate == "closed":
            return RedirectResponse("/dashboard", status_code=303)
        today = today_kst(utcnow()).isoformat()
        return page(
            request, "group_create.html", mode="create", group_id=group_id, today=today, brief=None, base_revision=None,
            display_name=user.display_name,
        )

    @router.get("/groups/{group_id}/brief/edit", include_in_schema=False)
    def edit_page(group_id: str, request: Request):
        user = current_user(request)
        if user is None:
            return login_redirect(request)
        try:
            g, brief, is_leader, _ = groups.current_brief(user.user_id, group_id)
        except GroupError as exc:
            if exc.code == "GROUP_NOT_ACTIVE":
                return RedirectResponse("/invite", status_code=303)
            return error_page(request, exc)
        if not is_leader:
            return error_page(request, GroupError(403, "FORBIDDEN", "그룹장만 프로젝트 정보를 수정할 수 있어요."))
        data = {
            "name": brief.name,
            "content": brief.content,
            "start_date": brief.start_date.isoformat(),
            "end_date": brief.end_date.isoformat(),
            "types": list(brief.deliverable_types),
            "milestones": list(brief.milestones),
        }
        return page(
            request, "group_create.html", mode="edit", group_id=group_id, today=today_kst(utcnow()).isoformat(),
            brief=data, base_revision=g.brief_revision, display_name=user.display_name,
        )

    @router.get("/groups/{group_id}", include_in_schema=False)
    def group_page(group_id: str, request: Request):
        user = current_user(request)
        if user is None:
            return login_redirect(request)
        try:
            g, brief, is_leader, members = groups.current_brief(user.user_id, group_id)
        except GroupError as exc:
            if exc.code == "GROUP_NOT_ACTIVE":
                return RedirectResponse("/invite", status_code=303)  # waiting room: no group screen before activation
            return error_page(request, exc)
        today = today_kst(utcnow())
        side = [
            {"id": s.group_id, "name": s.name, "href": f"/groups/{s.group_id}", "shape": s.members[0].shape if s.members else "c",
             "current": s.group_id == group_id}
            for s in groups.groups_for(user.user_id) if s.status == ACTIVE
        ]
        return page(
            request,
            "group.html",
            group_id=group_id,
            name=brief.name,
            dday=dday_label(brief.end_date, today),
            is_leader=is_leader,
            members=[member_view(m, user.user_id) for m in members],
            goal=goals.view(group_id),
            side=side,
            display_name=user.display_name,
        )

    # ---- API: invite room -------------------------------------------------------------------

    @router.post("/api/groups", dependencies=[Depends(verify_csrf)])
    def create_group(body: EmptyBody, user: SessionUser = Depends(require_user)):
        group_id, reused = groups.create_or_reuse_room(user.user_id)
        return json_ok({"group_id": group_id, "reused": reused, "redirect": "/invite"}, 200 if reused else 201)

    @router.get("/api/groups/{group_id}/room")
    def room(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok(room_payload(user, group_id))

    @router.post("/api/groups/join", dependencies=[Depends(verify_csrf)])
    def join(body: JoinBody, request: Request, user: SessionUser = Depends(require_user)):
        ip = client_ip(request)
        keys = [(th.JOIN_USER, user.user_id), (th.JOIN_IP, ip)]
        locked = throttle.locked_for(keys)
        if locked:  # a locked key refuses even a correct code, so the lock is no oracle
            raise too_many(locked)
        try:
            group_id, joined = groups.join_by_code(user.user_id, body.code.strip())
        except GroupError as exc:
            if exc.code == "INVALID_CODE":
                locked = max(throttle.hit(rule, ident) for rule, ident in keys)
                if locked:
                    raise too_many(locked) from None
            raise
        return json_ok({"group_id": group_id, "joined": joined, "redirect": "/invite"}, 201 if joined else 200)

    @router.post("/api/groups/{group_id}/code", dependencies=[Depends(verify_csrf)])
    def regenerate_code(group_id: str, body: EmptyBody, user: SessionUser = Depends(require_user)):
        groups.regenerate_code(user.user_id, group_id)
        return json_ok(room_payload(user, group_id))

    @router.post("/api/groups/{group_id}/cancel", dependencies=[Depends(verify_csrf)])
    def cancel(group_id: str, body: CancelBody, user: SessionUser = Depends(require_user)):
        if not body.confirm:
            raise AuthError(422, "CONFIRM_REQUIRED", "초대방을 닫으려면 확인이 필요해요.")
        groups.cancel_room(user.user_id, group_id)
        return json_ok({"status": CANCELLED, "redirect": "/dashboard"})

    # ---- API: ProjectBrief and activation ---------------------------------------------------

    def parse_brief(body):
        try:
            return validate_brief(body)
        except ValidationError:
            raise AuthError(400, "BAD_REQUEST", "요청 형식이 올바르지 않아요.") from None

    @router.post("/api/groups/{group_id}/activate", dependencies=[Depends(verify_csrf)])
    def activate(group_id: str, body: ActivateBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        brief = parse_brief(body)
        revision = groups.activate(user.user_id, group_id, brief)
        if brief.content.strip():
            background.add_task(goals.run, group_id, revision)
        background.add_task(candidates.run, group_id, revision)
        return json_ok({"status": ACTIVE, "brief_revision": revision, "redirect": f"/groups/{group_id}/chat"}, 201)

    @router.post("/api/groups/{group_id}/brief", dependencies=[Depends(verify_csrf)])
    def update_brief(group_id: str, body: UpdateBriefBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        brief = parse_brief(body)
        revision, changed = groups.update_brief(user.user_id, group_id, body.base_revision, brief)
        if changed and brief.content.strip():
            background.add_task(goals.run, group_id, revision)
        if changed:
            background.add_task(candidates.run, group_id, revision)
        return json_ok({"brief_revision": revision, "changed": changed, "redirect": f"/groups/{group_id}"})

    @router.get("/api/groups/{group_id}/brief")
    def get_brief(group_id: str, user: SessionUser = Depends(require_user)):
        g, brief, is_leader, _ = groups.current_brief(user.user_id, group_id)
        return json_ok(
            {
                "revision": g.brief_revision,
                "name": brief.name,
                "content": brief.content,
                "start_date": brief.start_date.isoformat(),
                "end_date": brief.end_date.isoformat(),
                "types": list(brief.deliverable_types),
                "milestones": list(brief.milestones),
                "is_leader": is_leader,
            }
        )

    # ---- API: team goal sentence ------------------------------------------------------------

    @router.get("/api/groups/{group_id}/goal")
    def goal(group_id: str, user: SessionUser = Depends(require_user)):
        _, _, is_leader, _ = groups.current_brief(user.user_id, group_id)  # members of the ACTIVE cohort only
        view = goals.view(group_id)
        view["can_retry"] = is_leader and view["state"] == FAILED
        return json_ok(view)

    @router.post("/api/groups/{group_id}/goal/retry", dependencies=[Depends(verify_csrf)])
    def goal_retry(group_id: str, body: GoalRetryBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        _, _, is_leader, _ = groups.current_brief(user.user_id, group_id)
        if not is_leader:
            raise GroupError(403, "FORBIDDEN", "그룹장만 다시 시도할 수 있어요.")
        if not goals.retry(group_id, body.brief_revision):
            raise GroupError(409, "NOT_RETRYABLE", "지금은 다시 시도할 수 없어요. 새로고침해서 상태를 확인해 주세요.")
        background.add_task(goals.run, group_id, body.brief_revision)
        view = goals.view(group_id)
        view["can_retry"] = False
        return json_ok(view, 202)

    return router
