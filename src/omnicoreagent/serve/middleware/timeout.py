"""Request timeout middleware for OmniServe."""

import asyncio
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

from ..config import OmniServeConfig


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a timeout while building HTTP responses."""

    def __init__(self, app, timeout_seconds: int):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _route_manages_timeout(request.url.path):
            return await call_next(request)
        try:
            return await asyncio.wait_for(
                call_next(request), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"OmniServe Timeout: {request.method} {request.url.path} "
                f"exceeded {self.timeout_seconds}s"
            )
            return JSONResponse(
                status_code=504,
                content={
                    "error": "GatewayTimeout",
                    "message": "Request timed out",
                    "detail": (
                        f"Request timed out after {self.timeout_seconds} seconds"
                    ),
                },
            )


def add_timeout_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """Install request timeout middleware when configured."""
    if config.request_timeout <= 0:
        return

    app.add_middleware(TimeoutMiddleware, timeout_seconds=config.request_timeout)
    logger.info(f"OmniServe: Request timeout set to {config.request_timeout}s")


def _route_manages_timeout(path: str) -> bool:
    """Return true for routes that produce their own structured timeout response."""
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part != "background":
            continue
        return (
            len(parts) == index + 4
            and parts[index + 1] == "tasks"
            and parts[index + 3] == "run"
        )
    return False
