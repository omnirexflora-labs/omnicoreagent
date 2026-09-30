"""CORS middleware setup for OmniServe."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from omnicoreagent.core.logging import logger

from ..config import OmniServeConfig


def add_cors_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """Add CORS middleware when enabled."""
    if not config.cors_enabled:
        return

    credentials = config.cors_credentials
    if credentials and "*" in config.cors_origins:
        # Starlette reflects the caller's origin for "*" with credentials, so
        # every web page could read responses (the rc7 security review).
        logger.warning(
            "OmniServe: CORS credentials are off: they need explicit origins, not '*'."
        )
        credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=credentials,
        allow_methods=config.cors_methods,
        allow_headers=config.cors_headers,
    )
    logger.info(
        f"OmniServe: CORS middleware enabled for origins: {config.cors_origins}"
    )
