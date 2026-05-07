"""
OmniCoreAgent AI Framework.

Package exports are resolved lazily so importing ``omnicoreagent`` stays light.
Provider clients, dotenv loading in user code, and optional integrations should
only be touched when the corresponding runtime object is requested.
"""

from importlib import import_module
from typing import Any

__all__ = [
    # Core
    "ReactAgent",
    "MemoryRouter",
    "LLMConnection",
    "EventRouter",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "logger",
    # Agents
    "OmniCoreAgent",
    "BackgroundOmniCoreAgent",
    "BackgroundAgentManager",
    "TaskRegistry",
    "APSchedulerBackend",
    "BackgroundTaskScheduler",
    "ParallelAgent",
    "SequentialAgent",
    "RouterAgent",
    # MCP
    "MCPClient",
    # OmniServe
    "OmniServe",
    "OmniServeConfig",
    # Resilience utilities
    "RetryConfig",
    "RetryStrategy",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "with_retry",
    "retry_async",
    "get_metrics",
]

_EXPORTS = {
    "ReactAgent": ("omnicoreagent.core.agents", "ReactAgent"),
    "MemoryRouter": ("omnicoreagent.core.memory_store", "MemoryRouter"),
    "LLMConnection": ("omnicoreagent.core.llm", "LLMConnection"),
    "EventRouter": ("omnicoreagent.core.events", "EventRouter"),
    "ToolRegistry": ("omnicoreagent.core.tools", "ToolRegistry"),
    "Tool": ("omnicoreagent.core.tools", "Tool"),
    "logger": ("omnicoreagent.core.logging", "logger"),
    "OmniCoreAgent": (
        "omnicoreagent.core.runtime.omnicore_agent",
        "OmniCoreAgent",
    ),
    "MCPClient": ("omnicoreagent.mcp_clients_connection", "MCPClient"),
    "ParallelAgent": ("omnicoreagent.workflows.parallel_agent", "ParallelAgent"),
    "SequentialAgent": ("omnicoreagent.workflows.sequential_agent", "SequentialAgent"),
    "RouterAgent": ("omnicoreagent.workflows.router_agent", "RouterAgent"),
}

_OPTIONAL_EXPORTS = {
    "DatabaseMessageStore": ("omnicoreagent.core.memory_store", "postgres"),
    "BackgroundOmniCoreAgent": (
        "omnicoreagent.background",
        "background",
    ),
    "BackgroundAgentManager": (
        "omnicoreagent.background",
        "background",
    ),
    "TaskRegistry": ("omnicoreagent.background", "background"),
    "APSchedulerBackend": (
        "omnicoreagent.background",
        "background",
    ),
    "BackgroundTaskScheduler": (
        "omnicoreagent.background",
        "background",
    ),
    "OmniServe": ("omnicoreagent.serve", "serve"),
    "OmniServeConfig": ("omnicoreagent.serve", "serve"),
    "RetryConfig": ("omnicoreagent.serve.resilience", "serve"),
    "RetryStrategy": ("omnicoreagent.serve.resilience", "serve"),
    "CircuitBreaker": ("omnicoreagent.serve.resilience", "serve"),
    "CircuitBreakerConfig": (
        "omnicoreagent.serve.resilience",
        "serve",
    ),
    "with_retry": ("omnicoreagent.serve.resilience", "serve"),
    "retry_async": ("omnicoreagent.serve.resilience", "serve"),
    "get_metrics": ("omnicoreagent.serve.observability", "serve"),
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
