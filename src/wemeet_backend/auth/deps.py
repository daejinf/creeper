"""FastAPI dependencies. ``current_user`` is reusable by any future router."""

from threading import Lock

from fastapi import Depends, Request
from sqlalchemy import Engine

from .. import db
from ..config import Settings
from . import service
from .models import UserOut

_engine_lock = Lock()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_engine(request: Request) -> Engine:
    """One lazily-built engine per app (nothing connects until first query)."""
    state = request.app.state
    if state.engine is None:
        with _engine_lock:
            if state.engine is None:
                state.engine = db.build_engine(state.settings)
    return state.engine


def session_token(request: Request, settings: Settings = Depends(get_settings)) -> str | None:
    return request.cookies.get(settings.cookie_name)


def current_user(
    token: str | None = Depends(session_token), engine: Engine = Depends(get_engine)
) -> UserOut:
    """Cookie -> session -> user, else 401 UNAUTHENTICATED. Sync (thread pool), own short transaction."""
    with engine.begin() as conn:
        return service.authenticate(conn, token)
