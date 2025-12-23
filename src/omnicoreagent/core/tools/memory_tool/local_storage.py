from pathlib import Path
import urllib.parse
from filelock import FileLock
from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
import json
from typing import Any


class LocalMemoryBackend(AbstractMemoryBackend):
    """
    Local filesystem-based memory backend.
    Safe for concurrent access using file locks and atomic writes.
    """

    def __init__(self, base_dir="./memories"):
        self.base_dir = Path(base_dir).resolve()
        self._ensure_base_dir()

    def _ensure_base_dir(self):
        """Ensure the base directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str | None) -> Path:
        """Resolve a relative path safely inside the base directory."""
        self._ensure_base_dir()
        if not path or path.strip() == "":
            return self.base_dir

        decoded = urllib.parse.unquote(path).strip()
        decoded = decoded.lstrip("/")

        if decoded.startswith("memories/"):
            decoded = decoded[len("memories/") :]
        elif decoded == "memories":
            decoded = ""

        candidate = (self.base_dir / decoded).resolve()

        try:
            candidate.relative_to(self.base_dir)
        except ValueError:
            raise ValueError(
                f"Invalid path '{path}' → resolved outside base directory.\n"
                f"Path traversal detected.\nAll paths must stay inside: {self.base_dir}"
            )
        return candidate

    def _describe_dir(self) -> str:
        self._ensure_base_dir()
        contents = list(self.base_dir.iterdir())
        if not contents:
            return "(empty)"
        return "\n".join(p.name for p in contents)

    def _write_atomic(self, abs_path: Path, content: str):
        """Write to a file atomically with a lock."""
        lock_path = abs_path.with_suffix(".lock")
        with FileLock(lock_path):
            tmp_path = abs_path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(abs_path)

    def _append_atomic(self, abs_path: Path, content: str):
        """Append to a file safely with a lock."""
        lock_path = abs_path.with_suffix(".lock")
        with FileLock(lock_path):
            if abs_path.exists():
                existing = abs_path.read_text(encoding="utf-8")
                combined = existing.rstrip("\n") + "\n" + content
            else:
                combined = content
            tmp_path = abs_path.with_suffix(".tmp")
            tmp_path.write_text(combined, encoding="utf-8")
            tmp_path.rename(abs_path)

    def view(self, path: str | None = None) -> str:
        try:
            abs_path = self._resolve_path(path)
        except ValueError as e:
            return str(e)

        if abs_path.is_dir():
            contents = list(abs_path.iterdir())
            return f"Contents of directory: {abs_path}\n" + (
                "\n".join(p.name for p in contents) if contents else "(empty)"
            )
        elif abs_path.is_file():
            with FileLock(abs_path.with_suffix(".lock")):
                return f"Contents of file {abs_path}:\n{abs_path.read_text(encoding='utf-8')}"
        else:
            return f"Path not found: {path}\nBase directory: {self.base_dir}\nCurrent contents:\n{self._describe_dir()}"

    def create_update(self, path: str, file_text: Any, mode: str = "create") -> str:
        if isinstance(file_text, str):
            content = file_text
        elif isinstance(file_text, list):
            content = "\n".join(str(item) for item in file_text)
        elif isinstance(file_text, dict):
            content = json.dumps(file_text, indent=2)
        else:
            content = str(file_text)

        try:
            abs_path = self._resolve_path(path)
        except ValueError as e:
            return str(e)

        abs_path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "create":
            if abs_path.exists():
                with FileLock(abs_path.with_suffix(".lock")):
                    preview = "".join(
                        abs_path.read_text(encoding="utf-8").splitlines()[:5]
                    )
                return (
                    f"File already exists: {abs_path}\n"
                    f"--- Preview (first 5 lines) ---\n{preview}\n"
                    "Use mode='append' or mode='overwrite'."
                )
            self._write_atomic(abs_path, content)
            return f"New file created: {abs_path}"

        elif mode == "append":
            if not abs_path.exists():
                return (
                    f"Cannot append: File not found at {abs_path}\nUse mode='create'."
                )
            self._append_atomic(abs_path, content)
            return f"Appended text to {abs_path}"

        elif mode == "overwrite":
            if not abs_path.exists():
                return f"Cannot overwrite: File not found at {abs_path}\nUse mode='create'."
            self._write_atomic(abs_path, content)
            return f"File overwritten: {abs_path}"

        else:
            return f"Invalid mode '{mode}'. Allowed modes: create, append, overwrite."

    def str_replace(self, path: str, old_str: str, new_str: str) -> str:
        try:
            abs_path = self._resolve_path(path)
        except ValueError as e:
            return str(e)

        if not abs_path.is_file():
            return f"File not found: {path}"

        lock_path = abs_path.with_suffix(".lock")
        with FileLock(lock_path):
            content = abs_path.read_text(encoding="utf-8")
            if old_str not in content:
                return f"String '{old_str}' not found in {abs_path}."
            self._write_atomic(abs_path, content.replace(old_str, new_str))
        return f"Replaced '{old_str}' with '{new_str}' in {abs_path}"

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        try:
            abs_path = self._resolve_path(path)
        except ValueError as e:
            return str(e)

        if not abs_path.is_file():
            return f"File not found: {path}"

        lock_path = abs_path.with_suffix(".lock")
        with FileLock(lock_path):
            lines = abs_path.read_text(encoding="utf-8").splitlines()
            insert_index = max(0, min(insert_line - 1, len(lines)))
            lines.insert(insert_index, insert_text)
            self._write_atomic(abs_path, "\n".join(lines) + "\n")
        return f"Inserted text at line {insert_line} in {abs_path}"

    def delete(self, path: str) -> str:
        try:
            abs_path = self._resolve_path(path)
        except ValueError as e:
            return str(e)

        lock_path = abs_path.with_suffix(".lock")
        with FileLock(lock_path):
            if abs_path.is_file():
                abs_path.unlink()
                return f"File deleted: {abs_path}"
            elif abs_path.is_dir():
                try:
                    for sub_item in abs_path.rglob("*"):
                        if sub_item.is_file():
                            with FileLock(sub_item.with_suffix(".lock")):
                                sub_item.unlink()
                    abs_path.rmdir()
                    return f"Directory deleted: {abs_path}"
                except OSError:
                    return f"Directory not empty: {path}. Use recursive delete if intended."
            else:
                return f"Path not found: {path}"

    def rename(self, old_path: str, new_path: str) -> str:
        try:
            abs_old = self._resolve_path(old_path)
            abs_new = self._resolve_path(new_path)
        except ValueError as e:
            return str(e)

        if not abs_old.exists():
            return f"Path not found: {old_path}"

        abs_new.parent.mkdir(parents=True, exist_ok=True)
        lock_old = abs_old.with_suffix(".lock")
        lock_new = abs_new.with_suffix(".lock")
        with FileLock(lock_old), FileLock(lock_new):
            abs_old.rename(abs_new)
        return f"Renamed {abs_old} → {abs_new}"

    def clear_all_memory(self) -> str:
        dir_lock = self.base_dir / ".lock"
        try:
            with FileLock(dir_lock):
                for item in self.base_dir.iterdir():
                    if item.is_file():
                        with FileLock(item.with_suffix(".lock")):
                            item.unlink()
                    elif item.is_dir():
                        for sub_item in item.rglob("*"):
                            if sub_item.is_file():
                                with FileLock(sub_item.with_suffix(".lock")):
                                    sub_item.unlink()
                        item.rmdir()
            return f"All memory cleared in {self.base_dir}"
        except Exception as e:
            return f"Error clearing memory: {e}"
