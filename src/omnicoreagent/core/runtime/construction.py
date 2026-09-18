from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicoreagent.core.runtime.imports import runtime, runtime_logger
from omnicoreagent.core.runtime.config import normalize_guardrail_mode


def default_memory_router() -> Any:
    return runtime("MemoryRouter")(memory_store_type="in_memory")


def default_telemetry_store(
    *,
    telemetry_config: Any = None,
    workspace_config: Any = None,
) -> Any:
    """Build the built-in telemetry store selected by the effective policy.

    Injected stores are handled by the caller. ``auto`` and ``jsonl`` write
    durable local JSONL (``storage_path``, or ``telemetry/traces.jsonl`` in the
    local workspace directory); ``memory`` is an explicit opt-out. Every
    component that resolves the same file shares one store object.
    """
    from omnicoreagent.core.telemetry import InMemoryTelemetryStore, TelemetryConfig
    from omnicoreagent.core.telemetry.store import shared_jsonl_telemetry_store

    config = TelemetryConfig.from_value(telemetry_config) or TelemetryConfig()
    if config.storage == "memory":
        return InMemoryTelemetryStore(max_traces=config.memory_max_traces)
    return shared_jsonl_telemetry_store(
        _telemetry_jsonl_path(config, workspace_config),
        retention_days=config.retention_days,
    )


def default_telemetry_payload_store(
    *,
    telemetry_config: Any = None,
    workspace_config: Any = None,
) -> Any:
    """Build the built-in store for oversized redacted telemetry payloads."""
    from omnicoreagent.core.telemetry import TelemetryConfig
    from omnicoreagent.core.telemetry.payloads import (
        LocalTelemetryPayloadStore,
        WorkspaceTelemetryPayloadStore,
    )
    from omnicoreagent.core.workspace.config import resolve_workspace_config

    config = TelemetryConfig.from_value(telemetry_config) or TelemetryConfig()
    if not config.offload_large_payloads:
        return None

    from omnicoreagent.core.workspace.storage import create_workspace_storage

    resolved_workspace = resolve_workspace_config(workspace_config)
    if config.offload_target == "object_storage":
        if resolved_workspace.workspace_backend not in {"s3", "r2"}:
            if config.strict:
                raise ValueError(
                    "telemetry offload_target='object_storage' requires an S3 or R2 workspace"
                )
            return None
        storage = create_workspace_storage(
            namespace="telemetry/payloads",
            config=resolved_workspace,
        )
        return WorkspaceTelemetryPayloadStore(
            storage,
            retention_days=config.payload_retention_days,
        )

    if config.storage_path is not None:
        return LocalTelemetryPayloadStore(
            f"{Path(config.storage_path).expanduser()}.payloads",
            retention_days=config.payload_retention_days,
        )

    storage = create_workspace_storage(
        namespace="telemetry/payloads",
        config=resolved_workspace,
    )
    return WorkspaceTelemetryPayloadStore(
        storage,
        retention_days=config.payload_retention_days,
    )


def _telemetry_jsonl_path(config: Any, workspace_config: Any = None) -> Path:
    if config.storage_path is not None:
        return Path(config.storage_path).expanduser()

    from omnicoreagent.core.workspace.config import (
        WorkspaceConfig,
        resolve_workspace_config,
    )

    resolved_workspace = resolve_workspace_config(workspace_config)
    if resolved_workspace.workspace_backend != "local":
        # A cloud workspace never makes telemetry a cloud dependency: traces
        # stay in the local workspace directory unless a path is configured.
        resolved_workspace = WorkspaceConfig(
            workspace_dir=WorkspaceConfig.from_env().workspace_dir
        )
    return resolved_workspace.local_namespace_path("telemetry") / "traces.jsonl"


def build_guardrail(agent_name: str, agent_config: dict[str, Any]) -> tuple[str, Any]:
    guardrail_mode = normalize_guardrail_mode(agent_config.get("guardrail_mode", "full"))
    if guardrail_mode == "off":
        runtime_logger().info(f"Guardrail disabled for agent '{agent_name}'")
        return guardrail_mode, None

    guardrail_config = agent_config.get("guardrail_config", {})
    detection_config = runtime("DetectionConfig")(**guardrail_config)
    guardrail = runtime("PromptInjectionGuard")(detection_config)
    runtime_logger().info(
        f"Guardrail enabled for agent '{agent_name}' (mode: {guardrail_mode})"
    )
    return guardrail_mode, guardrail


def create_llm_runtime(
    *,
    mcp_tools: list[dict[str, Any]],
    model_config: dict[str, Any],
    debug: bool,
    governance_engine: Any = None,
) -> tuple[Any, Any]:
    if not mcp_tools:
        llm_connection = runtime("LLMConnection")(
            model_config=model_config,
            api_key=model_config.get("api_key"),
        )
        return None, llm_connection

    from omnicoreagent.mcp_clients_connection.client import MCPClient

    mcp_client = MCPClient(
        servers=mcp_tools,
        model_config=model_config,
        api_key=model_config.get("api_key"),
        governance_engine=governance_engine,
        debug=debug,
    )
    return mcp_client, mcp_client.llm_connection


def build_agent_settings(agent_config: dict[str, Any]) -> Any:
    return runtime("AgentConfig")(**agent_config)


def configure_memory_router(
    *,
    memory_router: Any,
    agent_settings: Any,
    summarize_fn: Any,
):
    if not memory_router:
        return

    summary_config = agent_settings.memory_config.get("summary")
    memory_router.set_memory_config(
        mode=agent_settings.memory_config["mode"],
        value=agent_settings.memory_config["value"],
        summary_config=summary_config,
        summarize_fn=summarize_fn
        if summary_config and summary_config.get("enabled")
        else None,
    )


def create_react_agent(
    *,
    agent_settings: Any,
    guardrail: Any,
    guardrail_mode: str,
    governance_engine: Any = None,
) -> Any:
    tool_guardrail = guardrail if guardrail_mode == "full" else None
    return runtime("ReactAgent")(
        config=agent_settings,
        guardrail=tool_guardrail,
        governance_engine=governance_engine,
    )


def build_governance_engine(agent_config: dict[str, Any], telemetry_recorder: Any = None) -> Any:
    governance_config = agent_config.get("governance_config") or {}
    if not governance_config.get("enabled", False):
        return None

    from omnicoreagent.governance import GovernanceEngine, load_policy

    policy = load_policy(
        policy=governance_config.get("policy"),
        explicit_path=governance_config.get("policy_path"),
        project_root=governance_config.get("project_root"),
        profile=governance_config.get("profile", "interactive-dev"),
    )
    sandbox_runtime = governance_config.get("sandbox_runtime")
    if sandbox_runtime is None and governance_config.get("sandbox_config") is not None:
        from omnicoreagent.sandbox import build_sandbox_runtime

        sandbox_runtime = build_sandbox_runtime(
            governance_config.get("sandbox_config"),
            telemetry_recorder=telemetry_recorder,
        )
    return GovernanceEngine(
        policy,
        approval_resolver=governance_config.get("approval_resolver"),
        telemetry_recorder=telemetry_recorder,
        sandbox_runtime=sandbox_runtime,
        allow_test_sandbox_runtime=governance_config.get(
            "allow_test_sandbox_runtime", False
        ),
        allow_static_high_risk_approvals=governance_config.get(
            "allow_static_high_risk_approvals", False
        ),
    )
