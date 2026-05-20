"""OmniServe middleware setup."""

from fastapi import FastAPI

from ..config import OmniServeConfig
from .auth import add_auth_middleware
from .cors import add_cors_middleware
from .errors import add_error_handling_middleware
from .logging import add_request_logging_middleware
from .rate_limit import add_rate_limit_middleware
from .timeout import add_timeout_middleware


def setup_all_middleware(app: FastAPI, config: OmniServeConfig) -> None:
    """Set up middleware in execution order."""
    add_error_handling_middleware(app)
    add_timeout_middleware(app, config)
    add_auth_middleware(app, config)
    add_rate_limit_middleware(app, config)
    add_request_logging_middleware(app, config)
    add_cors_middleware(app, config)
