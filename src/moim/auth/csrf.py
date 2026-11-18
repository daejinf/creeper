"""CSRF: double-submit token plus Origin / Fetch-Metadata checks.

The token lives in an HttpOnly cookie; pages embed the same value in a meta tag and scripts send it back in the
X-CSRF-Token header. A cross-site page can neither read the cookie nor the meta tag. Production cookies are
`__Host-` prefixed, so a sibling subdomain cannot plant a token.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import urlsplit

from fastapi import Request, Response

from .errors import AuthError

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
HEADER = "x-csrf-token"
MSG = "요청을 확인할 수 없어요. 새로고침 후 다시 시도해주세요."


def cookie_name(secure: bool) -> str:
    return "__Host-moim_csrf" if secure else "moim_csrf"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def set_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(cookie_name(secure), token, httponly=True, samesite="lax", secure=secure, path="/")


def current_token(request: Request, secure: bool) -> str | None:
    value = request.cookies.get(cookie_name(secure))
    return value if value and TOKEN_RE.fullmatch(value) else None


def verify(request: Request, secure: bool) -> None:
    """Raise 403 unless this is a same-origin request carrying the matching token."""
    fail = AuthError(403, "CSRF_FAILED", MSG)
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise fail
    origin = request.headers.get("origin")
    if origin is not None and (origin == "null" or urlsplit(origin).netloc != request.headers.get("host", "")):
        raise fail
    cookie = current_token(request, secure)
    sent = request.headers.get(HEADER, "")
    if not cookie or not sent or not secrets.compare_digest(cookie.encode(), sent.encode()):
        raise fail
