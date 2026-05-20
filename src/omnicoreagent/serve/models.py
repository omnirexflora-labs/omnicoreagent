"""
OmniServe Request/Response Models.

Pydantic models for API request/response schemas.
"""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from omnicoreagent.background import (
    BackgroundAgentSpec,
    BackgroundRun,
    BackgroundScheduleState,
    BackgroundTaskSpec,
    OverlapPolicy,
    RetryPolicy,
    ScheduleSpec,
    SessionPolicy,
    WorkspacePolicy,
)


# =============================================================================
# Request Models
# =============================================================================


class RunRequest(BaseModel):
    """Request model for agent run endpoint."""

    query: str = Field(..., description="The query/prompt for the agent")
    session_id: Optional[str] = Field(
        None, description="Optional session ID for conversation continuity"
    )


class BackgroundAgentRegistrationRequest(BaseModel):
    """Register the served agent or an agent spec for background work."""

    agent_id: str | None = Field(
        default=None,
        description="Agent id. Defaults to OmniServe background_agent_id.",
    )
    replace: bool = Field(default=False, description="Replace an existing agent spec")
    spec: BackgroundAgentSpec | None = Field(
        default=None,
        description="Optional agent spec. Omit to register the served agent.",
    )


class BackgroundTaskCreateRequest(BaseModel):
    """Create a background task."""

    task_id: str = Field(..., description="Stable task identifier")
    agent_id: str | None = Field(
        default=None,
        description="Agent id. Defaults to OmniServe background_agent_id.",
    )
    query: str = Field(..., description="Instruction executed for each run")
    schedule: ScheduleSpec = Field(..., description="Manual, once, interval, or cron")
    enabled: bool = Field(default=True, description="Whether the scheduler may run it")
    timeout_seconds: int | None = Field(default=None, description="Per-run timeout")
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    overlap_policy: OverlapPolicy = Field(default=OverlapPolicy.SKIP_IF_RUNNING)
    session_policy: SessionPolicy = Field(default_factory=SessionPolicy)
    workspace_policy: WorkspacePolicy = Field(default_factory=WorkspacePolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)
    replace: bool = Field(default=False, description="Replace an existing task")


class BackgroundTaskPatchRequest(BaseModel):
    """Patch mutable fields on a background task."""

    query: str | None = None
    schedule: ScheduleSpec | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = None
    retry_policy: RetryPolicy | None = None
    overlap_policy: OverlapPolicy | None = None
    session_policy: SessionPolicy | None = None
    workspace_policy: WorkspacePolicy | None = None
    metadata: dict[str, Any] | None = None


class BackgroundTaskRunRequest(BaseModel):
    """Queue a manual run, optionally waiting for terminal state."""

    query: str | None = Field(
        default=None,
        description="Optional one-off query override for this run",
    )
    wait: bool = Field(
        default=False,
        description="Wait for the run to reach a terminal status before returning",
    )


# =============================================================================
# Response Models
# =============================================================================


class RunResponse(BaseModel):
    """Response model for synchronous agent run."""

    response: str = Field(..., description="Agent's response")
    session_id: str = Field(..., description="Session ID for this conversation")
    agent_name: str = Field(..., description="Name of the agent")
    metric: Optional[dict[str, Any]] = Field(
        None, description="Optional metrics for this run"
    )
    trace_id: Optional[str] = Field(
        None, description="Telemetry trace ID for this run"
    )
    run_id: Optional[str] = Field(
        None, description="Runtime run ID for this run"
    )


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""

    status: str = Field(..., description="Health status ('healthy' or 'unhealthy')")
    agent_name: str = Field(..., description="Name of the agent")
    uptime: float = Field(..., description="Server uptime in seconds")
    version: str = Field(default="0+unknown", description="OmniCoreAgent version")


class ReadinessResponse(BaseModel):
    """Response model for readiness check endpoint."""

    ready: bool = Field(..., description="Whether the agent is ready")
    agent_name: str = Field(..., description="Name of the agent")
    initialized: bool = Field(..., description="Whether the agent is initialized")
    mcp_connected: bool = Field(..., description="Whether MCP servers are connected")


