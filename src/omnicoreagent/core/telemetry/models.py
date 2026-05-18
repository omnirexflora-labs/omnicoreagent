from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def telemetry_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ActorType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"
    MEMORY = "memory"
    WORKSPACE = "workspace"
    GUARDRAIL = "guardrail"
    BACKGROUND = "background"
    SERVE = "serve"


class SpanStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class TraceStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ABORTED_RESOURCE_GUARD = "aborted_resource_guard"
    ABORTED_SAFETY_GUARD = "aborted_safety_guard"
    PARTIAL = "partial"


FOUNDATION_SPAN_KINDS = frozenset(
    {
        "agent.run",
        "agent.step",
        "model.call",
        "context.assembly",
        "context.compression",
        "tool.batch",
        "tool.call",
        "mcp.tool.call",
        "observation.pipeline",
        "memory.read",
        "memory.write",
        "workspace.read",
        "workspace.write",
        "workspace.delete",
        "tool.offload",
        "guardrail.check",
        "subagent.run",
        "workflow.route",
        "background.task",
        "background.run",
        "serve.request",
        "runtime.control",
    }
)

RESERVED_SPAN_KINDS = frozenset({"verifier.run", "eval.score"})

FOUNDATION_EVENT_TYPES = frozenset(
    {
        "agent_start",
        "agent_step",
        "agent_end",
        "user_message",
        "system_instruction",
        "model_call",
        "model_response",
        "model_error",
        "tool_call",
        "tool_result",
        "tool_error",
        "tool_retry",
        "tool_batch_start",
        "tool_batch_end",
        "tool_batch_error",
        "mcp_tool_call",
        "mcp_tool_result",
        "mcp_tool_error",
        "approval_request",
        "approval_granted",
        "approval_denied",
        "memory_read",
        "memory_write",
        "memory_update",
        "memory_eviction",
        "context_compression",
        "context_dropped",
        "context_restored",
        "observation_pipeline_start",
        "observation_pipeline_end",
        "observation_pipeline_error",
        "workspace_read",
        "workspace_write",
        "workspace_delete",
        "workspace_offload",
        "guardrail_check",
        "guardrail_violation",
        "resource_guard_warning",
        "resource_guard_halt",
        "safety_guard_halt",
        "planning_step",
        "reflection",
        "retry",
        "subagent_spawn",
        "subagent_result",
        "subagent_error",
        "workflow_route",
        "workflow_handoff",
        "workflow_join",
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_heartbeat",
        "background_run_retrying",
        "background_run_completed",
        "background_run_failed",
        "background_run_cancelled",
        "background_run_timeout",
        "background_run_skipped",
        "background_run_recovered",
        "background_task_scheduled",
        "serve_request_start",
        "serve_request_end",
        "serve_request_error",
        "final_answer",
        "final_state",
        "runtime_error",
        "uncaught_exception",
        "telemetry_error",
    }
)


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if is_dataclass(value):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [to_plain(item) for item in value]
    return value


def parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SerializableTelemetryRecord:
    def model_dump(self) -> dict[str, Any]:
        return to_plain(self)

    def dict(self) -> dict[str, Any]:
        return self.model_dump()

    def json(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True)


@dataclass
class TelemetryActor(SerializableTelemetryRecord):
    type: ActorType | str
    id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        self.type = ActorType(self.type)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TelemetryActor:
        if data is None:
            return cls(type=ActorType.SYSTEM)
        return cls(type=data["type"], id=data.get("id"), name=data.get("name"))


@dataclass
class TelemetryError(SerializableTelemetryRecord):
    type: str
    message: str
    retryable: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    stack: str | None = None

    @classmethod
    def from_exception(cls, exc: BaseException) -> TelemetryError:
        return cls(type=exc.__class__.__name__, message=str(exc))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TelemetryError | None:
        if data is None:
            return None
        return cls(
            type=data["type"],
            message=data["message"],
            retryable=data.get("retryable"),
            metadata=dict(data.get("metadata") or {}),
            stack=data.get("stack"),
        )


@dataclass
class TokenUsage(SerializableTelemetryRecord):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TokenUsage:
        if data is None:
            return cls()
        return cls(
            prompt_tokens=data.get("prompt_tokens"),
            completion_tokens=data.get("completion_tokens"),
            total_tokens=data.get("total_tokens"),
        )


