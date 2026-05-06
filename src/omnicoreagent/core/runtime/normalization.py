from __future__ import annotations

from typing import Any

from omnicoreagent.core.runtime.imports import runtime


def build_model_config(model_config: Any) -> dict[str, Any]:
    return runtime("normalize_model_config")(model_config)


def build_mcp_tools(mcp_tools: list[Any] | None) -> list[dict[str, Any]]:
    return runtime("normalize_mcp_tools")(mcp_tools)


def build_agent_config(name: str, agent_config: Any = None) -> dict[str, Any]:
    return runtime("normalize_agent_config")(name, agent_config)


def normalize_local_tools(local_tools: Any) -> Any:
    if not isinstance(local_tools, list):
        return local_tools

    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    registry = ToolRegistry()
    for tool in local_tools:
        registry.register(tool)
    return registry
