"""
Core AI agent harness components.

Exports are resolved lazily to keep core package import cheap and free of
provider/runtime side effects.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "ReactAgent",
    "MemoryRouter",
    "LLMConnection",
    "EventRouter",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "AgentConfig",
    "ParsedResponse",
    "ToolCall",
    "UsageLimits",
    "Usage",
    "UsageLimitExceeded",
]

_EXPORTS = {
    "ReactAgent": ("omnicoreagent.core.agents", "ReactAgent"),
    "MemoryRouter": ("omnicoreagent.core.memory_store", "MemoryRouter"),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "EventRouter": ("omnicoreagent.core.events", "EventRouter"),
    "ToolRegistry": ("omnicoreagent.core.tools", "ToolRegistry"),
    "Tool": ("omnicoreagent.core.tools", "Tool"),
    "AgentConfig": ("omnicoreagent.core.runtime.config", "AgentConfig"),
    "ParsedResponse": ("omnicoreagent.core.types", "ParsedResponse"),
    "ToolCall": ("omnicoreagent.core.types", "ToolCall"),
    "UsageLimits": ("omnicoreagent.core.token_usage", "UsageLimits"),
    "Usage": ("omnicoreagent.core.token_usage", "Usage"),
    "UsageLimitExceeded": ("omnicoreagent.core.token_usage", "UsageLimitExceeded"),
}

_OPTIONAL_EXPORTS = {
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store", "postgres"),
}


def __getattr__(name: str) -> Any:
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

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
