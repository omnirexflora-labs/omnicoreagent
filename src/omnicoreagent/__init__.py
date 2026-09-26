"""
OmniCoreAgent agent harness runtime.

Package exports are resolved lazily so importing ``omnicoreagent`` stays light.
Provider clients, dotenv loading in user code, and optional integrations should
only be touched when the corresponding runtime object is requested.
"""

from importlib import import_module
import sys
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
    # MCP
    "MCPClient",
    # OmniServe
    "OmniServe",
    "OmniServeConfig",
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
    "PORTABLE_EVIDENCE_CONTRACT",
    "PORTABLE_EVIDENCE_SCHEMA",
    "PORTABLE_EVIDENCE_SCHEMA_VERSION",
    "validate_portable_evidence_document",
    # Governance
    "GovernanceEngine",
    "PolicyEvaluator",
    "PolicyEnvelope",
    "PolicyRule",
    "PolicyRuleSet",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyMode",
    "PolicyProfile",
    "PolicyConstraints",
    "PolicyRuleConditions",
    "PolicyProvenance",
    "PolicySource",
    "PolicyBudget",
    "AuthorityRequest",
    "AuthorityTarget",
    "CapabilityDescriptor",
    "DescriptorSource",
    "DescriptorTrust",
    "TargetMatcher",
    "ReasonCode",
    "ApprovalRequest",
    "ApprovalResult",
    "ApprovalResolver",
    "StaticApprovalResolver",
    "DenyAllApprovalResolver",
    "GovernanceError",
    "ApprovalInvalidError",
    "AuditRequiredError",
    "PolicyLoadError",
    "PolicyEvaluationError",
    "PolicyDeniedError",
    "ApprovalRequiredError",
    "ApprovalExpiredError",
    "SandboxRequiredError",
    "BudgetExceededError",
    "UnknownCapabilityError",
    "UngovernedCapabilityError",
    "build_default_policy",
    "attach_policy_hash",
    "canonical_policy_payload",
    "discover_policy_file",
    "load_policy",
    "load_policy_file",
    "policy_from_mapping",
    "policy_hash",
    "network_authority_request",
    "network_capability_descriptor",
    "package_install_authority_request",
    "package_install_capability_descriptor",
    "filesystem_authority_request",
    "filesystem_capability_descriptor",
    "secret_authority_request",
    "secret_capability_descriptor",
    "tool_authority_request",
    "tool_authority_requests",
    "tool_capability_descriptor",
    "tool_capability_name",
    # Sandbox
    "SandboxRuntime",
    "NoneSandboxRuntime",
    "LocalTestSandboxRuntime",
    "SandboxManifest",
    "SandboxSession",
    "SandboxExecRequest",
    "SandboxExecResult",
    "SandboxSnapshot",
    "SandboxAuthorityContext",
    "SandboxCommand",
    "SandboxCommandSpec",
    "SandboxCommandContext",
    "SandboxExecutionService",
    "SandboxRuntimeConfig",
    "SandboxProvider",
    "NetworkPolicy",
    "SandboxNetworkDefault",
    "SandboxFilesystemDefault",
    "SandboxFilesystemPolicy",
    "SandboxEnvironment",
    "SandboxResources",
    "SandboxLifecycle",
    "SandboxLifecycleCleanup",
    "WorkspaceMount",
    "WorkspaceMountMode",
    "SandboxRuntimeError",
    "SandboxUnsupportedError",
    "SandboxSessionNotFoundError",
    "build_sandbox_runtime",
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
    "PORTABLE_EVIDENCE_CONTRACT": (
        "omnicoreagent.core.telemetry",
        "PORTABLE_EVIDENCE_CONTRACT",
    ),
    "PORTABLE_EVIDENCE_SCHEMA": (
        "omnicoreagent.core.telemetry",
        "PORTABLE_EVIDENCE_SCHEMA",
    ),
    "PORTABLE_EVIDENCE_SCHEMA_VERSION": (
        "omnicoreagent.core.telemetry",
        "PORTABLE_EVIDENCE_SCHEMA_VERSION",
    ),
    "validate_portable_evidence_document": (
        "omnicoreagent.core.telemetry",
        "validate_portable_evidence_document",
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
    "GovernanceEngine": ("omnicoreagent.governance", "GovernanceEngine"),
    "PolicyEvaluator": ("omnicoreagent.governance", "PolicyEvaluator"),
    "PolicyEnvelope": ("omnicoreagent.governance", "PolicyEnvelope"),
    "PolicyRule": ("omnicoreagent.governance", "PolicyRule"),
    "PolicyRuleSet": ("omnicoreagent.governance", "PolicyRuleSet"),
    "PolicyDecision": ("omnicoreagent.governance", "PolicyDecision"),
    "PolicyEffect": ("omnicoreagent.governance", "PolicyEffect"),
    "PolicyMode": ("omnicoreagent.governance", "PolicyMode"),
    "PolicyProfile": ("omnicoreagent.governance", "PolicyProfile"),
    "PolicyConstraints": ("omnicoreagent.governance", "PolicyConstraints"),
    "PolicyRuleConditions": ("omnicoreagent.governance", "PolicyRuleConditions"),
    "PolicyProvenance": ("omnicoreagent.governance", "PolicyProvenance"),
    "PolicySource": ("omnicoreagent.governance", "PolicySource"),
    "PolicyBudget": ("omnicoreagent.governance", "PolicyBudget"),
    "AuthorityRequest": ("omnicoreagent.governance", "AuthorityRequest"),
    "AuthorityTarget": ("omnicoreagent.governance", "AuthorityTarget"),
    "CapabilityDescriptor": ("omnicoreagent.governance", "CapabilityDescriptor"),
    "DescriptorSource": ("omnicoreagent.governance", "DescriptorSource"),
    "DescriptorTrust": ("omnicoreagent.governance", "DescriptorTrust"),
    "TargetMatcher": ("omnicoreagent.governance", "TargetMatcher"),
    "ReasonCode": ("omnicoreagent.governance", "ReasonCode"),
    "ApprovalRequest": ("omnicoreagent.governance", "ApprovalRequest"),
    "ApprovalResult": ("omnicoreagent.governance", "ApprovalResult"),
    "ApprovalResolver": ("omnicoreagent.governance", "ApprovalResolver"),
    "StaticApprovalResolver": ("omnicoreagent.governance", "StaticApprovalResolver"),
    "DenyAllApprovalResolver": ("omnicoreagent.governance", "DenyAllApprovalResolver"),
    "GovernanceError": ("omnicoreagent.governance", "GovernanceError"),
    "ApprovalInvalidError": ("omnicoreagent.governance", "ApprovalInvalidError"),
    "AuditRequiredError": ("omnicoreagent.governance", "AuditRequiredError"),
    "PolicyLoadError": ("omnicoreagent.governance", "PolicyLoadError"),
    "PolicyEvaluationError": ("omnicoreagent.governance", "PolicyEvaluationError"),
    "PolicyDeniedError": ("omnicoreagent.governance", "PolicyDeniedError"),
    "ApprovalRequiredError": ("omnicoreagent.governance", "ApprovalRequiredError"),
    "ApprovalExpiredError": ("omnicoreagent.governance", "ApprovalExpiredError"),
    "SandboxRequiredError": ("omnicoreagent.governance", "SandboxRequiredError"),
    "BudgetExceededError": ("omnicoreagent.governance", "BudgetExceededError"),
    "UnknownCapabilityError": ("omnicoreagent.governance", "UnknownCapabilityError"),
    "UngovernedCapabilityError": (
        "omnicoreagent.governance",
        "UngovernedCapabilityError",
    ),
    "build_default_policy": ("omnicoreagent.governance", "build_default_policy"),
    "attach_policy_hash": ("omnicoreagent.governance", "attach_policy_hash"),
    "canonical_policy_payload": (
        "omnicoreagent.governance",
        "canonical_policy_payload",
    ),
    "discover_policy_file": ("omnicoreagent.governance", "discover_policy_file"),
    "load_policy": ("omnicoreagent.governance", "load_policy"),
    "load_policy_file": ("omnicoreagent.governance", "load_policy_file"),
    "policy_from_mapping": ("omnicoreagent.governance", "policy_from_mapping"),
    "policy_hash": ("omnicoreagent.governance", "policy_hash"),
    "network_authority_request": (
        "omnicoreagent.governance",
        "network_authority_request",
    ),
    "network_capability_descriptor": (
        "omnicoreagent.governance",
        "network_capability_descriptor",
    ),
    "package_install_authority_request": (
        "omnicoreagent.governance",
        "package_install_authority_request",
    ),
    "package_install_capability_descriptor": (
        "omnicoreagent.governance",
        "package_install_capability_descriptor",
    ),
    "filesystem_authority_request": (
        "omnicoreagent.governance",
        "filesystem_authority_request",
    ),
    "filesystem_capability_descriptor": (
        "omnicoreagent.governance",
        "filesystem_capability_descriptor",
    ),
    "secret_authority_request": (
        "omnicoreagent.governance",
        "secret_authority_request",
    ),
    "secret_capability_descriptor": (
        "omnicoreagent.governance",
        "secret_capability_descriptor",
    ),
    "tool_authority_request": ("omnicoreagent.governance", "tool_authority_request"),
    "tool_authority_requests": ("omnicoreagent.governance", "tool_authority_requests"),
    "tool_capability_descriptor": (
        "omnicoreagent.governance",
        "tool_capability_descriptor",
    ),
    "tool_capability_name": ("omnicoreagent.governance", "tool_capability_name"),
    "SandboxRuntime": ("omnicoreagent.sandbox", "SandboxRuntime"),
    "NoneSandboxRuntime": ("omnicoreagent.sandbox", "NoneSandboxRuntime"),
    "LocalTestSandboxRuntime": ("omnicoreagent.sandbox", "LocalTestSandboxRuntime"),
    "SandboxManifest": ("omnicoreagent.sandbox", "SandboxManifest"),
    "SandboxSession": ("omnicoreagent.sandbox", "SandboxSession"),
    "SandboxExecRequest": ("omnicoreagent.sandbox", "SandboxExecRequest"),
    "SandboxExecResult": ("omnicoreagent.sandbox", "SandboxExecResult"),
    "SandboxSnapshot": ("omnicoreagent.sandbox", "SandboxSnapshot"),
    "SandboxAuthorityContext": ("omnicoreagent.sandbox", "SandboxAuthorityContext"),
    "SandboxCommand": ("omnicoreagent.sandbox", "SandboxCommand"),
    "SandboxCommandSpec": ("omnicoreagent.sandbox", "SandboxCommandSpec"),
    "SandboxCommandContext": ("omnicoreagent.sandbox", "SandboxCommandContext"),
    "SandboxExecutionService": ("omnicoreagent.sandbox", "SandboxExecutionService"),
    "SandboxRuntimeConfig": ("omnicoreagent.sandbox", "SandboxRuntimeConfig"),
    "SandboxProvider": ("omnicoreagent.sandbox", "SandboxProvider"),
    "NetworkPolicy": ("omnicoreagent.sandbox", "NetworkPolicy"),
    "SandboxNetworkDefault": ("omnicoreagent.sandbox", "SandboxNetworkDefault"),
    "SandboxFilesystemDefault": ("omnicoreagent.sandbox", "SandboxFilesystemDefault"),
    "SandboxFilesystemPolicy": ("omnicoreagent.sandbox", "SandboxFilesystemPolicy"),
    "SandboxEnvironment": ("omnicoreagent.sandbox", "SandboxEnvironment"),
    "SandboxResources": ("omnicoreagent.sandbox", "SandboxResources"),
    "SandboxLifecycle": ("omnicoreagent.sandbox", "SandboxLifecycle"),
    "SandboxLifecycleCleanup": ("omnicoreagent.sandbox", "SandboxLifecycleCleanup"),
    "WorkspaceMount": ("omnicoreagent.sandbox", "WorkspaceMount"),
    "WorkspaceMountMode": ("omnicoreagent.sandbox", "WorkspaceMountMode"),
    "SandboxRuntimeError": ("omnicoreagent.sandbox", "SandboxRuntimeError"),
    "SandboxUnsupportedError": ("omnicoreagent.sandbox", "SandboxUnsupportedError"),
    "SandboxSessionNotFoundError": (
        "omnicoreagent.sandbox",
        "SandboxSessionNotFoundError",
    ),
    "build_sandbox_runtime": ("omnicoreagent.sandbox", "build_sandbox_runtime"),
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

    if name in _REMOVED:
        message = (
            f"{name} was removed in OmniCoreAgent 0.4: {_REMOVED[name]}. "
            "See https://docs-omnicoreagent.omnirexfloralabs.com/docs/upgrading"
        )
        # `from omnicoreagent import X` shows this message only if it is an
        # ImportError (an AttributeError becomes a bare "cannot import
        # name"); hasattr and getattr with a default need an AttributeError,
        # or code that checks for the name crashes.
        if _called_from_an_import(sys._getframe(1)):
            raise ImportError(message)
        raise AttributeError(message)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _called_from_an_import(frame: Any) -> bool:
    """Whether the lookup is a ``from ... import`` statement (checked on
    CPython 3.12, 3.13 and 3.14)."""
    try:
        import dis

        return dis.opname[frame.f_code.co_code[frame.f_lasti]] == "IMPORT_FROM"
    except Exception:
        return False


# Names 0.3 exported that 0.4 does not, and what replaced them.
_REMOVED = {
    "SequentialAgent": "run agents one after another with plain awaits, or give a lead agent sub_agents=[...]",
    "ParallelAgent": "run agents at once with asyncio.gather(a.run(...), b.run(...))",
    "RouterAgent": "give a lead agent sub_agents=[...]; each becomes a delegate_<name> tool it chooses",
    "DeepAgent": "set agent_config enable_subagents=True (spawn_subagents) with workspace files",
    "OmniAgent": "use OmniCoreAgent",
    "EventRouter": "runs are recorded as telemetry; read them with get_trajectory, stream with on_event or agent.stream",
    "BackgroundOmniCoreAgent": "use BackgroundAgentManager: register_agent, then register_task",
    "BackgroundTaskScheduler": "use BackgroundAgentManager.start()",
    "APSchedulerBackend": "use BackgroundAgentManager with a task store",
    "TaskRegistry": "use BackgroundAgentManager's task store (memory, sql, redis, mongodb)",
}
