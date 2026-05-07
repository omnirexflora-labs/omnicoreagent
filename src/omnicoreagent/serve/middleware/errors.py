"""Error handling middleware for OmniServe."""

from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Return stable JSON for uncaught serving errors."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(f"OmniServe Error: {type(exc).__name__}: {exc}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "InternalServerError",
                    "message": "An internal server error occurred",
                    "detail": str(exc),
                },
            )


def add_error_handling_middleware(app: FastAPI) -> None:
    """Install global JSON error handling."""
    app.add_middleware(ErrorHandlingMiddleware)
