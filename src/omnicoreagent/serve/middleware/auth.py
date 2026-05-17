"""Bearer-token authentication middleware for OmniServe."""

from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

from ..config import OmniServeConfig


def _normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return "/" + prefix.strip("/")


def public_paths(config: OmniServeConfig) -> set[str]:
    """Return paths that intentionally bypass auth."""
    prefixed = _normalize_prefix(config.api_prefix)
    api_public = {"/health", "/ready"}
    global_public = {"/docs", "/redoc", "/openapi.json", "/prometheus"}

    paths = set(global_public)
    paths.update(api_public)
    if prefixed:
        paths.update(f"{prefixed}{path}" for path in api_public)
    return paths


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer tokens for protected API routes."""

    def __init__(self, app, auth_token: str, public_paths: set[str]):
        super().__init__(app)
        self.auth_token = auth_token
        self.public_paths = public_paths

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.public_paths:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "Missing or invalid Authorization header",
                },
            )

        token = auth_header.removeprefix("Bearer ").strip()
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
    """Install bearer-token auth when enabled."""
    if not config.auth_enabled:
        return
    if not config.auth_token or not config.auth_token.strip():
        raise ValueError(
            "OmniServe auth is enabled but no bearer token is configured"
        )

    app.add_middleware(
        AuthMiddleware,
        auth_token=config.auth_token,
        public_paths=public_paths(config),
    )
    logger.info("OmniServe: Authentication middleware enabled")
