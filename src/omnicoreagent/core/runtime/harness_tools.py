from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime
from omnicoreagent.core.tools.mcp_results import mcp_tool_definition


def prepare_dynamic_subagents(
    *,
    enabled: bool,
    existing_factory: Any,
    base_model_config: dict[str, Any],
    mcp_tools: list[dict[str, Any]],
    local_tools: Any,
    agent_config: dict[str, Any],
    prompt_builder: Any,
    memory_router: Any,
    governance_engine: Any = None,
    debug: bool,
    telemetry_recorder: Any = None,
) -> tuple[Any, Any]:
    if not enabled:
        return existing_factory, local_tools
    if existing_factory is not None:
        return existing_factory, local_tools

    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    if local_tools is None:
        local_tools = ToolRegistry()

    factory = runtime("SubagentFactory")(
        base_model_config=base_model_config,
        mcp_tools=mcp_tools,
        local_tools=local_tools,
        agent_config=agent_config,
        prompt_builder=prompt_builder,
        memory_router=memory_router,
        governance_engine=governance_engine,
        telemetry_recorder=telemetry_recorder,
        debug=debug,
    )
    runtime("build_subagent_tools")(factory, local_tools)
    return factory, local_tools


def available_tools(mcp_client: Any, local_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []

    if mcp_client:
        for server_tools in mcp_client.available_tools.values():
            tools.extend(_available_mcp_tool(tool) for tool in server_tools)

    if local_tools:
        tools.extend(local_tools.get_available_tools())

    return tools


def _available_mcp_tool(tool: Any) -> dict[str, Any]:
    return {**mcp_tool_definition(tool), "type": "mcp"}
