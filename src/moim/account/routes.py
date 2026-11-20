"""Stage 6 pages and API: mypage (my info, password, my groups, account), leaving a group, deleting the account.

Every route needs a signed-in user; the user id comes from the session only. State changes are POSTs with CSRF.
Irreversible actions (leave, delete) run only with `confirm: true`; without it they change nothing and answer 409 with
the impact to show (D-D1 D6/D7, D-F1). A wrong current password is counted per user (`REAUTH_USER`); a locked key
refuses even the right password, so the lock is no oracle.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..auth import csrf, throttle as th
from ..auth.deps import current_user, require_user, session_cookie_name, verify_csrf
from ..auth.errors import AuthError
from ..auth.sessions import SessionStore, SessionUser
from ..auth.throttle import Throttle
from ..auth.validation import FieldError, validate_password
from ..config import Settings
from .lifecycle import LifecycleError, LifecycleService
from .service import MSG_PW_MISMATCH, MSG_PW_WRONG, AccountError, AccountService

log = logging.getLogger("moim.account.routes")

NO_STORE = {"Cache-Control": "no-store"}
MSG_TOO_MANY = "시도가 너무 많아요. 잠시 후 다시 시도해주세요."
MSG_LEAVE_PW_WRONG = "비밀번호가 맞지 않아요."  # the original deletion panel's hint


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileBody(_Body):
    name: str = Field(max_length=256)


class PasswordBody(_Body):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)
    new_password_confirm: str = Field(max_length=1024)


class DeleteBody(_Body):
    password: str = Field(max_length=1024)
    confirm: StrictBool = False


class LeaveBody(_Body):
    confirm: StrictBool = False


def make_router(settings: Settings, account: AccountService, lifecycle: LifecycleService, sessions: SessionStore,
                throttle: Throttle, templates: Jinja2Templates, on_assigned=None) -> APIRouter:
    router = APIRouter()
    secure = settings.secure_cookies
    sess_cookie = session_cookie_name(secure)

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    def too_many(seconds: int) -> AuthError:
        return AuthError(429, "RATE_LIMITED", MSG_TOO_MANY, retry_after=seconds)

    def reauth(user: SessionUser, password: str, field: str, message: str) -> str:
        """The checked hash, or an error. Failures count; a lock refuses even the right password."""
        locked = throttle.locked_for([(th.REAUTH_USER, user.user_id)])
        if locked:
            raise too_many(locked)
        ok, checked = account.check_password(user.user_id, password)
        if not ok:
            locked = throttle.hit(th.REAUTH_USER, user.user_id)
            log.info("account_event kind=REAUTH_FAILED user_id=%s", user.user_id)
            if locked:
                raise too_many(locked)
            raise AccountError(401, "WRONG_PASSWORD", message, field=field)
        throttle.clear(th.REAUTH_USER, user.user_id)
        return checked

    def end_session(response: Response) -> None:
        response.delete_cookie(sess_cookie, path="/", httponly=True, samesite="lax", secure=secure)
        csrf.set_cookie(response, csrf.new_token(), secure)

    def schedule(background: BackgroundTasks, assigned) -> None:
        if on_assigned is not None:
            for gid, rev in assigned:
                background.add_task(on_assigned, gid, rev)

    # ---- page ---------------------------------------------------------------------------------------------------------

    @router.get("/mypage", include_in_schema=False)
    def mypage(request: Request):
        user = current_user(request)
        if user is None:
            return RedirectResponse("/login?next=/mypage", status_code=303)
        token = csrf.current_token(request, secure) or csrf.new_token()
        response = templates.TemplateResponse(request, "mypage.html", {"csrf_token": token, **account.overview(user.user_id)},
                                              headers=NO_STORE)
        csrf.set_cookie(response, token, secure)
        return response

    # ---- my info ------------------------------------------------------------------------------------------------------

    @router.get("/api/account")
    def overview(user: SessionUser = Depends(require_user)):
        return json_ok(account.overview(user.user_id))

    @router.post("/api/account/profile", dependencies=[Depends(verify_csrf)])
    def profile(body: ProfileBody, user: SessionUser = Depends(require_user)):
        try:
            name = account.update_name(user.user_id, body.name)
        except FieldError as exc:
            raise AccountError(422, "INVALID", exc.message, field="name") from None
        return json_ok({"name": name})

    # ---- password -----------------------------------------------------------------------------------------------------

    @router.post("/api/account/password", dependencies=[Depends(verify_csrf)])
    def change_password(body: PasswordBody, request: Request, user: SessionUser = Depends(require_user)):
        if body.new_password != body.new_password_confirm:
            raise AccountError(422, "INVALID", MSG_PW_MISMATCH, field="new_password_confirm")
        try:
            validate_password(body.new_password)  # before the password check, so a bad new value costs no attempt
        except FieldError as exc:
            raise AccountError(422, "INVALID", exc.message, field="new_password") from None
        checked = reauth(user, body.current_password, "current_password", MSG_PW_WRONG)
        account.set_password(user.user_id, checked, body.new_password)
        # Every session of this account ends (other devices included); this device gets a fresh one, same "keep".
        sessions.revoke_all(user.user_id)
        token, max_age = sessions.create(user.user_id, user.keep)
        fresh = csrf.new_token()  # rotated with the privilege change; the page cannot read the HttpOnly cookie
        response = json_ok({"ok": True, "csrf_token": fresh})
        response.set_cookie(sess_cookie, token, max_age=max_age, httponly=True, samesite="lax", secure=secure, path="/")
        csrf.set_cookie(response, fresh, secure)
        return response

    # ---- leave one group (D6) -----------------------------------------------------------------------------------------

    @router.get("/api/groups/{group_id}/leave")
    def leave_preview(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok({"impact": lifecycle.leave_preview(user.user_id, group_id)})

    @router.post("/api/groups/{group_id}/leave", dependencies=[Depends(verify_csrf)])
    def leave(group_id: str, body: LeaveBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        impact, out = lifecycle.leave_group(user.user_id, group_id, body.confirm)
        schedule(background, out.assigned)
        return json_ok({"left": True, "group_deleted": bool(out.purged_groups), "redirect": "/dashboard"})

    # ---- the leader deletes the group (D-H1) -----------------------------------------------------------------------

    @router.get("/api/groups/{group_id}/delete")
    def delete_group_preview(group_id: str, user: SessionUser = Depends(require_user)):
        return json_ok({"impact": lifecycle.delete_group_preview(user.user_id, group_id)})

    @router.post("/api/groups/{group_id}/delete", dependencies=[Depends(verify_csrf)])
    def delete_group(group_id: str, body: LeaveBody, user: SessionUser = Depends(require_user)):
        lifecycle.delete_group(user.user_id, group_id, body.confirm)
        return json_ok({"deleted": True, "redirect": "/dashboard"})

    # ---- delete the account (D7) --------------------------------------------------------------------------------------

    @router.get("/api/account/delete")
    def delete_preview(user: SessionUser = Depends(require_user)):
        return json_ok({"impact": lifecycle.delete_preview(user.user_id)})

    @router.post("/api/account/delete", dependencies=[Depends(verify_csrf)])
    def delete_account(body: DeleteBody, background: BackgroundTasks, user: SessionUser = Depends(require_user)):
        reauth(user, body.password, "password", MSG_LEAVE_PW_WRONG)
        if not body.confirm:
            raise LifecycleError(409, "CONFIRM_REQUIRED", "확인이 필요해요.",
                                 extra={"impact": lifecycle.delete_preview(user.user_id)})
        out = lifecycle.delete_account(user.user_id)
        schedule(background, out.assigned)
        response = json_ok({"deleted": True, "redirect": "/"})
        end_session(response)
        return response

    return router
