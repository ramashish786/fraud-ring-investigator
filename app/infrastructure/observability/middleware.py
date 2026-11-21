"""Request correlation middleware (pure ASGI, so it doesn't buffer streaming bodies the way
Starlette's BaseHTTPMiddleware does).

* Accepts an inbound X-Request-ID only if it is a safe token; otherwise generates one.
* Binds request_id into structlog contextvars for every log line in the request.
* Echoes X-Request-ID on the response and logs one access line with latency.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging import get_logger
from .metrics import HTTP_LATENCY, HTTP_REQUESTS

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
log = get_logger("http")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._templates: list[tuple[re.Pattern[str], str]] | None = None

    def _route_template(self, scope: Scope) -> str:
        """Map a concrete path to its OpenAPI template using only public API (the app's OpenAPI schema), so
        metric labels stay bounded: /api/v1/investigations/{inv_id}, never the id itself."""
        if self._templates is None:
            app = scope.get("app")
            paths = app.openapi().get("paths", {}) if app is not None and hasattr(app, "openapi") else {}
            self._templates = [(re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", t) + "$"), t) for t in paths]
            self._templates.append((re.compile("^/metrics$"), "/metrics"))
        path = scope.get("path", "")
        return next((t for rx, t in self._templates if rx.match(path)), "unmatched")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = next((v.decode() for k, v in scope["headers"] if k == b"x-request-id"), "")
        request_id = inbound if _SAFE_ID.match(inbound) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = self._route_template(scope)  # template, never the raw path
            if route != "/metrics":
                HTTP_REQUESTS.labels(scope["method"], route, str(status)).inc()
                HTTP_LATENCY.labels(scope["method"], route).observe(elapsed)
            log.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()
