from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Iterable

WORKSPACE_NAMESPACE_ARTIFACTS = "artifacts"
WORKSPACE_NAMESPACE_FILES = "files"
WORKSPACE_NAMESPACE_CONFIG = "config"
WORKSPACE_FILE_PATH_PREFIXES = ("workspace", "workspace_files", "files")


def normalize_workspace_path(
    path: str | Path | None = None,
    *,
    strip_prefixes: Iterable[str] = (),
) -> str:
    """Normalize a user-supplied workspace path into a safe relative path."""
    if path is None or str(path).strip() == "":
        return ""

    decoded = urllib.parse.unquote(str(path)).strip().lstrip("/")
    for prefix in strip_prefixes:
        clean_prefix = prefix.strip("/")
        if decoded == clean_prefix:
            decoded = ""
            break
        if decoded.startswith(f"{clean_prefix}/"):
            decoded = decoded[len(clean_prefix) + 1 :]
            break

    parts = [part for part in decoded.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Invalid path '{path}' resolved outside workspace namespace.")
    return "/".join(parts)
