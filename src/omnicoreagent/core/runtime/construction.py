from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime, runtime_logger


def default_memory_router() -> Any:
    return runtime("MemoryRouter")(memory_store_type="in_memory")


def default_event_router() -> Any:
    return runtime("EventRouter")(event_store_type="in_memory")


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
) -> Any:
    tool_guardrail = guardrail if guardrail_mode == "full" else None
    return runtime("ReactAgent")(config=agent_settings, guardrail=tool_guardrail)
