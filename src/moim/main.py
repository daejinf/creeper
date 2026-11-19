"""FastAPI entrypoint: health check, the AI job API, auth, and the stage 2 group screens. Later stages add their own."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Mapping

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
from .groups.goals import GoalService
from .groups.routes import make_router as make_group_router
from .groups.service import GroupService
from .interview.candidates import CandidateService
from .interview.routes import make_router as make_interview_router
from .interview.ranking import RankingService
from .roles.assignment import AssignmentService
from .roles.coverage import CoverageService
from .roles.interview import RoleInterviewService
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


def _timely_client():
    from wemeet_ai.m0.client import TimelyClient

    return TimelyClient()


class _BlockedClient:
    """Test environment default for the interview/candidate AI: a test that forgets to inject a fixture must not reach Timely."""

    model = "blocked"

    def complete_json(self, *args, **kwargs):
        from wemeet_ai.m0.client import AIError

        raise AIError("LIVE_CALLS_PAUSED", "no AI client injected in the test environment")


def create_app(
    settings: Settings | None = None,
    job_handlers: Mapping[str, Handler] | None = None,
    goal_client_factory: Callable[[], object] | None = None,
    ai_client_factory: Callable[[], object] | None = None,
    rng_factory: Callable[[], object] | None = None,
) -> FastAPI:
    """`goal_client_factory` and `ai_client_factory` (role candidates) exist for deterministic tests only;
    the product always uses the Timely client."""
    settings = settings or load_settings()
    engine = make_engine(settings)
    handlers = dict(job_handlers or {})
    store = JobStore(make_session_factory(engine))
    group_service = GroupService(make_session_factory(engine))
    goal_service = GoalService(make_session_factory(engine), goal_client_factory or _timely_client)
    if ai_client_factory is None:
        ai_client_factory = (lambda: _BlockedClient()) if settings.env == "test" else _timely_client
    candidate_service = CandidateService(make_session_factory(engine), ai_client_factory)
    # Stage 4: the product draws with random.SystemRandom (no seed); only deterministic tests inject a seeded RNG.
    assignment_service = AssignmentService(rng_factory) if rng_factory is not None else AssignmentService()
    ranking_service = RankingService(make_session_factory(engine), assignment=assignment_service)
    coverage_service = CoverageService(make_session_factory(engine), ai_client_factory)
    role_interview_service = RoleInterviewService(make_session_factory(engine), ai_client_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_redaction()
        log = logging.getLogger("moim")
        log.info("starting env=%s db=%s", settings.env, settings.safe_database_url)
        interrupted = store.fail_interrupted()  # work lost with the previous process becomes a retryable failure
        if interrupted:
            log.warning("marked %d interrupted job(s) as FAILED (SERVER_RESTARTED)", interrupted)
        lost_goals = goal_service.fail_interrupted()
        if lost_goals:
            log.warning("marked %d interrupted team goal(s) as FAILED (SERVER_RESTARTED)", lost_goals)
        lost_candidates = candidate_service.fail_interrupted()
        if lost_candidates:
            log.warning("marked %d interrupted role candidate set(s) as FAILED (SERVER_RESTARTED)", lost_candidates)
        lost_coverage = coverage_service.fail_interrupted()
        if lost_coverage:
            log.warning("marked %d interrupted team coverage(s) as FAILED (SERVER_RESTARTED)", lost_coverage)
        lost_turns = role_interview_service.fail_interrupted()
        if lost_turns:
            log.warning("marked %d interrupted interview step(s) as FAILED (SERVER_RESTARTED)", lost_turns)
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
    app.state.groups = group_service
    app.state.goals = goal_service
    app.state.candidates = candidate_service
    app.state.ranking = ranking_service
    app.state.assignment = assignment_service
    app.state.coverage = coverage_service
    app.state.role_interview = role_interview_service
    app.add_middleware(SecurityMiddleware, hsts=settings.secure_cookies)
    app.add_exception_handler(AuthError, auth_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(("/api/auth/", "/api/groups")):  # malformed auth bodies get the auth error shape; no echo of input
            return auth_error_handler(request, AuthError(400, "BAD_REQUEST", "요청 형식이 올바르지 않아요."))
        return await request_validation_exception_handler(request, exc)

    app.include_router(make_router(store, handlers))
    app.include_router(make_auth_router(settings, session_factory, sessions, throttle, templates))
    app.include_router(make_group_router(settings, group_service, goal_service, candidate_service, throttle, templates))
    app.include_router(make_interview_router(
        settings, group_service, goal_service, candidate_service, ranking_service, templates,
        coverage=coverage_service, role_interview=role_interview_service,
    ))
    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz")
    def healthz():
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return {"status": "ok", "env": settings.env}

    return app


def app_factory() -> FastAPI:  # uvicorn --factory moim.main:app_factory
    return create_app()
