"""
Core AI Agent Framework Components

This package contains the core AI agent functionality including:
- Agents (React, Sequential)
- Memory Management (In-Memory, Redis, Database, MongoDB)
- LLM Connections and Support
- Event System
- Database Layer
- Tools Management
- Utilities and Constants
"""

from .agents import ReactAgent
from .memory_store import MemoryRouter
from .tools import ToolRegistry, Tool
from .types import ParsedResponse, ToolCall
from .token_usage import UsageLimits, Usage, UsageLimitExceeded
from omnicoreagent.runtime_config import AgentConfig

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

_OPTIONAL_EXPORTS = {
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store", "postgres"),
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from omnicoreagent._optional import load_optional

    module_name, extra = _OPTIONAL_EXPORTS[name]
    return load_optional(
        name,
        extra,
        lambda: getattr(__import__(module_name, fromlist=[name]), name),
    )
