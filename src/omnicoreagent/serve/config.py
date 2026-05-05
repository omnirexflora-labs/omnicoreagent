"""
OmniServe Configuration.

Pydantic settings for server configuration with sensible defaults.
Supports configuration via environment variables with OMNISERVE_ prefix.

Environment Variables (OVERRIDE code values):
    OMNISERVE_HOST: Host to bind to (default: 0.0.0.0)
    OMNISERVE_PORT: Port to bind to (default: 8000)
    OMNISERVE_WORKERS: Number of worker processes (default: 1)
    OMNISERVE_API_PREFIX: API path prefix (default: "")
    OMNISERVE_ENABLE_DOCS: Enable Swagger UI (default: true)
    OMNISERVE_CORS_ENABLED: Enable CORS (default: true)
    OMNISERVE_CORS_ORIGINS: Comma-separated allowed origins (default: *)
    OMNISERVE_AUTH_ENABLED: Enable Bearer token auth (default: false)
    OMNISERVE_AUTH_TOKEN: Bearer token for auth
    OMNISERVE_REQUEST_LOGGING: Log requests (default: true)
    OMNISERVE_LOG_LEVEL: Logging level (default: INFO)
    OMNISERVE_REQUEST_TIMEOUT: Request timeout in seconds (default: 300)
    OMNISERVE_RATE_LIMIT_ENABLED: Enable rate limiting (default: false)
    OMNISERVE_RATE_LIMIT_REQUESTS: Max requests per window (default: 100)
    OMNISERVE_RATE_LIMIT_WINDOW: Time window in seconds (default: 60)

Priority: Environment variables ALWAYS override code values.
"""

import os
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


def _get_env(key: str) -> Optional[str]:
    """Get environment variable with OMNISERVE_ prefix. Returns None if not set."""
    val = os.environ.get(f"OMNISERVE_{key}")
    return val if val is not None and val != "" else None


def _get_env_bool(key: str) -> Optional[bool]:
    """Get boolean environment variable. Returns None if not set."""
    val = _get_env(key)
    if val is None:
        return None
    return val.lower() in ("true", "1", "yes", "on")


def _get_env_int(key: str) -> Optional[int]:
    """Get integer environment variable. Returns None if not set."""
    val = _get_env(key)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _get_env_list(key: str) -> Optional[List[str]]:
    """Get list from comma-separated environment variable. Returns None if not set."""
    val = _get_env(key)
    if val is None:
        return None
    return [item.strip() for item in val.split(",") if item.strip()]


class OmniServeConfig(BaseModel):
    """
    Configuration for OmniServe server.

    Environment variables with OMNISERVE_ prefix ALWAYS override code values.
    For example, if OMNISERVE_PORT=9000 is set, it will override port=8000 in code.
    """

    # Server settings
    host: str = Field(default="0.0.0.0", description="Host to bind the server to")
    port: int = Field(default=8000, description="Port to bind the server to")
    workers: int = Field(default=1, description="Number of worker processes")

    # API settings
    api_prefix: str = Field(default="", description="API path prefix (e.g., '/api/v1')")
    enable_docs: bool = Field(default=True, description="Enable Swagger UI at /docs")
    enable_redoc: bool = Field(default=True, description="Enable ReDoc at /redoc")

    # CORS settings
    cors_enabled: bool = Field(default=True, description="Enable CORS middleware")
    cors_origins: List[str] = Field(default=["*"], description="Allowed CORS origins")
    cors_methods: List[str] = Field(default=["*"], description="Allowed CORS methods")
    cors_headers: List[str] = Field(default=["*"], description="Allowed CORS headers")
    cors_credentials: bool = Field(default=True, description="Allow credentials in CORS")

    # Authentication
    auth_enabled: bool = Field(default=False, description="Enable Bearer token auth")
    auth_token: Optional[str] = Field(default=None, description="Bearer token for authentication")

    # Logging
    request_logging: bool = Field(default=True, description="Log incoming requests")
    log_level: str = Field(default="INFO", description="Logging level")

    # Timeouts
    request_timeout: int = Field(default=300, description="Request timeout in seconds")

    # Rate limiting
    rate_limit_enabled: bool = Field(default=False, description="Enable rate limiting")
    rate_limit_requests: int = Field(default=100, description="Max requests per time window")
    rate_limit_window: int = Field(default=60, description="Rate limit time window in seconds")

    class Config:
        """Pydantic config."""
        extra = "allow"

    @model_validator(mode="after")
    def apply_env_overrides(self) -> "OmniServeConfig":
        """
        Apply environment variable overrides AFTER initial values are set.
        
        Environment variables always take priority over code-defined values.
        """
        # Server settings
        if (val := _get_env("HOST")) is not None:
            self.host = val
        if (val := _get_env_int("PORT")) is not None:
            self.port = val
        if (val := _get_env_int("WORKERS")) is not None:
            self.workers = val

        # API settings
        if (val := _get_env("API_PREFIX")) is not None:
            self.api_prefix = val
        if (val := _get_env_bool("ENABLE_DOCS")) is not None:
            self.enable_docs = val
        if (val := _get_env_bool("ENABLE_REDOC")) is not None:
            self.enable_redoc = val

        # CORS settings
        if (val := _get_env_bool("CORS_ENABLED")) is not None:
            self.cors_enabled = val
        if (val := _get_env_list("CORS_ORIGINS")) is not None:
            self.cors_origins = val
        if (val := _get_env_list("CORS_METHODS")) is not None:
            self.cors_methods = val
        if (val := _get_env_list("CORS_HEADERS")) is not None:
            self.cors_headers = val
        if (val := _get_env_bool("CORS_CREDENTIALS")) is not None:
            self.cors_credentials = val

        # Authentication
        if (val := _get_env_bool("AUTH_ENABLED")) is not None:
            self.auth_enabled = val
        if (val := _get_env("AUTH_TOKEN")) is not None:
            self.auth_token = val

        # Logging
        if (val := _get_env_bool("REQUEST_LOGGING")) is not None:
            self.request_logging = val
        if (val := _get_env("LOG_LEVEL")) is not None:
            self.log_level = val

        # Timeouts
        if (val := _get_env_int("REQUEST_TIMEOUT")) is not None:
            self.request_timeout = val

        # Rate limiting
        if (val := _get_env_bool("RATE_LIMIT_ENABLED")) is not None:
            self.rate_limit_enabled = val
        if (val := _get_env_int("RATE_LIMIT_REQUESTS")) is not None:
            self.rate_limit_requests = val
        if (val := _get_env_int("RATE_LIMIT_WINDOW")) is not None:
            self.rate_limit_window = val

        return self

    @classmethod
    def from_env(cls) -> "OmniServeConfig":
        """Create config from environment variables only."""
        return cls()
