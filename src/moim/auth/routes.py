"""Auth pages and API: landing, signup, login, logout, username check."""
from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import users
from ..timeutil import utcnow
from . import csrf, passwords, throttle as th
from .deps import current_user, session_cookie_name, verify_csrf
from .errors import AuthError
from .sessions import SessionStore
from .throttle import Throttle
from .validation import (
    MSG_PASSWORD_MISMATCH,
    MSG_TERMS,
    MSG_USERNAME_OK,
    MSG_USERNAME_TAKEN,
    FieldError,
    is_reserved,
    normalize_username,
    validate_name,
    validate_password,
    validate_username,
)

log = logging.getLogger("moim.auth")

DEFAULT_NEXT = "/dashboard"
MSG_LOGIN_FAILED = "아이디 또는 비밀번호가 올바르지 않아요."
MSG_TOO_MANY = "시도가 너무 많아요. 잠시 후 다시 시도해주세요."
NO_STORE = {"Cache-Control": "no-store"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsernameCheckBody(_Body):
    username: str = Field(max_length=256)


class SignupBody(_Body):
    name: str = Field(max_length=256)
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)
    password_confirm: str = Field(max_length=1024)
    terms: StrictBool = False
    notifications: StrictBool = False


class LoginBody(_Body):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)
    keep: StrictBool = False
    next: str | None = Field(default=None, max_length=512)


def safe_next(raw: str | None) -> str:
    """Only same-site absolute paths. Anything else (scheme, host, //, backslash, control characters) becomes the default."""
    if not raw or len(raw) > 200 or not raw.startswith("/") or raw.startswith("//"):
        return DEFAULT_NEXT
    if "\\" in raw or any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        return DEFAULT_NEXT
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return DEFAULT_NEXT
    return raw


def client_ip(request: Request) -> str:
    # No proxy headers are trusted: X-Forwarded-For is client-controlled. A reverse proxy needs explicit config (stage 7).
    return request.client.host if request.client else "unknown"


