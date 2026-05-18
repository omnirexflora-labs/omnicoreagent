"""Agent spec helpers for background execution."""

from __future__ import annotations

from typing import Any

from omnicoreagent.background.models import BackgroundAgentSpec
from omnicoreagent.background.store.base import AbstractTaskStore


def spec_from_agent(agent_id: str, agent: Any) -> BackgroundAgentSpec:
    """Create a durable background agent spec from a runtime agent object."""
    return BackgroundAgentSpec(
        agent_id=agent_id,
        name=getattr(agent, "name", agent_id),
        system_instruction=getattr(agent, "system_instruction", None),
        model_config=_safe_model_config(agent),
        agent_config=_safe_dict(getattr(agent, "agent_config", {})),
        mcp_tools=_safe_list(getattr(agent, "mcp_tools", [])),
        workspace_config=_safe_workspace_config(agent),
    )


async def resolve_agent(
    *,
    agent_id: str,
    agents: dict[str, Any],
    task_store: AbstractTaskStore,
    memory_router: Any,
) -> Any | None:
    """Resolve a registered runtime agent, reconstructing from spec when possible."""
    agent = agents.get(agent_id)
    if agent is not None:
        return agent

    spec = await task_store.get_agent(agent_id)
    if not spec or not spec.llm_model_config:
        return None

    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    agent = OmniCoreAgent(
        name=spec.name or spec.agent_id,
        system_instruction=spec.system_instruction or "",
        model_config=spec.llm_model_config,
        mcp_tools=spec.mcp_tools,
        agent_config={
            **spec.agent_config,
            **({"workspace_config": spec.workspace_config} if spec.workspace_config else {}),
        },
        memory_router=memory_router,
    )
    agents[agent_id] = agent
    return agent


def _safe_model_config(agent: Any) -> dict[str, Any] | None:
    value = getattr(agent, "model_config", None)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value if isinstance(value, dict) else None


def _safe_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        safe_values = []
        for item in value:
            if hasattr(item, "model_dump"):
                safe_values.append(item.model_dump())
            elif isinstance(item, dict):
                safe_values.append(item)
        return safe_values
    return []


def _safe_workspace_config(agent: Any) -> dict[str, Any] | None:
    config = getattr(agent, "agent_config", None)
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    if isinstance(config, dict):
        workspace_config = config.get("workspace_config")
        if hasattr(workspace_config, "model_dump"):
            return workspace_config.model_dump()
        if isinstance(workspace_config, dict):
            return workspace_config
    return None
