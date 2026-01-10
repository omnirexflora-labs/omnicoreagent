"""
OmniCoreAgent AI Framework

A comprehensive AI agent framework with MCP client capabilities.
"""

from .core.agents import ReactAgent
from .core.memory_store import MemoryRouter
from .core.llm import LLMConnection
from .core.events import EventRouter
from .core.memory_store import DatabaseMessageStore
from .core.tools import ToolRegistry, Tool
from .core.utils import logger

from .omni_agent.agent import OmniCoreAgent, OmniAgent
from .omni_agent.background_agent import (
    BackgroundOmniCoreAgent,
    BackgroundAgentManager,
    TaskRegistry,
    APSchedulerBackend,
    BackgroundTaskScheduler,
)

from .mcp_clients_connection import MCPClient, Configuration

from .omni_agent.workflow.parallel_agent import ParallelAgent
from .omni_agent.workflow.sequential_agent import SequentialAgent
from .omni_agent.workflow.router_agent import RouterAgent

__all__ = [
    "ReactAgent",
    "MemoryRouter",
    "LLMConnection",
    "EventRouter",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "logger",
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
    "MCPClient",
    "Configuration",
]
