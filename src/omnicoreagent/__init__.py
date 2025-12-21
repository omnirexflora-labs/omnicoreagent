"""
OmniCoreAgent AI Framework

A comprehensive AI agent framework with MCP client capabilities.
"""

from .core.agents import ReactAgent
from .core.memory_store import MemoryRouter
from .core.llm import LLMConnection
from .core.events import EventRouter
from .core.database import DatabaseMessageStore
from .core.tools import ToolRegistry, Tool
from .core.utils import logger

# High-level interface
from .omni_agent.agent import OmniAgent
from .omni_agent.background_agent import (
    BackgroundOmniAgent,
    BackgroundAgentManager,
    TaskRegistry,
    APSchedulerBackend,
    BackgroundTaskScheduler,
)

# MCP Client (for advanced users)
from .mcp_clients_connection import MCPClient, Configuration

# workflow agent interface
from .omni_agent.workflow.parallel_agent import ParallelAgent
from .omni_agent.workflow.sequential_agent import SequentialAgent
from .omni_agent.workflow.router_agent import RouterAgent

__all__ = [
    # Core Agents
    "ReactAgent",
    # Core Components
    "MemoryRouter",
    "LLMConnection",
    "EventRouter",
    "DatabaseMessageStore",
    "ToolRegistry",
    "Tool",
    "logger",
    # High-level Interface
    "OmniAgent",
    "BackgroundOmniAgent",
    "BackgroundAgentManager",
    "TaskRegistry",
    "APSchedulerBackend",
    "BackgroundTaskScheduler",
    "ParallelAgent",
    "SequentialAgent",
    "RouterAgent",
    # MCP Client
    "MCPClient",
    "Configuration",
]
