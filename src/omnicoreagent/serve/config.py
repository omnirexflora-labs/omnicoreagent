"""
OmniServe Configuration.

Pydantic settings for server configuration with sensible defaults.
Supports configuration via OMNICOREAGENT_SERVE_* and
OMNICOREAGENT_BACKGROUND_* environment variables.

Environment Variables (OVERRIDE code values):
    OMNICOREAGENT_SERVE_HOST: Host to bind to (default: 0.0.0.0)
    OMNICOREAGENT_SERVE_PORT: Port to bind to (default: 8000)
    OMNICOREAGENT_SERVE_WORKERS: Worker processes. Direct OmniServe requires 1.
    OMNICOREAGENT_SERVE_API_PREFIX: API path prefix (default: "")
    OMNICOREAGENT_SERVE_ENABLE_DOCS: Enable Swagger UI (default: true)
    OMNICOREAGENT_SERVE_ENABLE_REDOC: Enable ReDoc UI (default: true)
    OMNICOREAGENT_SERVE_CORS_ENABLED: Enable CORS (default: true)
    OMNICOREAGENT_SERVE_CORS_ORIGINS: Comma-separated allowed origins (default: *)
    OMNICOREAGENT_SERVE_CORS_METHODS: Comma-separated allowed methods (default: *)
    OMNICOREAGENT_SERVE_CORS_HEADERS: Comma-separated allowed headers (default: *)
    OMNICOREAGENT_SERVE_CORS_CREDENTIALS: Allow credentials in CORS (default: true)
    OMNICOREAGENT_SERVE_AUTH_ENABLED: Enable Bearer token auth (default: false)
    OMNICOREAGENT_SERVE_AUTH_TOKEN: Bearer token for auth
    OMNICOREAGENT_SERVE_REQUEST_LOGGING: Log requests (default: true)
    OMNICOREAGENT_SERVE_LOG_LEVEL: Logging level (default: INFO)
    OMNICOREAGENT_SERVE_REQUEST_TIMEOUT: Request timeout in seconds (default: 300)
    OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED: Enable rate limiting (default: false)
    OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS: Max requests per window (default: 100)
    OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW: Time window in seconds (default: 60)
    OMNICOREAGENT_BACKGROUND_ENABLED: Enable background APIs (default: true)
    OMNICOREAGENT_BACKGROUND_AGENT_ID: Agent id used for the served agent (default: default)
    OMNICOREAGENT_BACKGROUND_TASK_STORE: Task store backend (default: in_memory)
    OMNICOREAGENT_BACKGROUND_TASK_STORE_URL: SQL or Redis task-store URL
    OMNICOREAGENT_BACKGROUND_TASK_STORE_URI: MongoDB task-store URI
    OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE: MongoDB task-store database
    OMNICOREAGENT_BACKGROUND_TASK_STORE_PREFIX: Redis task-store key prefix
    OMNICOREAGENT_BACKGROUND_TASK_STORE_COLLECTION_PREFIX: MongoDB collection prefix
    OMNICOREAGENT_BACKGROUND_TASK_STORE_CONNECT_TIMEOUT: Backend connect timeout
    OMNICOREAGENT_BACKGROUND_START_WORKER: Start scheduler/worker loop (default: true)

Priority: Environment variables ALWAYS override code values.
"""

import os
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"}


def _get_env(prefix: str, key: str) -> Optional[str]:
    """Get a prefixed environment variable. Returns None if not set."""
    val = os.environ.get(f"{prefix}_{key}")
    return val if val is not None and val != "" else None


def _get_env_bool(prefix: str, key: str) -> Optional[bool]:
    """Get boolean environment variable. Returns None if not set."""
    val = _get_env(prefix, key)
    if val is None:
        return None
    normalized = val.lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{prefix}_{key} must be a boolean value")


def _get_env_int(prefix: str, key: str) -> Optional[int]:
    """Get integer environment variable. Returns None if not set."""
    val = _get_env(prefix, key)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"{prefix}_{key} must be an integer") from None


