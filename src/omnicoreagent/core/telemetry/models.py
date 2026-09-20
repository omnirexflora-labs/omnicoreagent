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
    # The run is waiting for a person; it continues in a new trace segment.
    SUSPENDED = "suspended"


class CaptureState(str, Enum):
    """What a telemetry payload descriptor establishes about its value."""

    AVAILABLE = "available"
    REDACTED = "redacted"
    TRUNCATED = "truncated"
    OFFLOADED = "offloaded"
    NOT_RECORDED = "not_recorded"
    MISSING = "missing"
    INFERRED = "inferred"


class TraceEvidenceStatus(str, Enum):
    """Completeness of the execution evidence, independent of run status."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


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
        "tool_requested",
        "tool_resolved",
        "tool_call",
        "tool_result",
        "tool_error",
        "tool_observation",
        "tool_retry",
        "tool_batch_start",
        "tool_batch_end",
        "tool_batch_error",
        "mcp_tool_call",
        "mcp_tool_result",
        "mcp_tool_error",
        "mcp_reconnect",
        "approval_request",
        "approval_granted",
        "approval_denied",
        "memory_read",
        "memory_write",
        "memory_update",
        "memory_eviction",
        "context_assembly",
        "run_configuration",
        "runtime_message",
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
        "background_run_awaiting_approval",
        "background_task_scheduled",
        "serve_request_start",
        "serve_request_end",
        "serve_request_error",
        "final_answer",
        "final_state",
        "runtime_error",
        "uncaught_exception",
        "telemetry_error",
        "policy_request_created",
        "policy_decision_allow",
        "policy_decision_ask",
        "policy_decision_deny",
        "approval_request_created",
        "approval_resolved",
        "sandbox_session_created",
        "sandbox_exec_started",
        "sandbox_exec_completed",
        "sandbox_exec_failed",
        "sandbox_session_closed",
        "sandbox_workspace_sync",
        "budget_warning",
        "budget_exhausted",
        "budget_cost_incomplete",
        "run_suspended",
        "run_resumed",
        "run_steered",
        "run_interrupted",
        "policy_violation",
        "secret_access_denied",
        "secret_access_brokered",
        "network_access_denied",
        "network_access_allowed",
        "filesystem_access_denied",
        "filesystem_access_allowed",
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
class TelemetryCapture(SerializableTelemetryRecord):
    """Descriptor for the payload stored on a span or event.

    The descriptor lets evaluators distinguish an absent runtime value from a
    value intentionally excluded by privacy or retention policy. Checksums and
    references always describe the redacted representation.
    """

    state: CaptureState | str
    source: str
    role: str
    reference: str | None = None
    content_type: str | None = None
    checksum: str | None = None
    original_bytes: int | None = None
    recorded_bytes: int | None = None
    policy_version: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        self.state = CaptureState(self.state)
        self.source = str(self.source).strip()
        self.role = str(self.role).strip()
        if not self.source:
            raise ValueError("Telemetry capture source must not be empty")
        if not self.role:
            raise ValueError("Telemetry capture role must not be empty")
        for field_name in ("original_bytes", "recorded_bytes"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"Telemetry capture {field_name} must be non-negative")
        if self.state in {
            CaptureState.NOT_RECORDED,
            CaptureState.MISSING,
            CaptureState.TRUNCATED,
            CaptureState.INFERRED,
        } and not self.reason:
            raise ValueError(
                f"Telemetry capture reason is required for state {self.state.value}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TelemetryCapture | None:
        if data is None:
            return None
        return cls(**data)


@dataclass
class TelemetryProvenance(SerializableTelemetryRecord):
    """External and runtime provenance attached to one execution trace."""

    source: str = "omnicoreagent"
    adapter: str | None = None
    application_version: str | None = None
    deployment_id: str | None = None
    environment: str | None = None
    evaluation_id: str | None = None
    case_id: str | None = None
    trial_id: str | None = None
    environment_id: str | None = None
    verifier_reference: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source = str(self.source).strip()
        if not self.source:
            raise ValueError("Telemetry provenance source must not be empty")
        self.external_ids = {
            str(key): str(value)
            for key, value in dict(self.external_ids or {}).items()
            if str(value).strip()
        }
        self.extra = dict(self.extra or {})

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TelemetryProvenance:
        if data is None:
            return cls()
        known = {
            "source",
            "adapter",
            "application_version",
            "deployment_id",
            "environment",
            "evaluation_id",
            "case_id",
            "trial_id",
            "environment_id",
            "verifier_reference",
            "external_ids",
            "extra",
        }
        extra = dict(data.get("extra") or {})
        extra.update({key: value for key, value in data.items() if key not in known})
        return cls(
            **{key: data[key] for key in known if key in data and key != "extra"},
            extra=extra,
        )


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
    schema_version: int = 3
    input_capture: TelemetryCapture | None = None
    output_capture: TelemetryCapture | None = None
    # Store-local transport cursor. It is assigned on stream copies and is not
    # part of the evidence identity (event_id remains the stable reference).
    stream_cursor: str | None = None

    def __post_init__(self) -> None:
        self.timestamp = parse_datetime(self.timestamp) or utc_now()
        self.actor = TelemetryActor.from_dict(self.actor) if isinstance(self.actor, dict) else self.actor
        self.error = TelemetryError.from_dict(self.error) if isinstance(self.error, dict) else self.error
        self.token_usage = (
            TokenUsage.from_dict(self.token_usage)
            if isinstance(self.token_usage, dict)
            else self.token_usage
        )
        self.input_capture = TelemetryCapture.from_dict(self.input_capture) if isinstance(self.input_capture, dict) else self.input_capture
        self.output_capture = TelemetryCapture.from_dict(self.output_capture) if isinstance(self.output_capture, dict) else self.output_capture
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("Telemetry event schema_version must be a positive integer")
        if (
            self.event_type not in FOUNDATION_EVENT_TYPES
            and not self.metadata.get("experimental")
        ):
            raise ValueError(f"Unknown telemetry event type: {self.event_type}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryEvent:
        payload = dict(data)
        payload.setdefault("schema_version", 1)
        return cls(**payload)


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
    schema_version: int = 3
    input_capture: TelemetryCapture | None = None
    output_capture: TelemetryCapture | None = None

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
        self.input_capture = TelemetryCapture.from_dict(self.input_capture) if isinstance(self.input_capture, dict) else self.input_capture
        self.output_capture = TelemetryCapture.from_dict(self.output_capture) if isinstance(self.output_capture, dict) else self.output_capture
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("Telemetry span schema_version must be a positive integer")
        if self.kind in RESERVED_SPAN_KINDS:
            raise ValueError(f"Reserved span kind cannot be emitted yet: {self.kind}")
        if self.kind not in FOUNDATION_SPAN_KINDS:
            raise ValueError(f"Unknown telemetry span kind: {self.kind}")
        if self.ended_at is not None and self.duration_ms is None:
            self.duration_ms = duration_ms(self.started_at, self.ended_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetrySpan:
        payload = dict(data)
        payload.setdefault("schema_version", 1)
        return cls(**payload)


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
    guardrail_mode: str | None = None
    guardrail_config_version: str | None = None
    privacy_config_version: str | None = None
    telemetry_config_version: str | None = None
    telemetry_storage: str | None = None
    telemetry_payload_storage: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> TelemetryTraceMetadata:
        if data is None:
            return cls()
        known = {
            "agent_name",
            "agent_version",
            "model_provider",
            "model",
            "prompt_version",
            "tool_schema_version",
            "memory_config_version",
            "constraint_config_version",
            "guardrail_mode",
            "guardrail_config_version",
            "privacy_config_version",
            "telemetry_config_version",
            "telemetry_storage",
            "telemetry_payload_storage",
            "tags",
            "extra",
        }
        extra = dict(data.get("extra") or {})
        extra.update({key: value for key, value in data.items() if key not in known})
        return cls(
            agent_name=data.get("agent_name"),
            agent_version=data.get("agent_version"),
            model_provider=data.get("model_provider"),
            model=data.get("model"),
            prompt_version=data.get("prompt_version"),
            tool_schema_version=data.get("tool_schema_version"),
            memory_config_version=data.get("memory_config_version"),
            constraint_config_version=data.get("constraint_config_version"),
            guardrail_mode=data.get("guardrail_mode"),
            guardrail_config_version=data.get("guardrail_config_version"),
            privacy_config_version=data.get("privacy_config_version"),
            telemetry_config_version=data.get("telemetry_config_version"),
            telemetry_storage=data.get("telemetry_storage"),
            telemetry_payload_storage=data.get("telemetry_payload_storage"),
            tags=list(data.get("tags") or []),
            extra=extra,
        )


@dataclass
class TelemetryTrace(SerializableTelemetryRecord):
    trace_id: str
    root_span_id: str
    parent_trace_id: str | None = None
    parent_span_id: str | None = None
    incomplete: bool = False
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
    schema_version: int = 3
    execution_surface: str = "interactive"
    evidence_status: TraceEvidenceStatus | str = TraceEvidenceStatus.COMPLETE
    provenance: TelemetryProvenance = field(default_factory=TelemetryProvenance)

    def __post_init__(self) -> None:
        self.status = TraceStatus(self.status)
        self.evidence_status = TraceEvidenceStatus(self.evidence_status)
        self.execution_surface = str(self.execution_surface).strip().lower()
        if not self.execution_surface:
            raise ValueError("Telemetry trace execution_surface must not be empty")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("Telemetry trace schema_version must be a positive integer")
        self.started_at = parse_datetime(self.started_at) or utc_now()
        self.ended_at = parse_datetime(self.ended_at)
        self.metadata = (
            TelemetryTraceMetadata.from_dict(self.metadata)
            if isinstance(self.metadata, dict)
            else self.metadata
        )
        self.provenance = (
            TelemetryProvenance.from_dict(self.provenance)
            if isinstance(self.provenance, dict)
            else self.provenance
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
        payload = dict(data)
        payload.setdefault("schema_version", 1)
        payload.setdefault("evidence_status", TraceEvidenceStatus.UNKNOWN.value)
        return cls(**payload)


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