@dataclass
class TelemetryEvent(SerializableTelemetryRecord):
    trace_id: str
    event_type: str
    actor: TelemetryActor
    event_id: str = field(default_factory=lambda: telemetry_id("event"))
    span_id: str | None = None
    parent_event_id: str | None = None
    sequence_number: int = 0
    timestamp: datetime = field(default_factory=utc_now)
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: TelemetryError | None = None
    duration_ms: int | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    estimated_cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timestamp = parse_datetime(self.timestamp) or utc_now()
        self.actor = TelemetryActor.from_dict(self.actor) if isinstance(self.actor, dict) else self.actor
        self.error = TelemetryError.from_dict(self.error) if isinstance(self.error, dict) else self.error
        self.token_usage = (
            TokenUsage.from_dict(self.token_usage)
            if isinstance(self.token_usage, dict)
            else self.token_usage
        )
        if (
            self.event_type not in FOUNDATION_EVENT_TYPES
            and not self.metadata.get("experimental")
        ):
            raise ValueError(f"Unknown telemetry event type: {self.event_type}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryEvent:
        return cls(**data)


@dataclass
class TelemetrySpan(SerializableTelemetryRecord):
    trace_id: str
    name: str
    kind: str
    actor: TelemetryActor
    span_id: str = field(default_factory=lambda: telemetry_id("span"))
    parent_span_id: str | None = None
    status: SpanStatus | str = SpanStatus.RUNNING
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: TelemetryError | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    estimated_cost_usd: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    event_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.actor = TelemetryActor.from_dict(self.actor) if isinstance(self.actor, dict) else self.actor
        self.status = SpanStatus(self.status)
        self.started_at = parse_datetime(self.started_at) or utc_now()
        self.ended_at = parse_datetime(self.ended_at)
        self.error = TelemetryError.from_dict(self.error) if isinstance(self.error, dict) else self.error
        self.token_usage = (
            TokenUsage.from_dict(self.token_usage)
            if isinstance(self.token_usage, dict)
            else self.token_usage
        )
        if self.kind in RESERVED_SPAN_KINDS:
            raise ValueError(f"Reserved span kind cannot be emitted yet: {self.kind}")
        if self.kind not in FOUNDATION_SPAN_KINDS:
            raise ValueError(f"Unknown telemetry span kind: {self.kind}")
        if self.ended_at is not None and self.duration_ms is None:
            self.duration_ms = duration_ms(self.started_at, self.ended_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetrySpan:
        return cls(**data)


@dataclass
class TelemetryTraceMetadata(SerializableTelemetryRecord):
    agent_name: str | None = None
    agent_version: str | None = None
    model_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    memory_config_version: str | None = None
    constraint_config_version: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> TelemetryTraceMetadata:
        if data is None:
            return cls()
        return cls(
            agent_name=data.get("agent_name"),
            agent_version=data.get("agent_version"),
            model_provider=data.get("model_provider"),
            model=data.get("model"),
            prompt_version=data.get("prompt_version"),
            tool_schema_version=data.get("tool_schema_version"),
            memory_config_version=data.get("memory_config_version"),
            constraint_config_version=data.get("constraint_config_version"),
            tags=list(data.get("tags") or []),
        )


@dataclass
class TelemetryTrace(SerializableTelemetryRecord):
    trace_id: str
    root_span_id: str
    status: TraceStatus | str = TraceStatus.RUNNING
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    suite_id: str | None = None
    agent_id: str | None = None
    workflow_id: str | None = None
    metadata: TelemetryTraceMetadata = field(default_factory=TelemetryTraceMetadata)
    spans: list[TelemetrySpan] = field(default_factory=list)
    events: list[TelemetryEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = TraceStatus(self.status)
        self.started_at = parse_datetime(self.started_at) or utc_now()
        self.ended_at = parse_datetime(self.ended_at)
        self.metadata = (
            TelemetryTraceMetadata.from_dict(self.metadata)
            if isinstance(self.metadata, dict)
            else self.metadata
        )
        self.spans = [
            TelemetrySpan.from_dict(span) if isinstance(span, dict) else span
            for span in self.spans
        ]
        self.events = [
            TelemetryEvent.from_dict(event) if isinstance(event, dict) else event
            for event in self.events
        ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryTrace:
        return cls(**data)


@dataclass
class TraceFilter:
    trace_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    suite_id: str | None = None
    agent_id: str | None = None
    workflow_id: str | None = None
    model: str | None = None
    status: TraceStatus | str | None = None

    def matches(self, trace: TelemetryTrace) -> bool:
        expected_status = TraceStatus(self.status) if self.status else None
        return all(
            (
                self.trace_id is None or trace.trace_id == self.trace_id,
                self.run_id is None or trace.run_id == self.run_id,
                self.session_id is None or trace.session_id == self.session_id,
                self.task_id is None or trace.task_id == self.task_id,
                self.suite_id is None or trace.suite_id == self.suite_id,
                self.agent_id is None or trace.agent_id == self.agent_id,
                self.workflow_id is None or trace.workflow_id == self.workflow_id,
                self.model is None or trace.metadata.model == self.model,
                expected_status is None or trace.status == expected_status,
            )
        )


@dataclass(frozen=True)
class TelemetryStreamScope:
    trace_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    event_types: tuple[str, ...] | None = None

    def matches(self, event: TelemetryEvent, trace: TelemetryTrace | None) -> bool:
        if self.trace_id is not None and event.trace_id != self.trace_id:
            return False
        if self.event_types is not None and event.event_type not in self.event_types:
            return False
        if trace is None:
            return self.run_id is None and self.session_id is None and self.task_id is None
        return all(
            (
                self.run_id is None or trace.run_id == self.run_id,
                self.session_id is None or trace.session_id == self.session_id,
                self.task_id is None or trace.task_id == self.task_id,
            )
        )


def duration_ms(start: datetime, end: datetime) -> int:
    return int(round((end - start).total_seconds() * 1000))
