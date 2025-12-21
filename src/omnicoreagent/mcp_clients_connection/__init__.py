"""
MCP (Model Context Protocol) Client Package

This package provides MCP client functionality including:
- MCP Client implementation
- CLI interface
- Resource management
- Tool discovery and management
- Server capabilities refresh
- Notifications and sampling
"""

from .client import MCPClient, Configuration
from .resources import (
    list_resources,
    read_resource,
    subscribe_resource,
    unsubscribe_resource,
)
from .tools import list_tools
from .prompts import get_prompt, get_prompt_with_react_agent, list_prompts

__all__ = [
    "MCPClient",
    "Configuration",
    "list_resources",
    "read_resource",
    "subscribe_resource",
    "unsubscribe_resource",
    "list_tools",
    "get_prompt",
    "get_prompt_with_react_agent",
    "list_prompts",
]