def _get_env_float(prefix: str, key: str) -> Optional[float]:
    """Get float environment variable. Returns None if not set."""
    val = _get_env(prefix, key)
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        raise ValueError(f"{prefix}_{key} must be a number") from None


def _get_env_list(prefix: str, key: str) -> Optional[list[str]]:
    """Get list from comma-separated environment variable. Returns None if not set."""
    val = _get_env(prefix, key)
    if val is None:
        return None
    return [item.strip() for item in val.split(",") if item.strip()]


def normalize_api_prefix(value: str | None) -> str:
    """Normalize a FastAPI router prefix from code or environment."""
    prefix = (value or "").strip()
    if prefix in {"", "/"}:
        return ""
    if any(char.isspace() for char in prefix):
        raise ValueError("OMNICOREAGENT_SERVE_API_PREFIX must not contain whitespace")
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


def validate_server_bind_config(*, host: str, port: int, workers: int) -> None:
    """Validate server bind values used by config and runtime overrides."""
    if not host or not host.strip():
        raise ValueError("OMNICOREAGENT_SERVE_HOST must not be empty")
    if port < 1 or port > 65535:
        raise ValueError("OMNICOREAGENT_SERVE_PORT must be between 1 and 65535")
    if workers != 1:
        raise ValueError(
            "OMNICOREAGENT_SERVE_WORKERS must be 1 for direct OmniServe. "
            "Run multiple OmniServe processes behind a process manager for "
            "horizontal scaling."
        )


