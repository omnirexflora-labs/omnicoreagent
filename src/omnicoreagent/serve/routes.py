"""
OmniServe API Routes.

Defines all API endpoints for the agent server.
"""

import time
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from omnicoreagent.core.utils import logger

from .models import (
    RunRequest,
    RunResponse,
    HealthResponse,
    ReadinessResponse,
    ToolsResponse,
    ToolInfo,
    MetricsResponse,
    SessionHistoryResponse,
    EventsResponse,
    TraceResponse,
    ErrorResponse,
)
from .sse import run_agent_stream, stream_session_events

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any


def create_agent_router() -> APIRouter:
    """
    Create the agent API router with all endpoints.

    Returns:
        FastAPI APIRouter with all agent endpoints
    """
    router = APIRouter(tags=["Agent"])

    # =========================================================================
    # Health Endpoints
    # =========================================================================

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Health check",
        description="Check if the server is healthy and running.",
    )
    async def health_check(request: Request) -> HealthResponse:
        """Health check endpoint."""
        agent: AgentType = request.app.state.agent
        start_time: float = getattr(request.app.state, "start_time", time.time())
        uptime = time.time() - start_time

        return HealthResponse(
            status="healthy",
            agent_name=agent.name,
            uptime=uptime,
            version="1.0.0",
        )

    @router.get(
        "/ready",
        response_model=ReadinessResponse,
        summary="Readiness check",
        description="Check if the agent is ready to accept requests.",
    )
    async def readiness_check(request: Request) -> ReadinessResponse:
        """Readiness check endpoint."""
        agent: AgentType = request.app.state.agent

        initialized = getattr(agent, "_initialized", True)

        # Check MCP connection
        mcp_connected = True
        if hasattr(agent, "mcp_client"):
            mcp_connected = agent.mcp_client is not None

        return ReadinessResponse(
            ready=initialized,
            agent_name=agent.name,
            initialized=initialized,
            mcp_connected=mcp_connected,
        )

    # =========================================================================
    # Agent Run Endpoints
    # =========================================================================

    @router.post(
        "/run",
        summary="Run agent (SSE streaming)",
        description="Run the agent with a query and stream events via Server-Sent Events.",
        responses={
            200: {"description": "SSE stream of agent events"},
            500: {"model": ErrorResponse},
        },
    )
    async def run_agent_sse(request: Request, body: RunRequest):
        """
        Run the agent with SSE streaming.

        Events streamed:
        - `session`: Session start/end
        - `user_message`: User query received
        - `tool_call_started`: Tool execution started
        - `tool_call_result`: Tool execution result
        - `agent_thought`: Agent reasoning
        - `complete`: Final response

        The stream ends with a `complete` event containing the final response.
        """
        agent: AgentType = request.app.state.agent

        # Generate session ID if not provided
        session_id = body.session_id
        if not session_id:
            session_id = agent.generate_session_id()

        logger.info(
            f"OmniServe: SSE run request - session={session_id}, "
            f"query_length={len(body.query)}"
        )

        return StreamingResponse(
            run_agent_stream(agent, body.query, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    @router.post(
        "/run/sync",
        response_model=RunResponse,
        summary="Run agent (synchronous)",
        description="Run the agent with a query and return a JSON response.",
        responses={500: {"model": ErrorResponse}},
    )
    async def run_agent_sync(request: Request, body: RunRequest) -> RunResponse:
        """
        Run the agent synchronously.

        Returns the complete response as JSON without streaming.
        """
        agent: AgentType = request.app.state.agent

        # Generate session ID if not provided
        session_id = body.session_id
        if not session_id:
            session_id = agent.generate_session_id()

        logger.info(
            f"OmniServe: Sync run request - session={session_id}, "
            f"query_length={len(body.query)}"
        )

        try:
            result = await agent.run(body.query, session_id=session_id)

            # Convert metric to dict if it's a Usage object (dataclass)
            metric = result.get("metric")
            if metric is not None:
                if is_dataclass(metric) and not isinstance(metric, type):
                    metric = asdict(metric)
                elif hasattr(metric, "to_dict"):
                    metric = metric.to_dict()
                elif hasattr(metric, "model_dump"):
                    metric = metric.model_dump()
                elif hasattr(metric, "dict"):
                    metric = metric.dict()
                elif not isinstance(metric, dict):
                    # Fallback: try to extract attributes
                    metric = {
                        k: v for k, v in metric.__dict__.items() 
                        if not k.startswith("_")
                    }

            return RunResponse(
                response=result.get("response", ""),
                session_id=session_id,
                agent_name=result.get("agent_name", agent.name),
                metric=metric,
            )
        except Exception as e:
            logger.error(f"OmniServe: Run error - {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # =========================================================================
    # Events Endpoints
    # =========================================================================

    @router.get(
        "/events/{session_id}",
        summary="Stream session events (SSE)",
        description="Stream events for a specific session via Server-Sent Events.",
    )
    async def stream_events(request: Request, session_id: str):
        """Stream events for a session via SSE."""
        agent: AgentType = request.app.state.agent

        return StreamingResponse(
            stream_session_events(agent, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @router.get(
        "/events/{session_id}/list",
        response_model=EventsResponse,
        summary="Get session events",
        description="Get all events for a specific session as JSON.",
    )
    async def get_events(request: Request, session_id: str) -> EventsResponse:
        """Get all events for a session."""
        agent: AgentType = request.app.state.agent

        events = await agent.get_events(session_id)

        # Convert events to dicts
        events_list = []
        for event in events:
            if hasattr(event, "model_dump"):
                events_list.append(event.model_dump())
            elif hasattr(event, "dict"):
                events_list.append(event.dict())
            elif isinstance(event, dict):
                events_list.append(event)
            else:
                events_list.append({"data": str(event)})

        return EventsResponse(
            session_id=session_id,
            events=events_list,
            count=len(events_list),
        )

    @router.get(
        "/events/{session_id}/trace",
        response_model=TraceResponse,
        summary="Get session trace",
        description="Build a compact trace summary from stored session events.",
    )
    async def get_trace(request: Request, session_id: str) -> TraceResponse:
        """Get a compact trace for a session."""
        agent: AgentType = request.app.state.agent
        trace = await agent.get_trace(session_id)

        return TraceResponse(
            session_id=session_id,
            summary=trace.get("summary", {}),
            steps=trace.get("steps", []),
        )

    # =========================================================================
    # Session Endpoints
    # =========================================================================

    @router.get(
        "/sessions/{session_id}/history",
        response_model=SessionHistoryResponse,
        summary="Get session history",
        description="Get message history for a specific session.",
    )
    async def get_session_history(
        request: Request, session_id: str
    ) -> SessionHistoryResponse:
        """Get message history for a session."""
        agent: AgentType = request.app.state.agent

        messages = await agent.get_session_history(session_id)

        return SessionHistoryResponse(
            session_id=session_id,
            messages=messages,
            count=len(messages),
        )

    @router.delete(
        "/sessions/{session_id}",
        summary="Clear session history",
        description="Clear message history for a specific session.",
    )
    async def clear_session(request: Request, session_id: str):
        """Clear message history for a session."""
        agent: AgentType = request.app.state.agent

        await agent.clear_session_history(session_id)

        return {"status": "cleared", "session_id": session_id}

    # =========================================================================
    # Tools Endpoint
    # =========================================================================

    @router.get(
        "/tools",
        response_model=ToolsResponse,
        summary="List available tools",
        description="List all tools available to the agent.",
    )
    async def list_tools(request: Request) -> ToolsResponse:
        """List all available tools."""
        agent: AgentType = request.app.state.agent

        tools = await agent.list_all_available_tools()

        tool_infos = [
            ToolInfo(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                inputSchema=tool.get("inputSchema", {}),
                type=tool.get("type", "unknown"),
            )
            for tool in tools
        ]

        return ToolsResponse(tools=tool_infos, total=len(tool_infos))

    # =========================================================================
    # Metrics Endpoint
    # =========================================================================

    @router.get(
        "/metrics",
        response_model=MetricsResponse,
        summary="Get agent metrics",
        description="Get cumulative metrics for the agent.",
    )
    async def get_metrics(request: Request) -> MetricsResponse:
        """Get agent metrics."""
        agent: AgentType = request.app.state.agent

        metrics = await agent.get_metrics()

        # Ensure all required fields have default values (handle None from fresh agents)
        return MetricsResponse(
            total_requests=metrics.get("total_requests") or 0,
            total_request_tokens=metrics.get("total_request_tokens") or 0,
            total_response_tokens=metrics.get("total_response_tokens") or 0,
            total_tokens=metrics.get("total_tokens") or 0,
            total_time=metrics.get("total_time") or 0.0,
            average_time=metrics.get("average_time") or 0.0,
        )

    return router
