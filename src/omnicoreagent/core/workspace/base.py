from abc import ABC, abstractmethod
from typing import Optional


class AbstractWorkspaceFilesBackend(ABC):
    """Storage contract for agent workspace files.

    Implementations are rooted inside the active workspace files area and expose
    file-style operations for scratchpads, notes, logs, task progress, and
    generated outputs.
    """

    @abstractmethod
    def ls(self, path: Optional[str] = None) -> str:
        """List directory contents."""
        pass

    @abstractmethod
    def read(self, path: str) -> str:
        """Read file contents."""
        pass

    @abstractmethod
    def view(self, path: Optional[str] = None) -> str:
        """Show directory listing or file contents."""
        pass

    @abstractmethod
    def write(self, path: str, content: str, mode: str = "create") -> str:
        """Create, append, or overwrite a file."""
        pass

    @abstractmethod
    def replace(self, path: str, old_str: str, new_str: str) -> str:
        """Replace all occurrences of old_str with new_str in a file."""
        pass

    @abstractmethod
    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        """Insert text at a specific line number in a file."""
        pass

    @abstractmethod
    def delete(self, path: str) -> str:
        """Delete a file or directory."""
        pass

    @abstractmethod
    def rename(self, old_path: str, new_path: str) -> str:
        """Rename or move a file/directory."""
        pass

    @abstractmethod
    def clear(self) -> str:
        """Clear all workspace files."""
        pass

    @abstractmethod
    def glob(self, pattern: str, path: Optional[str] = None) -> str:
        """Find files by pattern."""
        pass

    @abstractmethod
    def grep(
        self,
        pattern: str,
        path: Optional[str] = None,
        include: Optional[str] = None,
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> str:
        """Search file contents."""
        pass
