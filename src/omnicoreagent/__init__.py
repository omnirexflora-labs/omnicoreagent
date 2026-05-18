"""
OmniCoreAgent agent harness runtime.

Package exports are resolved lazily so importing ``omnicoreagent`` stays light.
Provider clients, dotenv loading in user code, and optional integrations should
only be touched when the corresponding runtime object is requested.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "__version__",
    # Core
    "ReactAgent",
    "MemoryRouter",
    "LLMConnection",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "logger",
    # Agents
    "OmniCoreAgent",
    "BackgroundAgentManager",
    "BackgroundAgentSpec",
    "BackgroundTaskSpec",
    "BackgroundScheduleState",
    "BackgroundRun",
    "BackgroundAttempt",
    "ScheduleSpec",
    "RetryPolicy",
    "OverlapPolicy",
    "SessionPolicy",
    "WorkspacePolicy",
    "RunStatus",
    "TaskStoreBackend",
    "TaskStoreConfig",
    "AbstractTaskStore",
    "TaskStoreRouter",
    "InMemoryTaskStore",
    "SqlTaskStore",
    "RedisTaskStore",
    "MongoDbTaskStore",
    "ParallelAgent",
    "SequentialAgent",
    "RouterAgent",
    # MCP
    "MCPClient",
    # OmniServe
    "OmniServe",
    "OmniServeConfig",
    "TelemetryConfig",
    "TelemetryRecorder",
    "TelemetryStream",
    "ActorType",
    "TelemetryEvent",
    "TelemetryActor",
    "TelemetryError",
    "TelemetrySpan",
    "TelemetryTrace",
    "TelemetryTraceMetadata",
    "TelemetryStreamScope",
    "TraceFilter",
    "TraceStatus",
    "SpanStatus",
    "TokenUsage",
    "InMemoryTelemetryStore",
    "JsonlTelemetryStore",
    "AbstractTelemetryStore",
]

_EXPORTS = {
    "ReactAgent": ("omnicoreagent.core.agents", "ReactAgent"),
    "MemoryRouter": ("omnicoreagent.core.memory_store", "MemoryRouter"),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "ToolRegistry": ("omnicoreagent.core.tools", "ToolRegistry"),
    "Tool": ("omnicoreagent.core.tools", "Tool"),
    "logger": ("omnicoreagent.core.logging", "logger"),
    "OmniCoreAgent": (
        "omnicoreagent.core.runtime.omnicore_agent",
        "OmniCoreAgent",
    ),
    "MCPClient": ("omnicoreagent.mcp_clients_connection", "MCPClient"),
    "ParallelAgent": ("omnicoreagent.workflows.parallel_agent", "ParallelAgent"),
    "SequentialAgent": ("omnicoreagent.workflows.sequential_agent", "SequentialAgent"),
    "RouterAgent": ("omnicoreagent.workflows.router_agent", "RouterAgent"),
    "BackgroundAgentManager": ("omnicoreagent.background", "BackgroundAgentManager"),
    "BackgroundAgentSpec": ("omnicoreagent.background", "BackgroundAgentSpec"),
    "BackgroundTaskSpec": ("omnicoreagent.background", "BackgroundTaskSpec"),
    "BackgroundScheduleState": (
        "omnicoreagent.background",
        "BackgroundScheduleState",
    ),
    "BackgroundRun": ("omnicoreagent.background", "BackgroundRun"),
    "BackgroundAttempt": ("omnicoreagent.background", "BackgroundAttempt"),
    "ScheduleSpec": ("omnicoreagent.background", "ScheduleSpec"),
    "RetryPolicy": ("omnicoreagent.background", "RetryPolicy"),
    "OverlapPolicy": ("omnicoreagent.background", "OverlapPolicy"),
    "SessionPolicy": ("omnicoreagent.background", "SessionPolicy"),
    "WorkspacePolicy": ("omnicoreagent.background", "WorkspacePolicy"),
    "RunStatus": ("omnicoreagent.background", "RunStatus"),
    "TaskStoreBackend": ("omnicoreagent.background", "TaskStoreBackend"),
    "TaskStoreConfig": ("omnicoreagent.background", "TaskStoreConfig"),
    "AbstractTaskStore": ("omnicoreagent.background", "AbstractTaskStore"),
    "TaskStoreRouter": ("omnicoreagent.background", "TaskStoreRouter"),
    "InMemoryTaskStore": ("omnicoreagent.background", "InMemoryTaskStore"),
    "SqlTaskStore": ("omnicoreagent.background", "SqlTaskStore"),
    "RedisTaskStore": ("omnicoreagent.background", "RedisTaskStore"),
    "MongoDbTaskStore": ("omnicoreagent.background", "MongoDbTaskStore"),
    "TelemetryConfig": ("omnicoreagent.core.telemetry", "TelemetryConfig"),
    "TelemetryRecorder": ("omnicoreagent.core.telemetry", "TelemetryRecorder"),
    "TelemetryStream": ("omnicoreagent.core.telemetry", "TelemetryStream"),
    "ActorType": ("omnicoreagent.core.telemetry", "ActorType"),
    "TelemetryEvent": ("omnicoreagent.core.telemetry", "TelemetryEvent"),
    "TelemetryActor": ("omnicoreagent.core.telemetry", "TelemetryActor"),
    "TelemetryError": ("omnicoreagent.core.telemetry", "TelemetryError"),
    "TelemetrySpan": ("omnicoreagent.core.telemetry", "TelemetrySpan"),
    "TelemetryTrace": ("omnicoreagent.core.telemetry", "TelemetryTrace"),
    "TelemetryTraceMetadata": ("omnicoreagent.core.telemetry", "TelemetryTraceMetadata"),
    "TelemetryStreamScope": ("omnicoreagent.core.telemetry", "TelemetryStreamScope"),
    "TraceFilter": ("omnicoreagent.core.telemetry", "TraceFilter"),
    "TraceStatus": ("omnicoreagent.core.telemetry", "TraceStatus"),
    "SpanStatus": ("omnicoreagent.core.telemetry", "SpanStatus"),
    "TokenUsage": ("omnicoreagent.core.telemetry", "TokenUsage"),
    "InMemoryTelemetryStore": ("omnicoreagent.core.telemetry", "InMemoryTelemetryStore"),
    "JsonlTelemetryStore": ("omnicoreagent.core.telemetry", "JsonlTelemetryStore"),
    "AbstractTelemetryStore": ("omnicoreagent.core.telemetry", "AbstractTelemetryStore"),
}

_OPTIONAL_EXPORTS = {
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store", "postgres"),
    "OmniServe": ("omnicoreagent.serve", "serve"),
    "OmniServeConfig": ("omnicoreagent.serve", "serve"),
}


def __getattr__(name: str) -> Any:
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version("omnicoreagent")
        except PackageNotFoundError:
            value = "0+unknown"
        globals()[name] = value
        return value

    if name in _EXPORTS:
        module_name, attr_name = _EXPORTS[name]
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value

    if name in _OPTIONAL_EXPORTS:
        from omnicoreagent._optional import load_optional

        module_name, extra = _OPTIONAL_EXPORTS[name]
        value = load_optional(
            name,
            extra,
            lambda: getattr(import_module(module_name), name),
        )
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
