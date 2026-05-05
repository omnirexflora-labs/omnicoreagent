"""
Memory Tool backends for OmniCoreAgent.

Provides pluggable storage backends for the memory tool:
- WorkspaceMemoryBackend: memory files inside the active workspace storage

Usage:
    # In agent config
    agent_config = {
        "memory_tool_backend": "workspace",
    }
"""

from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
from omnicoreagent.core.tools.memory_tool.storage import WorkspaceMemoryBackend
from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend
from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool, build_tool_registry_memory_tool

__all__ = [
    # Base class
    "AbstractMemoryBackend",
    # Backends
    "WorkspaceMemoryBackend",
    # Factory
    "create_memory_backend",
    # Memory tool
    "MemoryTool",
    "build_tool_registry_memory_tool",
]
