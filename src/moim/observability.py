"""Stage 7: request correlation and structured logs.

Every HTTP request gets a request id (a well-formed incoming `X-Request-ID` from the reverse proxy is kept, anything
else is replaced). The id is stored in a context variable, so every log line written while serving the request -
including the background AI work FastAPI runs after the response, and the `provider_call` line of each Timely call -
carries it. The id is returned in the `X-Request-ID` response header for support.

One access line per request (`moim.access`): method, the route *template* (never the raw path or the query string,
so no invite codes, `next=` values or ids leak into access logs), status, duration. Client addresses are not logged.

`configure_logging()` installs a JSON formatter (one object per line) on the root logger. The secret-masking filter
from `logging_utils` still runs first on every record.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import traceback
import uuid
from datetime import datetime, timezone

from .logging_utils import RedactingFilter

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("moim_request_id", default="-")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
access_log = logging.getLogger("moim.access")


def current_request_id() -> str:
    return request_id_var.get()


def _route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    raw = scope.get("path", "")
    if raw.startswith("/static/"):
        return "/static/*"
    return "<unmatched>"


class RequestContextMiddleware:
    """Pure ASGI so the context variable is set for the whole request, background tasks included."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            access_log.info(
                "http_request method=%s route=%s status=%d duration_ms=%d",
                scope.get("method", "-"), _route_template(scope), status["code"],
                int((time.perf_counter() - started) * 1000),
            )
            request_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. The message has already been masked by RedactingFilter."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            # The exception type and where it was raised, never its message (a message can quote user content).
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc_at"] = [f"{f.filename.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}:{f.lineno}:{f.name}"
                               for f in traceback.extract_tb(record.exc_info[2])][-8:]
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Root handler with JSON lines on stderr. Idempotent."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_moim_json", False):
            return
    handler = logging.StreamHandler()
    handler._moim_json = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level)
