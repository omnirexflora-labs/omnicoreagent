"""Workspace file tools for scratchpads, logs, task output, and notes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.workspace.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.workspace.config import WorkspaceConfig
from omnicoreagent.core.workspace.factory import create_workspace_files_backend

if TYPE_CHECKING:
    from omnicoreagent.core.workspace.manager import Workspace

WORKSPACE_COMMAND_TOOL_NAMES = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "insert_file",
        "delete_file",
        "move_file",
        "clear_files",
        "glob",
        "grep",
    }
)

WORKSPACE_TOOL_MARKER = "_omnicoreagent_builtin_workspace_tool"


class WorkspaceFilesTool:
    """High-level file operations rooted inside the active workspace."""

    def __init__(
        self,
        workspace_files_backend: AbstractWorkspaceFilesBackend | None = None,
        workspace: Workspace | None = None,
        workspace_config: WorkspaceConfig | dict | None = None,
    ):
        """
        Initialize WorkspaceFilesTool with the workspace files adapter.
        
        Args:
            workspace_files_backend: Optional workspace files adapter for direct injection.
                None uses the active workspace files area.
            workspace_config: Explicit workspace config used when an adapter is
                not provided. None falls back to environment configuration.
        """
        if isinstance(workspace_files_backend, AbstractWorkspaceFilesBackend):
            self.files_backend = workspace_files_backend
        else:
            self.files_backend = create_workspace_files_backend(
                workspace=workspace,
                workspace_config=workspace_config,
            )

    def ls(self, path: str | None = None) -> str:
        """List directory contents inside workspace files."""
        return self.files_backend.ls(path)

    def read_file(self, path: str) -> str:
        """Read a file inside workspace files."""
        return self.files_backend.read(path)

    def write(self, path: str, content: str, mode: str = "create") -> str:
        """Create, append, or overwrite a file."""
        return self.files_backend.write(path, content, mode)

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        """Replace all occurrences of old_str with new_str in a file."""
        return self.files_backend.replace(path, old_str, new_str)

    def insert_file(self, path: str, insert_line: int, insert_text: str) -> str:
        """Insert text at a specific line number in a file."""
        return self.files_backend.insert(path, insert_line, insert_text)

    def delete(self, path: str) -> str:
        """Delete a file or directory."""
        return self.files_backend.delete(path)

    def rename(self, old_path: str, new_path: str) -> str:
        """Rename or move a file/directory."""
        return self.files_backend.rename(old_path, new_path)

    def clear(self) -> str:
        """Clear all workspace files."""
        return self.files_backend.clear()

    def glob(self, pattern: str, path: str | None = None) -> str:
        """Find files in workspace files by glob pattern."""
        return self.files_backend.glob(pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> str:
        """Search text in workspace files."""
        return self.files_backend.grep(
            pattern=pattern,
            path=path,
            include=include,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
        )


def _register_workspace_tool(
    registry: ToolRegistry,
    *,
    name: str,
    description: str,
    inputSchema: dict | None = None,
    function: Callable[..., Any],
) -> None:
    existing = registry.get_tool(name)
    if existing and not getattr(
        existing.function,
        WORKSPACE_TOOL_MARKER,
        False,
    ):
        raise ValueError(
            f"Tool name conflict: '{name}' is reserved for built-in workspace tools "
            "when enable_workspace_files=True. Rename the app tool or disable "
            "workspace files for this agent."
        )

    setattr(function, WORKSPACE_TOOL_MARKER, True)
    registry.register_tool(
        name=name,
        description=description,
        inputSchema=inputSchema,
    )(function)


def build_tool_registry_workspace_files(
    registry: ToolRegistry,
    workspace_files_backend: AbstractWorkspaceFilesBackend | None = None,
    workspace: Workspace | None = None,
    workspace_config: WorkspaceConfig | dict | None = None,
) -> ToolRegistry:
    """
    Register workspace file commands in a ToolRegistry.

    Args:
        workspace_files_backend: Optional workspace files adapter. None uses the active workspace files area.
        registry: ToolRegistry to register commands with
    """
    validate_workspace_tool_name_conflicts(registry)

    workspace_files = WorkspaceFilesTool(
        workspace_files_backend=workspace_files_backend,
        workspace=workspace,
        workspace_config=workspace_config,
    )

    def grep_tool(
        pattern: str,
        path: str = "",
        include: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> str:
        return workspace_files.grep(
            pattern=pattern,
            path=path,
            include=include,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
        )

    _register_workspace_tool(
        registry,
        name="ls",
        description="""
        List files and directories inside the workspace files area.

        Use this before reading or editing when you need to discover what exists.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path inside workspace files. Defaults to the workspace files root.",
                }
            },
            "additionalProperties": False,
        },
        function=lambda path="": workspace_files.ls(path),
    )

    _register_workspace_tool(
        registry,
        name="read_file",
        description="""
        Read a file inside workspace files. This is the workspace equivalent of cat.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file inside workspace files.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        function=lambda path: workspace_files.read_file(path),
    )

    _register_workspace_tool(
        registry,
        name="write_file",
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
        function=lambda path, content, mode="create": workspace_files.write(
            path,
            content,
            mode,
        ),
    )

    _register_workspace_tool(
        registry,
        name="edit_file",
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
        function=lambda path, old_str, new_str: workspace_files.edit_file(
            path,
            old_str,
            new_str,
        ),
    )

    _register_workspace_tool(
        registry,
        name="insert_file",
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
        function=lambda path, insert_line, insert_text: workspace_files.insert_file(
            path,
            insert_line,
            insert_text,
        ),
    )

    _register_workspace_tool(
        registry,
        name="delete_file",
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
        function=lambda path: workspace_files.delete(path),
    )

    _register_workspace_tool(
        registry,
        name="move_file",
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
        function=lambda old_path, new_path: workspace_files.rename(old_path, new_path),
    )

    _register_workspace_tool(
        registry,
        name="clear_files",
        description="""
        Clear all workspace files.
        """,
        function=lambda: workspace_files.clear(),
    )

    _register_workspace_tool(
        registry,
        name="glob",
        description="""
        Find workspace file paths by glob pattern.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern such as '*.md' or '**/*.txt'.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional directory path to scope the search.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        function=lambda pattern, path="": workspace_files.glob(pattern, path),
    )

    _register_workspace_tool(
        registry,
        name="grep",
        description="""
        Search text inside workspace files and return matching path:line:content results.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text to search for."},
                "path": {
                    "type": "string",
                    "description": "File or directory path to search. Defaults to workspace files root.",
                },
                "include": {
                    "type": "string",
                    "description": "Optional glob filter such as '*.md'.",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether matching should be case-sensitive.",
                },
                "max_matches": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum number of matches to return.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        function=grep_tool,
    )

    return registry


def validate_workspace_tool_name_conflicts(registry: ToolRegistry) -> None:
    conflicts = []
    for name in sorted(WORKSPACE_COMMAND_TOOL_NAMES):
        existing = registry.get_tool(name)
        if existing and not getattr(existing.function, WORKSPACE_TOOL_MARKER, False):
            conflicts.append(name)

    if conflicts:
        names = ", ".join(conflicts)
        raise ValueError(
            "Tool name conflict: built-in workspace tools reserve these names "
            f"when enable_workspace_files=True: {names}. Rename the app tool or "
            "disable workspace files for this agent."
        )
