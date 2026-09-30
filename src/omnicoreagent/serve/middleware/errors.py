"""Error handling middleware for OmniServe."""

from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.credentials import scrub_credentials
from omnicoreagent.core.logging import logger


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Return stable JSON for uncaught serving errors."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            # A key in an exception's message reached the caller and the log
            # as it was (the rc7 security review).
            detail = scrub_credentials(str(exc))
            logger.error(f"OmniServe Error: {type(exc).__name__}: {detail}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "InternalServerError",
                    "message": "An internal server error occurred",
                    "detail": detail,
                },
            )


def add_error_handling_middleware(app: FastAPI) -> None:
    """Install global JSON error handling."""
    app.add_middleware(ErrorHandlingMiddleware)
