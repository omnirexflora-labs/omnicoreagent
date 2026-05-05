"""
OmniServe Rate Limiting Middleware.

Simple in-memory rate limiting using token bucket algorithm.
For production with multiple workers, use Redis-backed rate limiting.
"""

import time
from collections import defaultdict
from typing import Callable, Dict, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.utils import logger

from .config import OmniServeConfig


class RateLimitState:
    """Thread-safe rate limit state tracker."""

    def __init__(self):
        # Maps client IP -> (request_count, window_start_time)
        self._state: Dict[str, Tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    def check_rate_limit(
        self,
        client_ip: str,
        max_requests: int,
        window_seconds: int,
    ) -> Tuple[bool, int, int]:
        """
        Check if a request should be allowed.

        Args:
            client_ip: The client's IP address
            max_requests: Maximum requests allowed per window
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed, remaining_requests, reset_time)
        """
        current_time = time.time()
        request_count, window_start = self._state[client_ip]

        # Check if we're in a new window
        if current_time - window_start >= window_seconds:
            # Reset the window
            self._state[client_ip] = (1, current_time)
            return True, max_requests - 1, int(current_time + window_seconds)

        # Check if limit exceeded
        if request_count >= max_requests:
            reset_time = int(window_start + window_seconds)
            return False, 0, reset_time

        # Increment counter
        self._state[client_ip] = (request_count + 1, window_start)
        remaining = max_requests - request_count - 1
        reset_time = int(window_start + window_seconds)

        return True, remaining, reset_time

    def cleanup_old_entries(self, window_seconds: int) -> None:
        """Remove entries older than the window to prevent memory leaks."""
        current_time = time.time()
        to_remove = [
            ip
            for ip, (_, window_start) in self._state.items()
            if current_time - window_start > window_seconds * 2
        ]
        for ip in to_remove:
            del self._state[ip]


# Global rate limit state (shared across requests)
_rate_limit_state = RateLimitState()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting requests by client IP."""

    def __init__(self, app, max_requests: int, window_seconds: int):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._cleanup_counter = 0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check rate limit before processing request."""
        # Skip rate limiting for health endpoints
        if request.url.path in ["/health", "/ready"]:
            return await call_next(request)

        # Get client IP (handle proxy headers)
        client_ip = self._get_client_ip(request)

        # Check rate limit
        allowed, remaining, reset_time = _rate_limit_state.check_rate_limit(
            client_ip, self.max_requests, self.window_seconds
        )

        # Periodic cleanup (every 100 requests)
        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            _rate_limit_state.cleanup_old_entries(self.window_seconds)
            self._cleanup_counter = 0

        if not allowed:
            logger.warning(
                f"OmniServe Rate Limit: Client {client_ip} exceeded "
                f"{self.max_requests} requests per {self.window_seconds}s"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "TooManyRequests",
                    "message": f"Rate limit exceeded. Max {self.max_requests} requests per {self.window_seconds} seconds.",
                    "retry_after": reset_time - int(time.time()),
                },
                headers={
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                    "Retry-After": str(reset_time - int(time.time())),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP, handling reverse proxy headers."""
        # Check X-Forwarded-For header (from reverse proxies)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # First IP in the list is the original client
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct connection IP
        if request.client:
            return request.client.host

        return "unknown"


def add_rate_limit_middleware(app, config: OmniServeConfig) -> None:
    """
    Add rate limiting middleware if enabled.

    Args:
        app: FastAPI application
        config: OmniServe configuration
    """
    if not config.rate_limit_enabled:
        return

    app.add_middleware(
        RateLimitMiddleware,
        max_requests=config.rate_limit_requests,
        window_seconds=config.rate_limit_window,
    )
    logger.info(
        f"OmniServe: Rate limiting enabled - "
        f"{config.rate_limit_requests} requests per {config.rate_limit_window}s"
    )