def make_router(
    settings: Settings,
    session_factory: sessionmaker[Session],
    sessions: SessionStore,
    throttle: Throttle,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()
    secure = settings.secure_cookies
    sess_cookie = session_cookie_name(secure)

    # ---- helpers ----------------------------------------------------------------------------

    def page(request: Request, name: str, **ctx) -> Response:
        token = csrf.current_token(request, secure) or csrf.new_token()
        response = templates.TemplateResponse(request, name, {"csrf_token": token, **ctx}, headers=NO_STORE)
        csrf.set_cookie(response, token, secure)
        return response

    def start_session(response: Response, user_id: str, keep: bool, old_token: str | None) -> None:
        sessions.revoke(old_token)  # a presented token is never reused: no session fixation
        token, max_age = sessions.create(user_id, keep)
        response.set_cookie(sess_cookie, token, max_age=max_age, httponly=True, samesite="lax", secure=secure, path="/")
        csrf.set_cookie(response, csrf.new_token(), secure)  # rotate the CSRF token with the privilege change

    def json_ok(payload: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=NO_STORE)

    def too_many(seconds: int) -> AuthError:
        return AuthError(429, "RATE_LIMITED", MSG_TOO_MANY, retry_after=seconds)

    def field_error(exc: FieldError) -> AuthError:
        return AuthError(422, "INVALID", exc.message, field=exc.field)

    def username_taken(username: str) -> bool:
        if is_reserved(username):
            return True
        with session_factory() as s:
            return s.execute(select(users.c.id).where(users.c.username == username)).first() is not None

    # ---- pages ------------------------------------------------------------------------------

    @router.get("/", include_in_schema=False)
    def landing(request: Request):
        # D-A5: the bottom CTA goes to login when signed out (success returns to the dashboard) and to the dashboard when signed in.
        user = current_user(request)
        return page(request, "landing.html", cta_href=DEFAULT_NEXT if user else "/login")

    @router.get("/login", include_in_schema=False)
    def login_page(request: Request, next: str | None = None):
        if current_user(request):
            return RedirectResponse(safe_next(next), status_code=303)
        return page(request, "login.html", next=safe_next(next))

    @router.get("/signup", include_in_schema=False)
    def signup_page(request: Request):
        if current_user(request):
            return RedirectResponse(DEFAULT_NEXT, status_code=303)
        return page(request, "signup.html")

    # ---- API --------------------------------------------------------------------------------

    @router.post("/api/auth/username-check", dependencies=[Depends(verify_csrf)])
    def username_check(body: UsernameCheckBody, request: Request):
        locked = throttle.hit(th.CHECK_IP, client_ip(request))
        if locked:
            raise too_many(locked)
        try:
            username = validate_username(body.username)
        except FieldError as exc:
            return json_ok({"status": "invalid", "message": exc.message})
        if username_taken(username):
            return json_ok({"status": "taken", "message": MSG_USERNAME_TAKEN})
        return json_ok({"status": "available", "message": MSG_USERNAME_OK})

    @router.post("/api/auth/signup", dependencies=[Depends(verify_csrf)])
    def signup(body: SignupBody, request: Request):
        locked = throttle.hit(th.SIGNUP_IP, client_ip(request))
        if locked:
            raise too_many(locked)
        try:
            name = validate_name(body.name)
            username = validate_username(body.username)
            password = validate_password(body.password)
        except FieldError as exc:
            raise field_error(exc)
        if body.password != body.password_confirm:
            raise AuthError(422, "INVALID", MSG_PASSWORD_MISMATCH, field="password_confirm")
        if not body.terms:
            raise AuthError(422, "INVALID", MSG_TERMS, field="terms")
        if username_taken(username):
            raise AuthError(409, "USERNAME_TAKEN", MSG_USERNAME_TAKEN, field="username")
        user_id = str(uuid.uuid4())
        try:
            with session_factory.begin() as s:
                s.execute(
                    insert(users).values(
                        id=user_id,
                        username=username,
                        display_name=name,
                        password_hash=passwords.hash_password(password),
                        notify_opt_in=body.notifications,
                        terms_accepted_at=utcnow(),
                    )
                )
        except IntegrityError:  # lost a race for the same username
            raise AuthError(409, "USERNAME_TAKEN", MSG_USERNAME_TAKEN, field="username") from None
        throttle.purge()
        response = json_ok({"redirect": DEFAULT_NEXT}, status=201)
        start_session(response, user_id, keep=False, old_token=request.cookies.get(sess_cookie))
        log.info("auth.signup user_id=%s", user_id)
        return response

    @router.post("/api/auth/login", dependencies=[Depends(verify_csrf)])
    def login(body: LoginBody, request: Request):
        ip = client_ip(request)
        username = normalize_username(body.username).lower()[:64]
        pair = f"{username}|{ip}"
        keys = [(th.LOGIN_PAIR, pair), (th.LOGIN_IP, ip), (th.LOGIN_USER, username)]
        locked = throttle.locked_for(keys)
        if locked:  # a locked key rejects even the correct password, so a lock is no oracle
            raise too_many(locked)

        with session_factory() as s:
            row = s.execute(
                select(users.c.id, users.c.password_hash).where(users.c.username == username)
            ).first()
        too_long = len(body.password) > 128
        if row is None or too_long:
            passwords.burn_verify(body.password)  # same Argon2 cost whether or not the account exists
            valid = False
        else:
            valid = passwords.verify_password(row.password_hash, body.password)

        if not valid:
            locked = max(throttle.hit(rule, ident) for rule, ident in keys)
            log.info("auth.login.failed")
            if locked:
                raise too_many(locked)
            raise AuthError(401, "LOGIN_FAILED", MSG_LOGIN_FAILED)

        throttle.clear(th.LOGIN_PAIR, pair)
        if passwords.needs_rehash(row.password_hash):
            with session_factory.begin() as s:
                s.execute(
                    update(users).where(users.c.id == row.id).values(password_hash=passwords.hash_password(body.password))
                )
        response = json_ok({"redirect": safe_next(body.next)})
        start_session(response, row.id, keep=body.keep, old_token=request.cookies.get(sess_cookie))
        log.info("auth.login.ok user_id=%s", row.id)
        return response

    @router.post("/api/auth/logout", dependencies=[Depends(verify_csrf)])
    def logout(request: Request):
        sessions.revoke(request.cookies.get(sess_cookie))
        response = json_ok({"redirect": "/login"})
        response.delete_cookie(sess_cookie, path="/", httponly=True, samesite="lax", secure=secure)
        csrf.set_cookie(response, csrf.new_token(), secure)
        return response

    return router
