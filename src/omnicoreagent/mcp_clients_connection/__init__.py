"""
MCP (Model Context Protocol) Client Package

This package provides MCP client functionality including:
- MCP Client implementation
- CLI interface
- Tool discovery and management
- Server capabilities refresh
"""

from .client import MCPClient
from .tools import list_tools

__all__ = [
    "MCPClient",
    "list_tools",
]
