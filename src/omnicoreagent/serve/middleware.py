"""
OmniServe Middleware.

Middleware configuration and factories for:
- CORS
- Request logging
- Error handling
- Authentication
"""

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.utils import logger

from .config import OmniServeConfig


def add_cors_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """
    Add CORS middleware to the FastAPI app.

    Args:
        app: FastAPI application
        config: OmniServe configuration
    """
    if not config.cors_enabled:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_credentials,
        allow_methods=config.cors_methods,
        allow_headers=config.cors_headers,
    )
    logger.info(
        f"OmniServe: CORS middleware enabled for origins: {config.cors_origins}"
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging incoming requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details."""
        start_time = time.time()

        # Log request
        logger.info(
            f"OmniServe Request: {request.method} {request.url.path} "
            f"from {request.client.host if request.client else 'unknown'}"
        )

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Log response
        logger.info(
            f"OmniServe Response: {request.method} {request.url.path} "
            f"status={response.status_code} duration={duration:.3f}s"
        )

        # Add timing header
        response.headers["X-Process-Time"] = f"{duration:.3f}"

        return response


def add_request_logging_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """
    Add request logging middleware.

    Args:
        app: FastAPI application
        config: OmniServe configuration
    """
    if not config.request_logging:
        return

    app.add_middleware(RequestLoggingMiddleware)
    logger.info("OmniServe: Request logging middleware enabled")


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for Bearer token authentication."""

    def __init__(self, app, auth_token: str):
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Validate Bearer token."""
        # Skip auth for health endpoints
        if request.url.path in [
            "/health",
            "/ready",
            "/docs",
            "/redoc",
            "/openapi.json",
        ]:
            return await call_next(request)

        # Get Authorization header
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "Missing or invalid Authorization header",
                },
            )

        token = auth_header.replace("Bearer ", "")  # noqa: B005

        if token != self.auth_token:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "Invalid authentication token",
                },
            )

        return await call_next(request)


def add_auth_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """
    Add authentication middleware.

    Args:
        app: FastAPI application
        config: OmniServe configuration
    """
    if not config.auth_enabled or not config.auth_token:
        return

    app.add_middleware(AuthMiddleware, auth_token=config.auth_token)
    logger.info("OmniServe: Authentication middleware enabled")


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for global error handling."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Catch and handle exceptions."""
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"OmniServe Error: {type(e).__name__}: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "InternalServerError",
                    "message": "An internal server error occurred",
                    "detail": str(e),
                },
            )


def add_error_handling_middleware(app: FastAPI) -> None:
    """
    Add error handling middleware.

    This middleware is always enabled as it provides core error handling
    functionality for the API server.

    Args:
        app: FastAPI application
    """
    app.add_middleware(ErrorHandlingMiddleware)


def setup_all_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """
    Set up all middleware based on configuration.

    Middleware is added in the correct order (last added = first executed).

    Args:
        app: FastAPI application
        config: OmniServe configuration
    """
    # Import rate limiting here to avoid circular imports
    from .rate_limit import add_rate_limit_middleware

    # Add in reverse order of execution (last added runs first)
    add_error_handling_middleware(app)
    add_rate_limit_middleware(app, config)
    add_request_logging_middleware(app, config)
    add_auth_middleware(app, config)
    add_cors_middleware(app, config)