class ToolInfo(BaseModel):
    """Information about an available tool."""

    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description")
    model_config = ConfigDict(populate_by_name=True)

    input_schema: dict[str, Any] = Field(
        default_factory=dict, alias="inputSchema", description="Tool input schema"
    )
    type: str = Field(..., description="Tool type ('mcp' or 'local')")


class ToolsResponse(BaseModel):
    """Response model for tools listing endpoint."""

    tools: list[ToolInfo] = Field(..., description="List of available tools")
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
    messages: list[dict[str, Any]] = Field(..., description="Message history")
    count: int = Field(..., description="Number of messages")


class EventsResponse(BaseModel):
    """Response model for telemetry events endpoint."""

    session_id: str = Field(..., description="Session ID")
    events: list[dict[str, Any]] = Field(..., description="Telemetry events list")
    count: int = Field(..., description="Number of telemetry events")


class TraceResponse(BaseModel):
    """Response model for session telemetry trace summary endpoint."""

    session_id: str = Field(..., description="Session ID")
    summary: dict[str, Any] = Field(..., description="Telemetry trace summary")
    steps: list[dict[str, Any]] = Field(..., description="Ordered telemetry events")


class TelemetryEventsResponse(BaseModel):
    """Response model for filtered telemetry events."""

    filters: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(..., description="Telemetry events list")
    count: int = Field(..., description="Number of telemetry events")


class TelemetryTraceDetailResponse(BaseModel):
    """Response model for one telemetry trace."""

    filters: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(..., description="Telemetry trace summary")
    trace: dict[str, Any] = Field(..., description="Full telemetry trace")


class TelemetryTraceListResponse(BaseModel):
    """Response model for filtered telemetry traces."""

    filters: dict[str, Any] = Field(default_factory=dict)
    traces: list[dict[str, Any]] = Field(..., description="Telemetry traces")
    count: int = Field(..., description="Number of telemetry traces")


class BackgroundStatusResponse(BaseModel):
    """Simple status response for background control endpoints."""

    status: str = Field(..., description="Operation status")


class BackgroundManagerStatusResponse(BaseModel):
    """Inspectable background manager state."""

    running: bool
    initialized: bool
    worker_id: str
    lease_seconds: float
    agents: int
    tasks: int
    runs: int
    active_runs: int
    status_counts: dict[str, int]


class BackgroundTaskStatusResponse(BaseModel):
    """Inspectable status for one background task."""

    task_id: str
    agent_id: str
    enabled: bool
    schedule: ScheduleSpec
    schedule_state: BackgroundScheduleState | None
    runs: int
    active_runs: int
    status_counts: dict[str, int]
    latest_run: BackgroundRun | None


class BackgroundAgentsResponse(BaseModel):
    """Response model for background agent listing."""

    agents: list[BackgroundAgentSpec]
    total: int


class BackgroundTasksResponse(BaseModel):
    """Response model for background task listing."""

    tasks: list[BackgroundTaskSpec]
    total: int


class BackgroundRunsResponse(BaseModel):
    """Response model for background run listing."""

    runs: list[BackgroundRun]
    total: int


class BackgroundRunEventsResponse(BaseModel):
    """Response model for background run event replay."""

    run_id: str
    events: list[dict[str, Any]]
    count: int


class BackgroundRunWorkspaceResponse(BaseModel):
    """Response model for background run workspace inspection."""

    run_id: str
    task_id: str
    agent_id: str
    workspace_path: str
    files: list[dict[str, Any]]


class HttpErrorResponse(BaseModel):
    """Response shape produced by FastAPI HTTPException."""

    detail: Any = Field(..., description="Error detail")


class BackgroundRunTimeoutDetail(BaseModel):
    """Detail payload for a background run wait timeout."""

    message: str = Field(..., description="Timeout message")
    run_id: str = Field(..., description="Run that can be inspected later")
    task_id: str = Field(..., description="Task that created the run")
    status: str = Field(..., description="Latest run status")
    wait_timeout_seconds: float | None = Field(
        None, description="Background run wait budget applied to the request"
    )
    request_timeout_seconds: float | None = Field(
        None, description="Outer HTTP request timeout"
    )


class BackgroundRunTimeoutResponse(BaseModel):
    """HTTPException response for background run wait timeout."""

    detail: BackgroundRunTimeoutDetail


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")
