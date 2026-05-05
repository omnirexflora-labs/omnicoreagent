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

from .omni_agent.agent import OmniCoreAgent, OmniAgent

from .mcp_clients_connection import MCPClient

from .omni_agent.workflow.parallel_agent import ParallelAgent
from .omni_agent.workflow.sequential_agent import SequentialAgent
from .omni_agent.workflow.router_agent import RouterAgent

from .omni_agent.deep_agent import DeepAgent

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
    "OmniAgent",
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
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "BackgroundAgentManager": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "TaskRegistry": ("omnicoreagent.omni_agent.background_agent", "background"),
    "APSchedulerBackend": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "BackgroundTaskScheduler": (
        "omnicoreagent.omni_agent.background_agent",
        "background",
    ),
    "OmniServe": ("omnicoreagent.omni_agent.omni_serve", "serve"),
    "OmniServeConfig": ("omnicoreagent.omni_agent.omni_serve", "serve"),
    "RetryConfig": ("omnicoreagent.omni_agent.omni_serve.resilience", "serve"),
    "RetryStrategy": ("omnicoreagent.omni_agent.omni_serve.resilience", "serve"),
    "CircuitBreaker": ("omnicoreagent.omni_agent.omni_serve.resilience", "serve"),
    "CircuitBreakerConfig": (
        "omnicoreagent.omni_agent.omni_serve.resilience",
        "serve",
    ),
    "with_retry": ("omnicoreagent.omni_agent.omni_serve.resilience", "serve"),
    "retry_async": ("omnicoreagent.omni_agent.omni_serve.resilience", "serve"),
    "get_metrics": ("omnicoreagent.omni_agent.omni_serve.observability", "serve"),
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
