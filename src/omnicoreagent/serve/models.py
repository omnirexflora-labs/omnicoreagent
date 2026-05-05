"""
OmniServe Request/Response Models.

Pydantic models for API request/response schemas.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# =============================================================================
# Request Models
# =============================================================================


class RunRequest(BaseModel):
    """Request model for agent run endpoint."""

    query: str = Field(..., description="The query/prompt for the agent")
    session_id: Optional[str] = Field(
        None, description="Optional session ID for conversation continuity"
    )


class ClearSessionRequest(BaseModel):
    """Request model for clearing session history."""

    session_id: Optional[str] = Field(
        None, description="Session ID to clear. If None, clears all sessions."
    )


# =============================================================================
# Response Models
# =============================================================================


class RunResponse(BaseModel):
    """Response model for synchronous agent run."""

    response: str = Field(..., description="Agent's response")
    session_id: str = Field(..., description="Session ID for this conversation")
    agent_name: str = Field(..., description="Name of the agent")
    metric: Optional[Dict[str, Any]] = Field(
        None, description="Optional metrics for this run"
    )


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str = Field(..., description="Health status ('healthy' or 'unhealthy')")
    agent_name: str = Field(..., description="Name of the agent")
    uptime: float = Field(..., description="Server uptime in seconds")
    version: str = Field(default="1.0.0", description="OmniServe version")


class ReadinessResponse(BaseModel):
    """Response model for readiness check endpoint."""

    ready: bool = Field(..., description="Whether the agent is ready")
    agent_name: str = Field(..., description="Name of the agent")
    initialized: bool = Field(..., description="Whether the agent is initialized")
    mcp_connected: bool = Field(
        ..., description="Whether MCP servers are connected"
    )


class ToolInfo(BaseModel):
    """Information about an available tool."""

    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    input_schema: Dict[str, Any] = Field(
        default_factory=dict, alias="inputSchema", description="Tool input schema"
    )
    type: str = Field(..., description="Tool type ('mcp' or 'local')")

    class Config:
        """Pydantic config."""

        populate_by_name = True


class ToolsResponse(BaseModel):
    """Response model for tools listing endpoint."""

    tools: List[ToolInfo] = Field(..., description="List of available tools")
    total: int = Field(..., description="Total number of tools")


class MetricsResponse(BaseModel):
    """Response model for metrics endpoint."""

    total_requests: int = Field(0, description="Total number of requests")
    total_request_tokens: int = Field(0, description="Total request tokens used")
    total_response_tokens: int = Field(0, description="Total response tokens used")
    total_tokens: int = Field(0, description="Total tokens used")
    total_time: float = Field(0, description="Total processing time in seconds")
    average_time: float = Field(0, description="Average time per request")


class SessionHistoryResponse(BaseModel):
    """Response model for session history endpoint."""

    session_id: str = Field(..., description="Session ID")
    messages: List[Dict[str, Any]] = Field(..., description="Message history")
    count: int = Field(..., description="Number of messages")


class EventsResponse(BaseModel):
    """Response model for events endpoint."""

    session_id: str = Field(..., description="Session ID")
    events: List[Dict[str, Any]] = Field(..., description="Events list")
    count: int = Field(..., description="Number of events")


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")
