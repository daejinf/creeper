"""/api/auth endpoints. Sync handlers run in the thread pool (scrypt is CPU-bound).

Each handler commits its own transaction before the response is built, so a client
never sees a response for a change that is not yet durable.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import Engine

from ..config import Settings
from . import service
from .deps import current_user, get_engine, get_settings, session_token
from .models import LoginRequest, SignupRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_days * 86400,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _ok(user: UserOut) -> dict:
    return {"status": "OK", "data": {"user": user.model_dump(mode="json")}}


@router.post("/signup", status_code=201)
def signup(
    body: SignupRequest,
    response: Response,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
):
    with engine.begin() as conn:
        auth = service.signup(conn, body, settings.session_days)
    _set_cookie(response, settings, auth.token)
    return _ok(auth.user)


@router.post("/login")
def login(
    body: LoginRequest,
    response: Response,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
):
    with engine.begin() as conn:
        auth = service.login(conn, body, settings.session_days)
    _set_cookie(response, settings, auth.token)
    return _ok(auth.user)


@router.post("/logout")
def logout(
    response: Response,
    token: str | None = Depends(session_token),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
):
    with engine.begin() as conn:
        service.logout(conn, token)
    _clear_cookie(response, settings)
    return {"status": "OK", "data": {}}


@router.get("/me")
def me(user: UserOut = Depends(current_user)):
    return _ok(user)
