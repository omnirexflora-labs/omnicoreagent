import json
import fnmatch
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from omnicoreagent.core.workspace.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.workspace.paths import (
    WORKSPACE_FILE_PATH_PREFIXES,
    normalize_workspace_path,
)
from omnicoreagent.core.workspace.storage import WorkspaceStorage



# The folder operations a person approved as a whole, as ``folder_operation_key``:
# the governed tool runner sets it for the one call it just authorized, so the
# files under an ask rule inside that folder count as approved for that
# operation and no other (0.5.1, B5: such a folder was refused with "does not
# allow", though a person could have approved it).
APPROVED_FOLDER_OPERATIONS: ContextVar[frozenset[str]] = ContextVar(
    "omnicoreagent_approved_folder_operations", default=frozenset()
)


def folder_operation_key(tool_name: str, tool_args: dict) -> str:
    return f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"


class FileOpFailed(str):
    """A file operation's result that is a failure (not found, already
    exists, a storage error). It is still the text the caller reads; the
    workspace tools report it as an error rather than a success."""

def _folder_refusal(doing: str, verb: str, where: str, file_path: str, why: str) -> str:
    if why == "asks":
        return (
            f"Refused: {doing} {where} would {verb} {file_path}, which the policy asks "
            "a person about, and this operation was not approved as a whole."
        )
    return (
        f"Refused: {doing} {where} would {verb} {file_path}, which the policy "
        f"does not allow {verb[:-1]}ing. {verb.capitalize()} only the files you may."
    )


