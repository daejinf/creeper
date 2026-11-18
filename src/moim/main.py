"""FastAPI entrypoint: health check, the AI job API and auth (landing, signup, login). Other product screens come later."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from .auth.errors import AuthError, auth_error_handler
from .auth.middleware import SecurityMiddleware
from .auth.routes import make_router as make_auth_router
from .auth.sessions import SessionStore
from .auth.throttle import Throttle
from .config import Settings, load_settings
from .db import make_engine, make_session_factory
from .job_routes import make_router
from .jobs import Handler, JobStore
from .logging_utils import install_redaction

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


class RevalidatedStaticFiles(StaticFiles):
    """Static files are not fingerprinted, so browsers must revalidate (ETag/Last-Modified) instead of reusing a heuristic cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None, job_handlers: Mapping[str, Handler] | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings)
    handlers = dict(job_handlers or {})
    store = JobStore(make_session_factory(engine))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_redaction()
        log = logging.getLogger("moim")
        log.info("starting env=%s db=%s", settings.env, settings.safe_database_url)
        interrupted = store.fail_interrupted()  # work lost with the previous process becomes a retryable failure
        if interrupted:
            log.warning("marked %d interrupted job(s) as FAILED (SERVER_RESTARTED)", interrupted)
        yield
        engine.dispose()

    app = FastAPI(title="MOIM", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    session_factory = make_session_factory(engine)
    sessions = SessionStore(session_factory)
    throttle = Throttle(session_factory)
    templates = Jinja2Templates(directory=TEMPLATES_DIR)  # autoescape is on for .html
    app.state.settings = settings
    app.state.engine = engine
    app.state.jobs = store
    app.state.sessions = sessions
    app.state.throttle = throttle
    app.add_middleware(SecurityMiddleware, hsts=settings.secure_cookies)
    app.add_exception_handler(AuthError, auth_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/auth/"):  # malformed auth bodies get the auth error shape; no echo of input
            return auth_error_handler(request, AuthError(400, "BAD_REQUEST", "요청 형식이 올바르지 않아요."))
        return await request_validation_exception_handler(request, exc)

    app.include_router(make_router(store, handlers))
    app.include_router(make_auth_router(settings, session_factory, sessions, throttle, templates))
    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz")
    def healthz():
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return {"status": "ok", "env": settings.env}

    return app


def app_factory() -> FastAPI:  # uvicorn --factory moim.main:app_factory
    return create_app()
