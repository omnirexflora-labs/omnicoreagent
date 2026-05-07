"""In-memory rate limiting middleware for OmniServe."""

import time
from collections import defaultdict
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

from ..config import OmniServeConfig
from .auth import public_paths


class RateLimitState:
    """Rate-limit counters for one middleware instance."""

    def __init__(self):
        self._state: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    def check(
        self,
        client_ip: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """Return whether a request is allowed, remaining requests, reset time."""
        current_time = time.time()
        request_count, window_start = self._state[client_ip]

        if current_time - window_start >= window_seconds:
            self._state[client_ip] = (1, current_time)
            return True, max_requests - 1, int(current_time + window_seconds)

        reset_time = int(window_start + window_seconds)
        if request_count >= max_requests:
            return False, 0, reset_time

        self._state[client_ip] = (request_count + 1, window_start)
        return True, max_requests - request_count - 1, reset_time

    def cleanup_old_entries(self, window_seconds: int) -> None:
        """Remove old entries so long-running servers do not grow forever."""
        current_time = time.time()
        for ip, (_, window_start) in list(self._state.items()):
            if current_time - window_start > window_seconds * 2:
                del self._state[ip]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit protected requests by client IP."""

    def __init__(
        self,
        app,
        max_requests: int,
        window_seconds: int,
        exempt_paths: set[str],
    ):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths
        self.state = RateLimitState()
        self._cleanup_counter = 0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        allowed, remaining, reset_time = self.state.check(
            client_ip, self.max_requests, self.window_seconds
        )

        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            self.state.cleanup_old_entries(self.window_seconds)
            self._cleanup_counter = 0

        if not allowed:
            retry_after = max(0, reset_time - int(time.time()))
            logger.warning(
                f"OmniServe Rate Limit: Client {client_ip} exceeded "
                f"{self.max_requests} requests per {self.window_seconds}s"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "TooManyRequests",
                    "message": (
                        f"Rate limit exceeded. Max {self.max_requests} requests "
                        f"per {self.window_seconds} seconds."
                    ),
                    "retry_after": retry_after,
                },
                headers={
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                    "Retry-After": str(retry_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return response

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        if request.client:
            return request.client.host

        return "unknown"


def add_rate_limit_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """Install in-memory rate limiting when enabled."""
    if not config.rate_limit_enabled:
        return

    app.add_middleware(
        RateLimitMiddleware,
        max_requests=config.rate_limit_requests,
        window_seconds=config.rate_limit_window,
        exempt_paths=public_paths(config),
    )
    logger.info(
        f"OmniServe: Rate limiting enabled - "
        f"{config.rate_limit_requests} requests per {config.rate_limit_window}s"
    )
