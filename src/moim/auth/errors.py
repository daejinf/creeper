"""Uniform JSON error shape for the auth API: {"error": {"code", "message", "field"?}}."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None, retry_after: int | None = None,
                 extra: dict | None = None):
        super().__init__(code)
        self.extra = extra  # extra keys of the error body (stage 6: the impact preview a confirmation must show)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.retry_after = retry_after


def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    body: dict = {"code": exc.code, "message": exc.message}
    if exc.field:
        body["field"] = exc.field
    if getattr(exc, "extra", None):
        body.update({k: v for k, v in exc.extra.items() if k not in ("code", "message", "field")})
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse({"error": body}, status_code=exc.status, headers=headers)
