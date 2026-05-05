import shutil
import urllib.parse
from pathlib import Path
from typing import Iterable

from filelock import FileLock


class LocalWorkspaceStorage:
    """Safe local storage rooted inside one workspace namespace."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.ensure_root()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(
        self,
        path: str | Path | None = None,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Path:
        self.ensure_root()
        if path is None or str(path).strip() == "":
            return self.root

        decoded = urllib.parse.unquote(str(path)).strip().lstrip("/")
        for prefix in strip_prefixes:
            clean_prefix = prefix.strip("/")
            if decoded == clean_prefix:
                decoded = ""
                break
            if decoded.startswith(f"{clean_prefix}/"):
                decoded = decoded[len(clean_prefix) + 1 :]
                break

        candidate = (self.root / decoded).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError(
                f"Invalid path '{path}' resolved outside workspace namespace.\n"
                f"Path traversal detected.\nAll paths must stay inside: {self.root}"
            )
        return candidate

    def describe_root(self) -> str:
        self.ensure_root()
        contents = list(self.root.iterdir())
        if not contents:
            return "(empty)"
        return "\n".join(path.name for path in contents)

    def read_text(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        with FileLock(resolved.with_suffix(".lock")):
            return resolved.read_text(encoding="utf-8")

    def write_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
        atomic: bool = True,
    ) -> Path:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if not atomic:
            resolved.write_text(content, encoding="utf-8")
            return resolved

        with FileLock(resolved.with_suffix(".lock")):
            tmp_path = resolved.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(resolved)
        return resolved

    def append_text(
        self,
        path: str | Path,
        content: str,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> Path:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(resolved.with_suffix(".lock")):
            if resolved.exists():
                existing = resolved.read_text(encoding="utf-8")
                content = existing.rstrip("\n") + "\n" + content
            tmp_path = resolved.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(resolved)
        return resolved

    def delete(self, path: str | Path, *, strip_prefixes: Iterable[str] = ()) -> str:
        resolved = self.resolve(path, strip_prefixes=strip_prefixes)
        with FileLock(resolved.with_suffix(".lock")):
            if resolved.is_file():
                resolved.unlink()
                return f"File deleted: {resolved}"
            if resolved.is_dir():
                shutil.rmtree(resolved)
                return f"Directory deleted: {resolved}"
            return f"Path not found: {path}"

    def rename(
        self,
        old_path: str | Path,
        new_path: str | Path,
        *,
        strip_prefixes: Iterable[str] = (),
    ) -> tuple[Path, Path]:
        old_resolved = self.resolve(old_path, strip_prefixes=strip_prefixes)
        new_resolved = self.resolve(new_path, strip_prefixes=strip_prefixes)
        if not old_resolved.exists():
            raise FileNotFoundError(str(old_path))

        new_resolved.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(old_resolved.with_suffix(".lock")), FileLock(
            new_resolved.with_suffix(".lock")
        ):
            old_resolved.rename(new_resolved)
        return old_resolved, new_resolved

    def clear(self) -> None:
        self.ensure_root()
        with FileLock(self.root.with_suffix(".lock")):
            for item in list(self.root.iterdir()):
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
