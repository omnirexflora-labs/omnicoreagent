"""
OmniCoreAgent AI Framework

A comprehensive AI agent framework with MCP client capabilities.
"""

from .core.agents import ReactAgent
from .core.memory_store import MemoryRouter
from .core.llm import LLMConnection
from .core.events import EventRouter
from .core.tools import ToolRegistry, Tool
from .core.utils import logger

from .agent import OmniCoreAgent

from .mcp_clients_connection import MCPClient

from .workflows.parallel_agent import ParallelAgent
from .workflows.sequential_agent import SequentialAgent
from .workflows.router_agent import RouterAgent

from .deep_agent import DeepAgent

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
    "DeepAgent",
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
