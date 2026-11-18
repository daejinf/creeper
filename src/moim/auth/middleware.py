"""ASGI middleware: security headers on every response and a request-body cap on the auth API."""
from __future__ import annotations

import json

AUTH_BODY_LIMIT = 8 * 1024

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
)


class SecurityMiddleware:
    def __init__(self, app, hsts: bool = False):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limited = scope["path"].startswith("/api/auth/")
        too_big = False

        async def guarded_receive():
            nonlocal too_big, seen
            message = await receive()
            if limited and message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > AUTH_BODY_LIMIT:
                    too_big = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        seen = 0
        if limited:
            declared = dict(scope["headers"]).get(b"content-length")
            if declared and declared.isdigit() and int(declared) > AUTH_BODY_LIMIT:
                await self._reject(send)
                return

        started = False

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                if too_big:  # body was cut off at the cap: never let the handler's answer through
                    started = True
                    await self._reject(send)
                    return
                headers = message.setdefault("headers", [])
                headers.extend(self._headers())
            if too_big and started:
                return
            await send(message)

        await self.app(scope, guarded_receive if limited else receive, guarded_send)

    def _headers(self) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"same-origin"),
            (b"x-frame-options", b"DENY"),
        ]
        if self.hsts:
            headers.append((b"strict-transport-security", b"max-age=31536000"))
        return headers

    async def _reject(self, send):
        body = json.dumps({"error": {"code": "TOO_LARGE", "message": "요청이 너무 커요."}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                    *self._headers(),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
