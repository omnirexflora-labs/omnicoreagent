"""OmniServe route composition."""

from fastapi import APIRouter

from .background import create_background_router
from .health import create_health_router
from .metrics import create_metrics_router
from .runs import create_runs_router
from .sessions import create_sessions_router
from .tools import create_tools_router


def create_agent_router() -> APIRouter:
    """Create the complete OmniServe API router."""
    router = APIRouter()
    router.include_router(create_health_router())
    router.include_router(create_runs_router())
    router.include_router(create_sessions_router())
    router.include_router(create_tools_router())
    router.include_router(create_metrics_router())
    router.include_router(create_background_router())
    return router
