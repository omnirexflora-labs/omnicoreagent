"""Health and readiness routes for OmniServe."""

import time
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request

from ..models import HealthResponse, ReadinessResponse
from ..readiness import evaluate_readiness
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
        readiness = evaluate_readiness(request)

        return ReadinessResponse(
            ready=readiness.ready,
            agent_name=readiness.agent_name,
            initialized=readiness.initialized,
            mcp_connected=readiness.mcp_connected,
        )

    return router


def _package_version() -> str:
    try:
        return version("omnicoreagent")
    except PackageNotFoundError:
        return "0+unknown"
