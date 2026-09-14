"""
Core AI agent harness components.

Exports are resolved lazily to keep core package import cheap and free of
provider/runtime side effects.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "ReactAgent",
    "MemoryRouter",
    "LLMConnection",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "AgentConfig",
    "ToolCall",
    "UsageLimits",
    "Usage",
    "UsageLimitExceeded",
    "TelemetryConfig",
    "TelemetryRecorder",
    "TelemetryStream",
    "TelemetryExporter",
    "TelemetryExportResult",
    "TelemetryExportError",
    "OTelTraceMapper",
    "OTLPHttpTelemetryExporter",
    "LangSmithTelemetryExporter",
    "OpikTelemetryExporter",
    "InMemoryTelemetryExporter",
    "JsonlTelemetryExporter",
    "build_telemetry_exporter",
    "ActorType",
    "CaptureState",
    "TelemetryEvent",
    "TelemetryActor",
    "TelemetryCapture",
    "TelemetryError",
    "TelemetrySpan",
    "TelemetryTrace",
    "TelemetryTraceMetadata",
    "TelemetryProvenance",
    "TelemetryStreamScope",
    "TraceFilter",
    "TraceEvidenceStatus",
    "TraceStatus",
    "SpanStatus",
    "TokenUsage",
    "InMemoryTelemetryStore",
    "JsonlTelemetryStore",
    "AbstractTelemetryStore",
    "TelemetryPayloadStore",
    "LocalTelemetryPayloadStore",
    "WorkspaceTelemetryPayloadStore",
    "TelemetryPayloadError",
    "redact_payload",
    "EvidenceReference",
    "EvidenceValidationError",
    "GenericTraceEvidenceAdapter",
    "OmniCoreEvidenceAdapter",
    "PortableExecutionEvidence",
]

_EXPORTS = {
    "ReactAgent": ("omnicoreagent.core.agents", "ReactAgent"),
    "MemoryRouter": ("omnicoreagent.core.memory_store", "MemoryRouter"),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "ToolRegistry": ("omnicoreagent.core.tools", "ToolRegistry"),
    "Tool": ("omnicoreagent.core.tools", "Tool"),
    "AgentConfig": ("omnicoreagent.core.runtime.config", "AgentConfig"),
    "ToolCall": ("omnicoreagent.core.types", "ToolCall"),
    "UsageLimits": ("omnicoreagent.core.token_usage", "UsageLimits"),
    "Usage": ("omnicoreagent.core.token_usage", "Usage"),
    "UsageLimitExceeded": ("omnicoreagent.core.token_usage", "UsageLimitExceeded"),
    "TelemetryConfig": ("omnicoreagent.core.telemetry", "TelemetryConfig"),
    "TelemetryPayloadStore": (
        "omnicoreagent.core.telemetry",
        "TelemetryPayloadStore",
    ),
    "LocalTelemetryPayloadStore": (
        "omnicoreagent.core.telemetry",
        "LocalTelemetryPayloadStore",
    ),
    "WorkspaceTelemetryPayloadStore": (
        "omnicoreagent.core.telemetry",
        "WorkspaceTelemetryPayloadStore",
    ),
    "TelemetryPayloadError": (
        "omnicoreagent.core.telemetry",
        "TelemetryPayloadError",
    ),
    "redact_payload": ("omnicoreagent.core.telemetry", "redact_payload"),
    "EvidenceReference": ("omnicoreagent.core.telemetry", "EvidenceReference"),
    "EvidenceValidationError": (
        "omnicoreagent.core.telemetry",
        "EvidenceValidationError",
    ),
    "GenericTraceEvidenceAdapter": (
        "omnicoreagent.core.telemetry",
        "GenericTraceEvidenceAdapter",
    ),
    "OmniCoreEvidenceAdapter": (
        "omnicoreagent.core.telemetry",
        "OmniCoreEvidenceAdapter",
    ),
    "PortableExecutionEvidence": (
        "omnicoreagent.core.telemetry",
        "PortableExecutionEvidence",
    ),
    "TelemetryRecorder": ("omnicoreagent.core.telemetry", "TelemetryRecorder"),
    "TelemetryStream": ("omnicoreagent.core.telemetry", "TelemetryStream"),
    "TelemetryExporter": ("omnicoreagent.core.telemetry", "TelemetryExporter"),
    "TelemetryExportResult": ("omnicoreagent.core.telemetry", "TelemetryExportResult"),
    "TelemetryExportError": ("omnicoreagent.core.telemetry", "TelemetryExportError"),
    "OTelTraceMapper": ("omnicoreagent.core.telemetry", "OTelTraceMapper"),
    "OTLPHttpTelemetryExporter": (
        "omnicoreagent.core.telemetry",
        "OTLPHttpTelemetryExporter",
    ),
    "LangSmithTelemetryExporter": (
        "omnicoreagent.core.telemetry",
        "LangSmithTelemetryExporter",
    ),
    "OpikTelemetryExporter": ("omnicoreagent.core.telemetry", "OpikTelemetryExporter"),
    "InMemoryTelemetryExporter": (
        "omnicoreagent.core.telemetry",
        "InMemoryTelemetryExporter",
    ),
    "JsonlTelemetryExporter": (
        "omnicoreagent.core.telemetry",
        "JsonlTelemetryExporter",
    ),
    "build_telemetry_exporter": (
        "omnicoreagent.core.telemetry",
        "build_telemetry_exporter",
    ),
    "ActorType": ("omnicoreagent.core.telemetry", "ActorType"),
    "CaptureState": ("omnicoreagent.core.telemetry", "CaptureState"),
    "TelemetryEvent": ("omnicoreagent.core.telemetry", "TelemetryEvent"),
    "TelemetryActor": ("omnicoreagent.core.telemetry", "TelemetryActor"),
    "TelemetryCapture": ("omnicoreagent.core.telemetry", "TelemetryCapture"),
    "TelemetryError": ("omnicoreagent.core.telemetry", "TelemetryError"),
    "TelemetrySpan": ("omnicoreagent.core.telemetry", "TelemetrySpan"),
    "TelemetryTrace": ("omnicoreagent.core.telemetry", "TelemetryTrace"),
    "TelemetryTraceMetadata": (
        "omnicoreagent.core.telemetry",
        "TelemetryTraceMetadata",
    ),
    "TelemetryProvenance": ("omnicoreagent.core.telemetry", "TelemetryProvenance"),
    "TelemetryStreamScope": ("omnicoreagent.core.telemetry", "TelemetryStreamScope"),
    "TraceFilter": ("omnicoreagent.core.telemetry", "TraceFilter"),
    "TraceEvidenceStatus": (
        "omnicoreagent.core.telemetry",
        "TraceEvidenceStatus",
    ),
    "TraceStatus": ("omnicoreagent.core.telemetry", "TraceStatus"),
    "SpanStatus": ("omnicoreagent.core.telemetry", "SpanStatus"),
    "TokenUsage": ("omnicoreagent.core.telemetry", "TokenUsage"),
    "InMemoryTelemetryStore": (
        "omnicoreagent.core.telemetry",
        "InMemoryTelemetryStore",
    ),
    "JsonlTelemetryStore": ("omnicoreagent.core.telemetry", "JsonlTelemetryStore"),
    "AbstractTelemetryStore": (
        "omnicoreagent.core.telemetry",
        "AbstractTelemetryStore",
    ),
}

_OPTIONAL_EXPORTS = {
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store", "postgres"),
}


def __getattr__(name: str) -> Any:
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
