"""App factory. ``app`` is built lazily on first attribute access, so importing this
module never needs the environment (uvicorn's ``wemeet_backend.main:app`` still works)."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth.router import router as auth_router
from .auth.service import AuthError
from .config import Settings, load_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def failed(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"status": "FAILED", "error": {"code": code, "message": message}}, status_code=status)


def origin_allowed(origin: str, host: str | None, settings: Settings) -> bool:
    if origin in settings.cors_origins:
        return True
    return bool(host) and urlsplit(origin).netloc == host  # same-origin


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.engine is not None:
            app.state.engine.dispose()

    docs = settings.enable_docs  # off by default; /docs and /openapi.json only for local API-spec sharing
    app = FastAPI(
        title="WE-MEET backend",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.engine = None

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError):
        return failed(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # Never echo submitted values (they may contain passwords): use only our own messages.
        message = "요청 형식이 올바르지 않습니다."
        for err in exc.errors():
            msg = str(err.get("msg", ""))
            if msg.startswith("Value error, "):
                message = msg.removeprefix("Value error, ")
                break
        return failed(422, "INVALID_REQUEST", message)

    @app.middleware("http")
    async def _origin_and_cache(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None:
            if not origin_allowed(origin, request.headers.get("host"), settings):
                return failed(403, "FORBIDDEN_ORIGIN", "허용되지 않은 출처입니다.")
        response = await call_next(request)
        if request.url.path.startswith("/api/auth"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if settings.cors_origins:  # added last = outermost, so preflight is answered before the checks above
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )

    @app.get("/health")
    def health():  # no DB access
        return {"status": "OK"}

    app.include_router(auth_router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
