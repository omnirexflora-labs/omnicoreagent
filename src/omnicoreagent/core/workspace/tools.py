"""Workspace file tools for scratchpads, logs, task output, and notes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.factory import create_workspace_files_backend

if TYPE_CHECKING:
    from omnicoreagent.core.workspace.manager import Workspace


class WorkspaceFilesTool:
    """High-level file operations rooted inside the active workspace."""

    def __init__(
        self,
        backend: AbstractWorkspaceFilesBackend | None = None,
        workspace: Workspace | None = None,
        workspace_config: WorkspaceConfig | dict | None = None,
    ):
        """
        Initialize WorkspaceFilesTool with a backend.
        
        Args:
            backend: Optional AbstractWorkspaceFilesBackend instance for direct injection.
                None uses the active workspace backend.
            workspace_config: Explicit workspace storage config used when backend is
                not provided. None falls back to environment configuration.
        """
        if isinstance(backend, AbstractWorkspaceFilesBackend):
            self.backend = backend
        else:
            self.backend = create_workspace_files_backend(
                workspace=workspace,
                workspace_config=workspace_config,
            )

    def view(self, path: str | None = None) -> str:
        """Show directory listing or file contents."""
        return self.backend.view(path)

    def write(self, path: str, content: str, mode: str = "create") -> str:
        """Create, append, or overwrite a file."""
        return self.backend.write(path, content, mode)

    def replace(self, path: str, old_str: str, new_str: str) -> str:
        """Replace all occurrences of old_str with new_str in a file."""
        return self.backend.replace(path, old_str, new_str)

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        """Insert text at a specific line number in a file."""
        return self.backend.insert(path, insert_line, insert_text)

    def delete(self, path: str) -> str:
        """Delete a file or directory."""
        return self.backend.delete(path)

    def rename(self, old_path: str, new_path: str) -> str:
        """Rename or move a file/directory."""
        return self.backend.rename(old_path, new_path)

    def clear(self) -> str:
        """Clear all workspace files."""
        return self.backend.clear()


def build_tool_registry_workspace_files(
    backend: AbstractWorkspaceFilesBackend | None,
    registry: ToolRegistry,
    workspace: Workspace | None = None,
    workspace_config: WorkspaceConfig | dict | None = None,
) -> ToolRegistry:
    """
    Register workspace file commands in a ToolRegistry.

    Args:
        backend: Optional backend instance. None uses workspace storage.
        registry: ToolRegistry to register commands with
    """
    workspace_files = WorkspaceFilesTool(
        backend=backend,
        workspace=workspace,
        workspace_config=workspace_config,
    )

    @registry.register_tool(
        name="workspace_file_view",
        description="""
        Inspect workspace files.
        
        Use this to **read** the contents of a file or **list** the files/directories
        inside a given path. 
        
        Why it exists:
        - Helps the agent explore workspace files before writing or modifying anything.
        - Essential for context gathering (what files exist, what's inside them).
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a file or directory inside workspace files. Example: '/workspace/notes.md', '/files/notes.md', or 'notes.md'.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    def workspace_file_view(path: str) -> str:
        return workspace_files.view(path)

    @registry.register_tool(
        name="workspace_file_write",
        description="""
        Safely create, append, or overwrite files in the workspace.
        
        Modes:
        - 'create': Create a new file. If file exists, returns an error with a preview.
        - 'append': Append text to an existing file. If file does not exist, returns error.
        - 'overwrite': Replace the entire file content. If file does not exist, returns error.
        
        Why it exists:
        - Prevents accidental overwrites (explicit overwrite mode required).
        - Supports incremental note-taking (append).
        - Provides safe, clear separation between create, append, and overwrite.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the target file inside workspace files.",
                },
                "content": {
                    "type": "string",
                    "description": "The text content to create, append, or overwrite.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["create", "append", "overwrite"],
                    "default": "create",
                    "description": "Choose how the file should be modified.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    )
    def workspace_file_write(path: str, content: str, mode: str = "create") -> str:
        return workspace_files.write(path, content, mode)

    @registry.register_tool(
        name="workspace_file_replace",
        description="""
        Replace all occurrences of a string inside a file.
        
        Why it exists:
        - Useful for correcting or updating specific words, phrases, or values.
        - Non-destructive to unrelated file contents.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the target file."},
                "old_str": {
                    "type": "string",
                    "description": "The string to search for.",
                },
                "new_str": {"type": "string", "description": "The replacement string."},
            },
            "required": ["path", "old_str", "new_str"],
            "additionalProperties": False,
        },
    )
    def workspace_file_replace(path: str, old_str: str, new_str: str) -> str:
        return workspace_files.replace(path, old_str, new_str)

    @registry.register_tool(
        name="workspace_file_insert",
        description="""
        Insert text into a file at a specific line number.
        
        Why it exists:
        - Allows precise placement of new content (e.g., add notes at top or bottom).
        - Maintains file structure without full overwrite.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the target file."},
                "insert_line": {
                    "type": "integer",
                    "description": "Line number to insert at (1-based index).",
                },
                "insert_text": {
                    "type": "string",
                    "description": "The text to insert at the given line.",
                },
            },
            "required": ["path", "insert_line", "insert_text"],
            "additionalProperties": False,
        },
    )
    def workspace_file_insert(path: str, insert_line: int, insert_text: str) -> str:
        return workspace_files.insert(path, insert_line, insert_text)

    @registry.register_tool(
        name="workspace_file_delete",
        description="""
        Delete a file or directory from workspace files.
        
        Why it exists:
        - Provides cleanup capability when files or directories are no longer needed.
        - Prevents clutter in the workspace files area.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or directory.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    def workspace_file_delete(path: str) -> str:
        return workspace_files.delete(path)

    @registry.register_tool(
        name="workspace_file_rename",
        description="""
        Rename or move a file/directory inside workspace files.
        
        Why it exists:
        - Enables reorganization of stored files without deleting data.
        - Useful for moving files between directories or renaming for clarity.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "old_path": {
                    "type": "string",
                    "description": "Current path of the file or directory.",
                },
                "new_path": {
                    "type": "string",
                    "description": "New desired path or name.",
                },
            },
            "required": ["old_path", "new_path"],
            "additionalProperties": False,
        },
    )
    def workspace_file_rename(old_path: str, new_path: str) -> str:
        return workspace_files.rename(old_path, new_path)

    @registry.register_tool(
        name="workspace_file_clear",
        description="""
        Clear all workspace files.
        """,
    )
    def workspace_file_clear() -> str:
        return workspace_files.clear()

    return registry
