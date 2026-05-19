import json
import fnmatch
from pathlib import Path
from typing import Any

from omnicoreagent.core.workspace.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.workspace.paths import (
    WORKSPACE_FILE_PATH_PREFIXES,
    normalize_workspace_path,
)
from omnicoreagent.core.workspace.storage import WorkspaceStorage


class WorkspaceFilesBackend(AbstractWorkspaceFilesBackend):
    """File operations rooted inside the active workspace storage."""

    _PATH_PREFIXES = WORKSPACE_FILE_PATH_PREFIXES

    def __init__(self, storage: WorkspaceStorage):
        self.storage = storage
        self.storage.ensure_root()
        self.base_dir = getattr(storage, "root", None)

    def _coerce_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(item) for item in content)
        if isinstance(content, dict):
            return json.dumps(content, indent=2)
        return str(content)

    def _storage_kwargs(self) -> dict:
        return {"strip_prefixes": self._PATH_PREFIXES}

    def _location(self, path: str | Path | None = None) -> str:
        return self.storage.location(path or "", **self._storage_kwargs())

    def _list_directory(self, path: str | None = None) -> list:
        return self.storage.list_files(path, **self._storage_kwargs())

    def _walk_files(self, path: str | None = None) -> list[str]:
        files: list[str] = []

        for item in self._list_directory(path):
            item_path = item.path
            if item.is_dir:
                files.extend(self._walk_files(item_path))
            else:
                files.append(item_path)

        if not files and path and self.storage.exists(path, **self._storage_kwargs()):
            try:
                self.storage.read_text(path, **self._storage_kwargs())
                files.append(path)
            except IsADirectoryError:
                pass

        return files

    def ls(self, path: str | None = None) -> str:
        try:
            items = self._list_directory(path)
            if items:
                location = self._location(path)
                names = []
                for item in sorted(
                    items,
                    key=lambda entry: (not entry.is_dir, entry.name),
                ):
                    suffix = "/" if item.is_dir else ""
                    names.append(f"{item.name}{suffix}")
                return f"Contents of directory: {location}\n" + "\n".join(names)

            if path and self.storage.exists(path, **self._storage_kwargs()):
                try:
                    self.storage.read_text(path, **self._storage_kwargs())
                    return (
                        f"{self._location(path)} is a file. "
                        "Use read_file to read file contents."
                    )
                except IsADirectoryError:
                    return f"Contents of directory: {self._location(path)}\n(empty)"

            if path in (None, "") or self.storage.exists(
                path or "",
                **self._storage_kwargs(),
            ):
                return f"Contents of directory: {self._location(path)}\n(empty)"

            return (
                f"Path not found: {path}\n"
                f"Workspace files root: {self._location()}\n"
                f"Current contents:\n{self.view('')}"
            )
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error listing workspace files: {e}"

    def read(self, path: str) -> str:
        try:
            if self._list_directory(path):
                return f"{self._location(path)} is a directory. Use ls to list it."

            if not self.storage.exists(path, **self._storage_kwargs()):
                return f"File not found: {path}"

            try:
                content = self.storage.read_text(path, **self._storage_kwargs())
            except IsADirectoryError:
                return f"{self._location(path)} is a directory. Use ls to list it."

            return f"Contents of file {self._location(path)}:\n{content}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error reading workspace file: {e}"

    def view(self, path: str | None = None) -> str:
        if path and self.storage.exists(path, **self._storage_kwargs()):
            try:
                content = self.storage.read_text(path, **self._storage_kwargs())
                return f"Contents of file {self._location(path)}:\n{content}"
            except IsADirectoryError:
                return self.ls(path)
            except Exception:
                pass
        return self.ls(path)

    def write(self, path: str, content: Any, mode: str = "create") -> str:
        content = self._coerce_content(content)

        try:
            exists = self.storage.exists(path, **self._storage_kwargs())
            location = self._location(path)

            if mode == "create":
                if exists:
                    preview = self.storage.read_text(
                        path, **self._storage_kwargs()
                    ).splitlines()[:5]
                    return (
                        f"File already exists: {location}\n"
                        f"--- Preview (first 5 lines) ---\n{''.join(preview)}\n"
                        "Use mode='append' or mode='overwrite'."
                    )
                self.storage.write_text(path, content, **self._storage_kwargs())
                return f"New file created: {location}"

            if mode == "append":
                if not exists:
                    return f"Cannot append: File not found at {location}\nUse mode='create'."
                self.storage.append_text(path, content, **self._storage_kwargs())
                return f"Appended text to {location}"

            if mode == "overwrite":
                if not exists:
                    return f"Cannot overwrite: File not found at {location}\nUse mode='create'."
                self.storage.write_text(path, content, **self._storage_kwargs())
                return f"File overwritten: {location}"

            return f"Invalid mode '{mode}'. Allowed modes: create, append, overwrite."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error writing workspace file: {e}"

    def replace(self, path: str, old_str: str, new_str: str) -> str:
        try:
            if not self.storage.exists(path, **self._storage_kwargs()):
                return f"File not found: {path}"

            content = self.storage.read_text(path, **self._storage_kwargs())
            if old_str not in content:
                return f"String '{old_str}' not found in {self._location(path)}."

            self.storage.write_text(
                path,
                content.replace(old_str, new_str),
                **self._storage_kwargs(),
            )
            return f"Replaced '{old_str}' with '{new_str}' in {self._location(path)}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error replacing workspace file text: {e}"

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        try:
            if not self.storage.exists(path, **self._storage_kwargs()):
                return f"File not found: {path}"

            content = self.storage.read_text(path, **self._storage_kwargs())
            lines = content.splitlines()
            insert_index = max(0, min(insert_line - 1, len(lines)))
            lines.insert(insert_index, insert_text)
            updated = "\n".join(lines)
            if content.endswith("\n") or updated:
                updated += "\n"
            self.storage.write_text(path, updated, **self._storage_kwargs())
            return f"Inserted text at line {insert_line} in {self._location(path)}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error inserting workspace file text: {e}"

    def delete(self, path: str) -> str:
        try:
            exists = self.storage.exists(path, **self._storage_kwargs())
            has_children = bool(self._list_directory(path))
            if not exists and not has_children:
                return f"Path not found: {path}"

            self.storage.delete(path, **self._storage_kwargs())
            return f"Deleted: {self._location(path)}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error deleting workspace file: {e}"

    def rename(self, old_path: str, new_path: str) -> str:
        try:
            has_children = bool(self._list_directory(old_path))
            if not self.storage.exists(old_path, **self._storage_kwargs()) and not has_children:
                return f"Path not found: {old_path}"

            old_location = self._location(old_path)
            new_location = self._location(new_path)
            self.storage.rename(old_path, new_path, **self._storage_kwargs())
            return f"Renamed {old_location} -> {new_location}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error renaming workspace file: {e}"

    def clear(self) -> str:
        try:
            root = self._location()
            self.storage.clear()
            return f"All workspace files cleared in {root}"
        except Exception as e:
            return f"Error clearing workspace files: {e}"

    def glob(self, pattern: str, path: str | None = None) -> str:
        try:
            pattern = normalize_workspace_path(
                pattern,
                strip_prefixes=self._PATH_PREFIXES,
            )

            root = path or ""
            matches = [
                file_path
                for file_path in self._walk_files(root)
                if fnmatch.fnmatch(file_path, pattern)
                or fnmatch.fnmatch(Path(file_path).name, pattern)
            ]
            if not matches:
                return f"No files matched pattern '{pattern}' under {root or '.'}."
            return "Matched files:\n" + "\n".join(sorted(matches))
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error matching workspace files: {e}"

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> str:
        try:
            candidates = self._walk_files(path or "")
            if include:
                include = normalize_workspace_path(
                    include,
                    strip_prefixes=self._PATH_PREFIXES,
                )
                candidates = [
                    file_path
                    for file_path in candidates
                    if fnmatch.fnmatch(file_path, include)
                    or fnmatch.fnmatch(Path(file_path).name, include)
                ]

            needle = pattern if case_sensitive else pattern.lower()
            matches: list[str] = []
            skipped = 0
            omitted = 0
            max_matches = max(1, int(max_matches))

            for file_path in sorted(candidates):
                try:
                    content = self.storage.read_text(
                        file_path,
                        **self._storage_kwargs(),
                    )
                except (UnicodeDecodeError, IsADirectoryError):
                    skipped += 1
                    continue

                for line_number, line in enumerate(content.splitlines(), start=1):
                    haystack = line if case_sensitive else line.lower()
                    if needle not in haystack:
                        continue
                    if len(matches) >= max_matches:
                        omitted += 1
                        continue
                    matches.append(f"{file_path}:{line_number}:{line}")

            if not matches:
                suffix = f" Skipped {skipped} unreadable files." if skipped else ""
                return f"No matches found for '{pattern}'.{suffix}"

            result = "Matches:\n" + "\n".join(matches)
            if omitted:
                result += f"\n... {omitted} more matches omitted."
            if skipped:
                result += f"\nSkipped {skipped} unreadable files."
            return result
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error searching workspace files: {e}"
