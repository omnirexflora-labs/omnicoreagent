"""
Workspace memory tools for OmniCoreAgent.

Provides file-style workspace memory for notes, scratchpads, logs, todos,
task progress, and generated files. Files live inside the active workspace
storage under the memories namespace.

Usage:
    # In agent config
    agent_config = {
        "enable_workspace_memory": True,
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