class WorkspaceFilesBackend(AbstractWorkspaceFilesBackend):
    """File operations rooted inside the active workspace storage."""

    _PATH_PREFIXES = WORKSPACE_FILE_PATH_PREFIXES

    # Whether the policy would allow a workspace call, as allows(tool_name,
    # tool_args) (set when governed): search and listing leave out what it
    # may not read; a folder is deleted or moved only if each file under it
    # could be.
    allows: Any = None
    # What the policy would decide for a workspace call, as
    # effect(tool_name, tool_args) -> "allow", "ask" or "deny" (set when
    # governed). A folder operation is refused for a file under a deny rule;
    # one under an ask rule needs the operation to have been approved.
    effect: Any = None

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

    def _walk_files(self, path: str | None = None, *, readable_only: bool = True) -> list[str]:
        files: list[str] = []

        for item in self._list_directory(path):
            item_path = item.path
            if item.is_dir:
                files.extend(self._walk_files(item_path, readable_only=readable_only))
            elif not readable_only or self._may_read(item_path):
                files.append(item_path)
        if not readable_only:
            return files

        if not files and path and self._may_read(path) and self.storage.exists(path, **self._storage_kwargs()):
            try:
                self.storage.read_text(path, **self._storage_kwargs())
                files.append(path)
            except IsADirectoryError:
                pass

        return files

    def _may_read(self, path: str) -> bool:
        return self.allows is None or bool(self.allows("read_file", {"path": str(path)}))

    def folder_calls(self, tool_name: str, tool_args: dict) -> list[tuple[str, dict]]:
        """The per-file calls a folder operation makes (delete_file, move_file,
        clear_files), or none when it is not a folder operation. The governed
        tool runner decides them before the operation runs."""
        try:
            if tool_name == "delete_file":
                path = tool_args.get("path")
                if not self._list_directory(path):
                    return []
                return [("delete_file", {"path": f}) for f in self._walk_files(path, readable_only=False)]
            if tool_name == "move_file":
                old_path, new_path = tool_args.get("old_path"), tool_args.get("new_path")
                if not self._list_directory(old_path):
                    return []
                return [
                    ("move_file", args)
                    for args in (self._moved(f, old_path, new_path) for f in self._walk_files(old_path, readable_only=False))
                ]
            if tool_name == "clear_files":
                return [("delete_file", {"path": f}) for f in self._walk_files(None, readable_only=False)]
        except Exception:
            return []  # the operation itself reports a path it cannot read
        return []

    @staticmethod
    def _moved(file_path: str, old_path: str, new_path: str) -> dict:
        base = old_path.rstrip("/")
        tail = file_path[len(base) + 1 :] if file_path.startswith(base + "/") else Path(file_path).name
        return {"old_path": file_path, "new_path": f"{new_path.rstrip('/')}/{tail}"}

    def _refused_below(self, operation: tuple[str, dict], path: str | None, call) -> tuple[str, str] | None:
        """The first file under a folder that this operation may not touch, as
        (file, why), or None. ``call(file)`` gives the per-file call. A deny
        rule always refuses. An ask rule refuses unless a person approved this
        very operation as a whole (the runner asked once, naming the files)."""
        if self.allows is None:
            return None
        approved = folder_operation_key(*operation) in APPROVED_FOLDER_OPERATIONS.get()
        asks: tuple[str, str] | None = None
        for file_path in self._walk_files(path, readable_only=False):
            tool_name, tool_args = call(file_path)
            if self.allows(tool_name, tool_args):
                continue
            decided = self.effect(tool_name, tool_args) if self.effect is not None else "deny"
            if decided != "ask":
                return file_path, "denies"
            if not approved and asks is None:
                asks = (file_path, "asks")
        return asks

    def ls(self, path: str | None = None) -> str:
        try:
            items = [
                item for item in self._list_directory(path)
                if item.is_dir or self._may_read(item.path)
            ]
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

            return FileOpFailed(
                f"Path not found: {path}\n"
                f"Workspace files root: {self._location()}\n"
                f"Current contents:\n{self.view('')}"
            )
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error listing workspace files: {e}")

    def read(self, path: str) -> str:
        try:
            if self._list_directory(path):
                return FileOpFailed(f"{self._location(path)} is a directory. Use ls to list it.")

            if not self.storage.exists(path, **self._storage_kwargs()):
                return FileOpFailed(f"File not found: {path}")

            try:
                content = self.storage.read_text(path, **self._storage_kwargs())
            except IsADirectoryError:
                return FileOpFailed(f"{self._location(path)} is a directory. Use ls to list it.")

            return f"Contents of file {self._location(path)}:\n{content}"
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error reading workspace file: {e}")

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
                    return FileOpFailed(
                        f"File already exists: {location}\n"
                        f"--- Preview (first 5 lines) ---\n{''.join(preview)}\n"
                        "Use mode='append' or mode='overwrite'."
                    )
                self.storage.write_text(path, content, **self._storage_kwargs())
                return f"New file created: {location}"

            if mode == "append":
                if not exists:
                    return FileOpFailed(f"Cannot append: File not found at {location}\nUse mode='create'.")
                self.storage.append_text(path, content, **self._storage_kwargs())
                return f"Appended text to {location}"

            if mode == "overwrite":
                if not exists:
                    return FileOpFailed(f"Cannot overwrite: File not found at {location}\nUse mode='create'.")
                self.storage.write_text(path, content, **self._storage_kwargs())
                return f"File overwritten: {location}"

            return FileOpFailed(f"Invalid mode '{mode}'. Allowed modes: create, append, overwrite.")
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error writing workspace file: {e}")

    def replace(self, path: str, old_str: str, new_str: str) -> str:
        try:
            if not self.storage.exists(path, **self._storage_kwargs()):
                return FileOpFailed(f"File not found: {path}")

            content = self.storage.read_text(path, **self._storage_kwargs())
            if old_str not in content:
                return FileOpFailed(f"String '{old_str}' not found in {self._location(path)}.")

            self.storage.write_text(
                path,
                content.replace(old_str, new_str),
                **self._storage_kwargs(),
            )
            return f"Replaced '{old_str}' with '{new_str}' in {self._location(path)}"
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error replacing workspace file text: {e}")

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        try:
            if not self.storage.exists(path, **self._storage_kwargs()):
                return FileOpFailed(f"File not found: {path}")

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
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error inserting workspace file text: {e}")

    def delete(self, path: str) -> str:
        try:
            exists = self.storage.exists(path, **self._storage_kwargs())
            has_children = bool(self._list_directory(path))
            if not exists and not has_children:
                return FileOpFailed(f"Path not found: {path}")
            if has_children:
                protected = self._refused_below(
                    ("delete_file", {"path": path}), path, lambda f: ("delete_file", {"path": f})
                )
                if protected is not None:
                    return FileOpFailed(_folder_refusal("deleting", "delete", path, *protected))

            self.storage.delete(path, **self._storage_kwargs())
            return f"Deleted: {self._location(path)}"
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error deleting workspace file: {e}")

    def rename(self, old_path: str, new_path: str) -> str:
        try:
            has_children = bool(self._list_directory(old_path))
            if not self.storage.exists(old_path, **self._storage_kwargs()) and not has_children:
                return FileOpFailed(f"Path not found: {old_path}")

            if has_children:
                def as_moved(f: str) -> tuple[str, dict]:
                    return "move_file", self._moved(f, old_path, new_path)

                protected = self._refused_below(
                    ("move_file", {"old_path": old_path, "new_path": new_path}), old_path, as_moved
                )
                if protected is not None:
                    return FileOpFailed(_folder_refusal("moving", "move", old_path, *protected))

            old_location = self._location(old_path)
            new_location = self._location(new_path)
            self.storage.rename(old_path, new_path, **self._storage_kwargs())
            return f"Renamed {old_location} -> {new_location}"
        except ValueError as e:
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error renaming workspace file: {e}")

    def clear(self) -> str:
        try:
            root = self._location()
            protected = self._refused_below(
                ("clear_files", {}), None, lambda f: ("delete_file", {"path": f})
            )
            if protected is not None:
                return FileOpFailed(_folder_refusal("clearing", "delete", "the workspace", *protected))
            self.storage.clear()
            return f"All workspace files cleared in {root}"
        except Exception as e:
            return FileOpFailed(f"Error clearing workspace files: {e}")

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
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error matching workspace files: {e}")

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
            return FileOpFailed(str(e))
        except Exception as e:
            return FileOpFailed(f"Error searching workspace files: {e}")
