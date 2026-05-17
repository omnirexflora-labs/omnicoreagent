"""Health and readiness routes for OmniServe."""

import time
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request

from ..models import HealthResponse, ReadinessResponse
from ..state import get_agent, get_agent_name


def create_health_router() -> APIRouter:
    """Create health/readiness endpoints."""
    router = APIRouter(tags=["Health"])

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Health check",
        description="Check if the server is healthy and running.",
    )
    async def health_check(request: Request) -> HealthResponse:
        agent = get_agent(request)
        start_time: float = getattr(request.app.state, "start_time", time.time())

        return HealthResponse(
            status="healthy",
            agent_name=get_agent_name(agent),
            uptime=time.time() - start_time,
            version=_package_version(),
        )

    @router.get(
        "/ready",
        response_model=ReadinessResponse,
        summary="Readiness check",
        description="Check if the agent is ready to accept requests.",
    )
    async def readiness_check(request: Request) -> ReadinessResponse:
        agent = get_agent(request)
        initialized = getattr(agent, "_initialized", True)
        mcp_connected = not hasattr(agent, "mcp_client") or agent.mcp_client is not None

        return ReadinessResponse(
            ready=initialized,
            agent_name=get_agent_name(agent),
            initialized=initialized,
            mcp_connected=mcp_connected,
        )

    return router


def _package_version() -> str:
    try:
        return version("omnicoreagent")
    except PackageNotFoundError:
        return "0+unknown"
