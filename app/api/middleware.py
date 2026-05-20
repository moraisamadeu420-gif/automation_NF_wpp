"""
app/api/middleware.py
Request logging and basic rate limiting middleware.
"""
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "{} {} {} {:.1f}ms",
            request.method, request.url.path, response.status_code, elapsed,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-process rate limiter — replace with Redis for multi-process deploys."""

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._window = 60
        self._counts: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/webhook"):
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            window_start = now - self._window
            self._counts[client_ip] = [t for t in self._counts[client_ip] if t > window_start]
            if len(self._counts[client_ip]) >= self._limit:
                return Response("Too Many Requests", status_code=429)
            self._counts[client_ip].append(now)
        return await call_next(request)
