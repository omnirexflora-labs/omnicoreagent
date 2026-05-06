from __future__ import annotations

from omnicoreagent.core.runtime.construction import (
    build_agent_settings,
    build_guardrail,
    configure_memory_router,
    create_llm_runtime,
    create_react_agent,
    default_event_router,
    default_memory_router,
)
from omnicoreagent.core.runtime.harness_tools import (
    available_tools,
    index_tools_for_advanced_use,
    prepare_dynamic_subagents,
)
from omnicoreagent.core.runtime.imports import (
    LazyDefaultPromptBuilder,
    runtime,
    runtime_logger,
)
from omnicoreagent.core.runtime.normalization import (
    build_agent_config,
    build_mcp_tools,
    build_model_config,
    normalize_local_tools,
)
from omnicoreagent.core.runtime.summaries import (
    extract_summary_text,
    render_history,
    summary_instruction,
)

__all__ = [
    "LazyDefaultPromptBuilder",
    "available_tools",
    "build_agent_config",
    "build_agent_settings",
    "build_guardrail",
    "build_mcp_tools",
    "build_model_config",
    "configure_memory_router",
    "create_llm_runtime",
    "create_react_agent",
    "default_event_router",
    "default_memory_router",
    "extract_summary_text",
    "index_tools_for_advanced_use",
    "normalize_local_tools",
    "prepare_dynamic_subagents",
    "render_history",
    "runtime",
    "runtime_logger",
    "summary_instruction",
]


def __getattr__(name: str):
    return runtime(name)
