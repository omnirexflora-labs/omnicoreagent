from __future__ import annotations

from importlib import import_module
from typing import Any


_RUNTIME_EXPORTS = {
    "AdvanceToolsUse": (
        "omnicoreagent.core.tools.advance_tools.advanced_tools_use",
        "AdvanceToolsUse",
    ),
    "AgentConfig": ("omnicoreagent.core.runtime.config", "AgentConfig"),
    "DetectionConfig": ("omnicoreagent.core.guardrails", "DetectionConfig"),
    "EventRouter": ("omnicoreagent.core.events.event_router", "EventRouter"),
    "FAST_CONVERSATION_SUMMARY_PROMPT": (
        "omnicoreagent.core.system_prompts",
        "FAST_CONVERSATION_SUMMARY_PROMPT",
    ),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "MemoryRouter": ("omnicoreagent.core.memory_store.memory_router", "MemoryRouter"),
    "OmniCoreAgentPromptBuilder": (
        "omnicoreagent.core.system_prompts",
        "OmniCoreAgentPromptBuilder",
    ),
    "PromptInjectionGuard": ("omnicoreagent.core.guardrails", "PromptInjectionGuard"),
    "REACT_AGENT_PROMPT": ("omnicoreagent.core.system_prompts", "REACT_AGENT_PROMPT"),
    "ReactAgent": ("omnicoreagent.core.agents.react_agent", "ReactAgent"),
    "SubagentFactory": ("omnicoreagent.core.subagents", "SubagentFactory"),
    "Usage": ("omnicoreagent.core.token_usage", "Usage"),
    "build_subagent_tools": ("omnicoreagent.core.subagents", "build_subagent_tools"),
    "logger": ("omnicoreagent.core.utils", "logger"),
    "normalize_agent_config": (
        "omnicoreagent.core.runtime.config",
        "normalize_agent_config",
    ),
    "normalize_mcp_tools": (
        "omnicoreagent.core.runtime.config",
        "normalize_mcp_tools",
    ),
    "normalize_model_config": (
        "omnicoreagent.core.runtime.config",
        "normalize_model_config",
    ),
}


def __getattr__(name: str) -> Any:
    return runtime(name)


def runtime(name: str) -> Any:
    if name in globals():
        return globals()[name]
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _RUNTIME_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def runtime_logger() -> Any:
    return runtime("logger")


class LazyDefaultPromptBuilder:
    def __init__(self):
        self._builder = None

    def _load(self):
        if self._builder is None:
            self._builder = runtime("OmniCoreAgentPromptBuilder")(
                runtime("REACT_AGENT_PROMPT")
            )
        return self._builder

    def build(self, *, system_instruction: str) -> str:
        return self._load().build(system_instruction=system_instruction)
