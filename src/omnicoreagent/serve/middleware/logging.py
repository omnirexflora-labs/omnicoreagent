"""Request logging middleware for OmniServe."""

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

from ..config import OmniServeConfig


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log incoming requests and response status."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        client_host = request.client.host if request.client else "unknown"

        logger.info(
            f"OmniServe Request: {request.method} {request.url.path} from {client_host}"
        )

        response = await call_next(request)
        duration = time.time() - start_time

        logger.info(
            f"OmniServe Response: {request.method} {request.url.path} "
            f"status={response.status_code} duration={duration:.3f}s"
        )

        response.headers["X-Process-Time"] = f"{duration:.3f}"
        return response


def add_request_logging_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """Install request logging when enabled."""
    if not config.request_logging:
        return

    app.add_middleware(RequestLoggingMiddleware)
    logger.info("OmniServe: Request logging middleware enabled")
