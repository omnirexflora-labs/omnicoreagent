"""Agent usage metrics routes for OmniServe."""

from fastapi import APIRouter, Request

from ..models import MetricsResponse
from ..state import get_agent


def create_metrics_router() -> APIRouter:
    """Create agent usage metrics endpoints."""
    router = APIRouter(tags=["Metrics"])

    @router.get(
        "/metrics",
        response_model=MetricsResponse,
        summary="Get agent metrics",
        description="Get cumulative usage metrics from the agent runtime.",
    )
    async def get_metrics(request: Request) -> MetricsResponse:
        agent = get_agent(request)
        metrics = await agent.get_metrics()

        return MetricsResponse(
            total_requests=metrics.get("total_requests") or 0,
            total_request_tokens=metrics.get("total_request_tokens") or 0,
            total_response_tokens=metrics.get("total_response_tokens") or 0,
            total_tokens=metrics.get("total_tokens") or 0,
            total_time=metrics.get("total_time") or 0.0,
            average_time=metrics.get("average_time") or 0.0,
        )

    return router
