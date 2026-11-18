"""FastAPI entrypoint: health check and the AI job API. No auth UI or product screens yet."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import Settings, load_settings
from .db import make_engine, make_session_factory
from .job_routes import make_router
from .jobs import Handler, JobStore
from .logging_utils import install_redaction

STATIC_DIR = Path(__file__).parent / "static"


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
    app.state.settings = settings
    app.state.engine = engine
    app.state.jobs = store
    app.include_router(make_router(store, handlers))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz")
    def healthz():
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return {"status": "ok", "env": settings.env}

    return app


def app_factory() -> FastAPI:  # uvicorn --factory moim.main:app_factory
    return create_app()