class OmniServeConfig(BaseModel):
    """
    Configuration for OmniServe server.

    OMNICOREAGENT_SERVE_* and OMNICOREAGENT_BACKGROUND_* environment variables
    ALWAYS override code values. For example, if
    OMNICOREAGENT_SERVE_PORT=9000 is set, it overrides port=8000 in code.
    """

    # Server settings
    host: str = Field(default="0.0.0.0", description="Host to bind the server to")
    port: int = Field(default=8000, description="Port to bind the server to")
    workers: int = Field(
        default=1,
        description="Worker processes. Direct OmniServe requires one process.",
    )

    # API settings
    api_prefix: str = Field(default="", description="API path prefix (e.g., '/api/v1')")
    enable_docs: bool = Field(default=True, description="Enable Swagger UI at /docs")
    enable_redoc: bool = Field(default=True, description="Enable ReDoc at /redoc")

    # CORS settings
    cors_enabled: bool = Field(default=True, description="Enable CORS middleware")
    model_config = ConfigDict(extra="allow")

    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"], description="Allowed CORS origins"
    )
    cors_methods: list[str] = Field(
        default_factory=lambda: ["*"], description="Allowed CORS methods"
    )
    cors_headers: list[str] = Field(
        default_factory=lambda: ["*"], description="Allowed CORS headers"
    )
    cors_credentials: bool = Field(
        default=True, description="Allow credentials in CORS"
    )

    # Authentication
    auth_enabled: bool = Field(default=False, description="Enable Bearer token auth")
    auth_token: Optional[str] = Field(
        default=None, description="Bearer token for authentication"
    )

    # Logging
    request_logging: bool = Field(default=True, description="Log incoming requests")
    log_level: str = Field(default="INFO", description="Logging level")

    # Timeouts
    request_timeout: int = Field(default=300, description="Request timeout in seconds")

    # Rate limiting
    rate_limit_enabled: bool = Field(default=False, description="Enable rate limiting")
    rate_limit_requests: int = Field(
        default=100, description="Max requests per time window"
    )
    rate_limit_window: int = Field(
        default=60, description="Rate limit time window in seconds"
    )

    # Background execution
    background_enabled: bool = Field(
        default=True, description="Expose background execution endpoints"
    )
    background_agent_id: str = Field(
        default="default", description="Agent id used for the served agent"
    )
    background_task_store: str | dict[str, Any] | None = Field(
        default="in_memory", description="Background task store backend or config"
    )
    background_task_store_url: str | None = Field(
        default=None, description="SQL or Redis task store URL for background execution"
    )
    background_task_store_uri: str | None = Field(
        default=None, description="MongoDB task store URI for background execution"
    )
    background_task_store_database: str | None = Field(
        default=None, description="MongoDB task store database"
    )
    background_task_store_prefix: str | None = Field(
        default=None, description="Redis task store key prefix"
    )
    background_task_store_collection_prefix: str | None = Field(
        default=None, description="MongoDB task store collection prefix"
    )
    background_task_store_connect_timeout: float | None = Field(
        default=None, description="Task store backend connect timeout"
    )
    background_start_worker: bool = Field(
        default=True,
        description="Start the background scheduler/worker during OmniServe lifespan",
    )

    @model_validator(mode="after")
    def apply_env_overrides(self) -> "OmniServeConfig":
        """
        Apply environment variable overrides AFTER initial values are set.

        Environment variables always take priority over code-defined values.
        """
        # Server settings
        serve_prefix = "OMNICOREAGENT_SERVE"
        background_prefix = "OMNICOREAGENT_BACKGROUND"

        if (val := _get_env(serve_prefix, "HOST")) is not None:
            self.host = val
        if (val := _get_env_int(serve_prefix, "PORT")) is not None:
            self.port = val
        if (val := _get_env_int(serve_prefix, "WORKERS")) is not None:
            self.workers = val

        # API settings
        if (val := _get_env(serve_prefix, "API_PREFIX")) is not None:
            self.api_prefix = val
        if (val := _get_env_bool(serve_prefix, "ENABLE_DOCS")) is not None:
            self.enable_docs = val
        if (val := _get_env_bool(serve_prefix, "ENABLE_REDOC")) is not None:
            self.enable_redoc = val

        # CORS settings
        if (val := _get_env_bool(serve_prefix, "CORS_ENABLED")) is not None:
            self.cors_enabled = val
        if (val := _get_env_list(serve_prefix, "CORS_ORIGINS")) is not None:
            self.cors_origins = val
        if (val := _get_env_list(serve_prefix, "CORS_METHODS")) is not None:
            self.cors_methods = val
        if (val := _get_env_list(serve_prefix, "CORS_HEADERS")) is not None:
            self.cors_headers = val
        if (val := _get_env_bool(serve_prefix, "CORS_CREDENTIALS")) is not None:
            self.cors_credentials = val

        # Authentication
        if (val := _get_env_bool(serve_prefix, "AUTH_ENABLED")) is not None:
            self.auth_enabled = val
        if (val := _get_env(serve_prefix, "AUTH_TOKEN")) is not None:
            self.auth_token = val

        # Logging
        if (val := _get_env_bool(serve_prefix, "REQUEST_LOGGING")) is not None:
            self.request_logging = val
        if (val := _get_env(serve_prefix, "LOG_LEVEL")) is not None:
            self.log_level = val

        # Timeouts
        if (val := _get_env_int(serve_prefix, "REQUEST_TIMEOUT")) is not None:
            self.request_timeout = val

        # Rate limiting
        if (val := _get_env_bool(serve_prefix, "RATE_LIMIT_ENABLED")) is not None:
            self.rate_limit_enabled = val
        if (val := _get_env_int(serve_prefix, "RATE_LIMIT_REQUESTS")) is not None:
            self.rate_limit_requests = val
        if (val := _get_env_int(serve_prefix, "RATE_LIMIT_WINDOW")) is not None:
            self.rate_limit_window = val

        # Background execution
        if (val := _get_env_bool(background_prefix, "ENABLED")) is not None:
            self.background_enabled = val
        if (val := _get_env(background_prefix, "AGENT_ID")) is not None:
            self.background_agent_id = val
        if (val := _get_env(background_prefix, "TASK_STORE")) is not None:
            self.background_task_store = val
        if (val := _get_env(background_prefix, "TASK_STORE_URL")) is not None:
            self.background_task_store_url = val
        if (val := _get_env(background_prefix, "TASK_STORE_URI")) is not None:
            self.background_task_store_uri = val
        if (val := _get_env(background_prefix, "TASK_STORE_DATABASE")) is not None:
            self.background_task_store_database = val
        if (val := _get_env(background_prefix, "TASK_STORE_PREFIX")) is not None:
            self.background_task_store_prefix = val
        if (val := _get_env(background_prefix, "TASK_STORE_COLLECTION_PREFIX")) is not None:
            self.background_task_store_collection_prefix = val
        if (val := _get_env_float(background_prefix, "TASK_STORE_CONNECT_TIMEOUT")) is not None:
            self.background_task_store_connect_timeout = val
        if (val := _get_env_bool(background_prefix, "START_WORKER")) is not None:
            self.background_start_worker = val

        self.api_prefix = normalize_api_prefix(self.api_prefix)
        self.log_level = self.log_level.upper()
        self._validate_server_config()
        self._validate_log_level()
        self._validate_auth_config()
        self._validate_rate_limit_config()
        return self

    def _validate_server_config(self) -> None:
        validate_server_bind_config(
            host=self.host,
            port=self.port,
            workers=self.workers,
        )

    def _validate_log_level(self) -> None:
        if self.log_level not in _VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
            raise ValueError(
                f"OMNICOREAGENT_SERVE_LOG_LEVEL must be one of: {allowed}"
            )

    def _validate_auth_config(self) -> None:
        if self.auth_enabled and not _has_value(self.auth_token):
            raise ValueError(
                "OMNICOREAGENT_SERVE_AUTH_TOKEN is required when "
                "OMNICOREAGENT_SERVE_AUTH_ENABLED=true"
            )

    def _validate_rate_limit_config(self) -> None:
        if not self.rate_limit_enabled:
            return
        if self.rate_limit_requests < 1:
            raise ValueError(
                "OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS must be at least 1 "
                "when rate limiting is enabled"
            )
        if self.rate_limit_window < 1:
            raise ValueError(
                "OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW must be at least 1 "
                "when rate limiting is enabled"
            )

    @classmethod
    def from_env(cls) -> "OmniServeConfig":
        """Create config from environment variables only."""
        return cls()

    def background_task_store_config(self) -> str | dict[str, Any] | None:
        """Return normalized task-store config for BackgroundAgentManager."""
        if isinstance(self.background_task_store, dict):
            return self.background_task_store
        backend = self.background_task_store or "in_memory"
        if self.background_task_store_uri:
            if backend not in {"in_memory", "mongodb"}:
                raise ValueError(
                    "OMNICOREAGENT_BACKGROUND_TASK_STORE_URI can only be used "
                    "with the MongoDB background task store"
                )
            return {
                "backend": "mongodb",
                "uri": self.background_task_store_uri,
                "database": self.background_task_store_database or "omnicoreagent",
                "collection_prefix": self.background_task_store_collection_prefix,
                "connect_timeout": self.background_task_store_connect_timeout,
            }
        if self.background_task_store_url:
            if backend == "in_memory":
                backend = "sql"
            if backend not in {"sql", "redis"}:
                raise ValueError(
                    "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL can only be used "
                    "with the SQL or Redis background task store"
                )
            return {
                "backend": backend,
                "url": self.background_task_store_url,
                "prefix": self.background_task_store_prefix,
                "connect_timeout": self.background_task_store_connect_timeout,
            }
        if self.background_task_store == "mongodb":
            raise ValueError(
                "MongoDB background task store requires "
                "OMNICOREAGENT_BACKGROUND_TASK_STORE_URI"
            )
        if self.background_task_store == "redis":
            raise ValueError(
                "Redis background task store requires "
                "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL"
            )
        return self.background_task_store


def _has_value(value: str | None) -> bool:
    return value is not None and value.strip() != ""
