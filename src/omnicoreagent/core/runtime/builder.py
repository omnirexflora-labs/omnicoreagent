from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omnicoreagent.core.runtime import construction, harness_tools


@dataclass(slots=True)
class AgentRuntimeComponents:
    """Initialized runtime objects owned by the OmniCoreAgent facade."""

    agent: Any
    mcp_client: Any
    llm_connection: Any
    local_tools: Any
    subagent_factory: Any


def build_agent_runtime(
    *,
    model_config: dict[str, Any],
    mcp_tools: list[dict[str, Any]],
    local_tools: Any,
    agent_config: dict[str, Any],
    memory_router: Any,
    prompt_builder: Any,
    existing_subagent_factory: Any,
    guardrail: Any,
    guardrail_mode: str,
    summarize_fn: Any,
    debug: bool,
    telemetry_recorder: Any = None,
) -> AgentRuntimeComponents:
    """Build the executable agent runtime without mutating the facade."""
    governance_engine = construction.build_governance_engine(
        agent_config=agent_config,
        telemetry_recorder=telemetry_recorder,
    )
    mcp_client, llm_connection = construction.create_llm_runtime(
        mcp_tools=mcp_tools,
        model_config=model_config,
        debug=debug,
        governance_engine=governance_engine,
    )

    agent_settings = construction.build_agent_settings(agent_config)
    construction.configure_memory_router(
        memory_router=memory_router,
        agent_settings=agent_settings,
        summarize_fn=summarize_fn,
    )

    agent = construction.create_react_agent(
        agent_settings=agent_settings,
        guardrail=guardrail,
        guardrail_mode=guardrail_mode,
        governance_engine=governance_engine,
    )

    subagent_factory, local_tools = harness_tools.prepare_dynamic_subagents(
        enabled=agent_config.get("enable_subagents", False),
        existing_factory=existing_subagent_factory,
        base_model_config=model_config,
        mcp_tools=mcp_tools,
        local_tools=local_tools,
        agent_config=agent_config,
        prompt_builder=prompt_builder,
        memory_router=memory_router,
        governance_engine=governance_engine,
        debug=debug,
    )

    if local_tools:
        harness_tools.index_tools_for_advanced_use(
            enabled=agent.enable_advanced_tool_use,
            local_tools=local_tools,
        )

    return AgentRuntimeComponents(
        agent=agent,
        mcp_client=mcp_client,
        llm_connection=llm_connection,
        local_tools=local_tools,
        subagent_factory=subagent_factory,
    )
