"""ASGI middleware that records metrics and one log line per request (M1.3)."""

import time

from opentelemetry.metrics import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.logging import get_logger

# These endpoints are infrastructure, not application traffic, so they are skipped.
_SKIPPED_PATHS = frozenset({"/metrics", "/healthz"})

_logger = get_logger(__name__)


class RequestMetricsMiddleware:
    """Time every HTTP request and feed the counter and histogram."""

    def __init__(self, app: ASGIApp, *, requests: Counter, duration: Histogram) -> None:
        self.app = app
        self._requests = requests
        self._duration = duration

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # A mounted /metrics redirects to /metrics/, so ignore the trailing slash.
        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or path in _SKIPPED_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._record(scope, 500, started)
            raise

        self._record(scope, status_code, started)

    def _record(self, scope: Scope, status_code: int, started: float) -> None:
        """Record one request and log it once."""
        route = getattr(scope.get("route"), "path", None) or "unmatched"
        method = scope.get("method", "")
        duration_seconds = time.perf_counter() - started

        attributes = {
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status_code,
        }
        self._requests.add(1, attributes)
        self._duration.record(duration_seconds, attributes)

        _logger.info(
            "request_completed",
            method=method,
            route=route,
            status_code=status_code,
            duration_ms=round(duration_seconds * 1000, 3),
        )
