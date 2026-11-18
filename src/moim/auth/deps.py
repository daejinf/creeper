"""FastAPI dependencies other stages reuse: the signed-in user and CSRF verification."""
from __future__ import annotations

from fastapi import Request

from . import csrf
from .errors import AuthError
from .sessions import SessionStore, SessionUser


def session_cookie_name(secure: bool) -> str:
    return "__Host-moim_session" if secure else "moim_session"


def current_user(request: Request) -> SessionUser | None:
    """The signed-in user from the session cookie, or None. Looked up once per request."""
    cached = getattr(request.state, "auth_user", False)
    if cached is not False:
        return cached
    store: SessionStore = request.app.state.sessions
    secure = request.app.state.settings.secure_cookies
    user = store.lookup(request.cookies.get(session_cookie_name(secure)))
    request.state.auth_user = user
    return user


def require_user(request: Request) -> SessionUser:
    user = current_user(request)
    if user is None:
        raise AuthError(401, "UNAUTHENTICATED", "로그인이 필요해요.")
    return user


def verify_csrf(request: Request) -> None:
    csrf.verify(request, request.app.state.settings.secure_cookies)
