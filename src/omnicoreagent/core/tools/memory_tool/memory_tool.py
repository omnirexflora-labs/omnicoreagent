from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
from omnicoreagent.core.tools.memory_tool.local_storage import LocalMemoryBackend


class MemoryTool:
    """
    Memory Tool that delegates all operations to a backend
    implementing AbstractMemoryBackend.
    """

    def __init__(self, backend: AbstractMemoryBackend):
        if isinstance(backend, str):
            if backend == "local":
                backend = LocalMemoryBackend()

        self.backend = backend

    def view(self, path: str | None = None) -> str:
        if self.backend:
            return self.backend.view(path)

    def create_update(self, path: str, file_text: str, mode: str = "create") -> str:
        if self.backend:
            return self.backend.create_update(path, file_text, mode)

    def str_replace(self, path: str, old_str: str, new_str: str) -> str:
        if self.backend:
            return self.backend.str_replace(path, old_str, new_str)

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        if self.backend:
            return self.backend.insert(path, insert_line, insert_text)

    def delete(self, path: str) -> str:
        if self.backend:
            return self.backend.delete(path)

    def rename(self, old_path: str, new_path: str) -> str:
        if self.backend:
            return self.backend.rename(old_path, new_path)

    def clear_all_memory(self) -> str:
        if self.backend:
            return self.backend.clear_all_memory()


def build_tool_registry_memory_tool(
    memory_tool_backend: str, registry: ToolRegistry
) -> ToolRegistry:
    """
    Register memory tool commands in a ToolRegistry.

    Each command provides safe, controlled file system interactions inside a dedicated
    "memories" directory. Tools are designed to be descriptive, explicit, and prevent
    accidental destructive actions.
    """
    memory_tool = MemoryTool(backend=memory_tool_backend)

    @registry.register_tool(
        name="memory_view",
        description="""
        Inspect memory contents.
        
        Use this to **read** the contents of a file or **list** the files/directories
        inside a given path. 
        
        Why it exists:
        - Helps the agent explore the memory structure before writing or modifying anything.
        - Essential for context gathering (what files exist, what’s inside them).
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a file or directory inside the memory root. Example: '/memories/notes.md' or '/memories'.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    def memory_view(path: str) -> str:
        return memory_tool.view(path)

    @registry.register_tool(
        name="memory_create_update",
        description="""
        Safely create, append, or overwrite files in memory.
        
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
                    "description": "Path to the target file inside the memory root.",
                },
                "file_text": {
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
            "required": ["path", "file_text"],
            "additionalProperties": False,
        },
    )
    def memory_create_update(path: str, file_text: str, mode: str = "create") -> str:
        return memory_tool.create_update(path, file_text, mode)

    @registry.register_tool(
        name="memory_str_replace",
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
    def memory_str_replace(path: str, old_str: str, new_str: str) -> str:
        return memory_tool.str_replace(path, old_str, new_str)

    @registry.register_tool(
        name="memory_insert",
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
    def memory_insert(path: str, insert_line: int, insert_text: str) -> str:
        return memory_tool.insert(path, insert_line, insert_text)

    @registry.register_tool(
        name="memory_delete",
        description="""
        Delete a file or directory from memory.
        
        Why it exists:
        - Provides cleanup capability when files or directories are no longer needed.
        - Prevents clutter in the memory store.
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
    def memory_delete(path: str) -> str:
        return memory_tool.delete(path)

    @registry.register_tool(
        name="memory_rename",
        description="""
        Rename or move a file/directory inside the memory root.
        
        Why it exists:
        - Enables reorganization of stored memories without deleting data.
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
    def memory_rename(old_path: str, new_path: str) -> str:
        return memory_tool.rename(old_path, new_path)

    @registry.register_tool(
        name="memory_clear_all",
        description="""
        Clear all memory storage.
        """,
    )
    def memory_clear_all() -> str:
        return memory_tool.clear_all_memory()

    return registry
