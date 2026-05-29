from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime, runtime_logger


def default_memory_router() -> Any:
    return runtime("MemoryRouter")(memory_store_type="in_memory")


def default_telemetry_store() -> Any:
    from omnicoreagent.core.telemetry import InMemoryTelemetryStore

    return InMemoryTelemetryStore()


def build_guardrail(agent_name: str, agent_config: dict[str, Any]) -> tuple[str, Any]:
    guardrail_mode = agent_config.get("guardrail_mode", "full")
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
    return GovernanceEngine(
        policy,
        approval_resolver=governance_config.get("approval_resolver"),
        telemetry_recorder=telemetry_recorder,
        sandbox_runtime=governance_config.get("sandbox_runtime"),
        allow_test_sandbox_runtime=governance_config.get(
            "allow_test_sandbox_runtime", False
        ),
        allow_static_high_risk_approvals=governance_config.get(
            "allow_static_high_risk_approvals", False
        ),
    )
