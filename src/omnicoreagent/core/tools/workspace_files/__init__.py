"""Workspace file tools for OmniCoreAgent.

Provides file-style workspace storage for notes, scratchpads, logs, todos,
task progress, and generated outputs. Files live inside the active workspace
storage under the files namespace.

Usage:
    # In agent config
    agent_config = {
        "enable_workspace_files": True,
    }
"""

from omnicoreagent.core.tools.workspace_files.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.tools.workspace_files.storage import WorkspaceFilesBackend
from omnicoreagent.core.tools.workspace_files.factory import create_workspace_files_backend
from omnicoreagent.core.tools.workspace_files.tool import (
    WorkspaceFilesTool,
    build_tool_registry_workspace_files,
)

__all__ = [
    # Base class
    "AbstractWorkspaceFilesBackend",
    # Backends
    "WorkspaceFilesBackend",
    # Factory
    "create_workspace_files_backend",
    # Workspace files tool
    "WorkspaceFilesTool",
    "build_tool_registry_workspace_files",
]
