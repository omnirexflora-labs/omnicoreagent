from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Iterable

WORKSPACE_NAMESPACE_ARTIFACTS = "artifacts"
WORKSPACE_NAMESPACE_FILES = "files"
WORKSPACE_NAMESPACE_CONFIG = "config"
WORKSPACE_FILE_PATH_PREFIXES = ("workspace", "workspace_files", "files")


# The workspace files roots in this process. An absolute path under one names
# that file: with the root at /app, "/app/ssl/x" was taken as "app/ssl/x" and
# written to /app/app/ssl/x (the 0.5.0rc5 gate). Stripped here, where storage
# and policy both normalize, so a rule on "ssl/*" sees what storage touches.
_ROOTS: set[str] = set()


def register_workspace_root(root: str | Path) -> None:
    for form in {str(root), str(Path(root).resolve())}:
        form = form.rstrip("/")
        if form:
            _ROOTS.add(form)


def _under_a_root(decoded: str) -> str | None:
    """The path relative to the root it is under; None when it is under none."""
    for root in sorted(_ROOTS, key=len, reverse=True):
        if decoded == root:
            return ""
        if decoded.startswith(root + "/"):
            return decoded[len(root) + 1 :]
    return None


def normalize_workspace_path(
    path: str | Path | None = None,
    *,
    strip_prefixes: Iterable[str] = (),
) -> str:
    """Normalize a user-supplied workspace path into a safe relative path."""
    if path is None or str(path).strip() == "":
        return ""

    decoded = urllib.parse.unquote(str(path)).strip()
    if decoded.startswith("/"):
        inside = _under_a_root(decoded)
        if inside is not None:
            decoded = inside
        elif decoded.strip("/") and not any(
            decoded.lstrip("/") == prefix.strip("/")
            or decoded.lstrip("/").startswith(prefix.strip("/") + "/")
            for prefix in strip_prefixes
        ):
            # An absolute path under no workspace root leads outside: refused,
            # as the docs say. It was taken as relative, so /tmp/x was written
            # to <root>/tmp/x and the record named /tmp/x (the 0.5.0rc6 gate).
            # "/" alone and "/files/..." still mean the workspace.
            raise ValueError(
                f"Invalid path '{path}': an absolute path outside the workspace. "
                "Use a path relative to the workspace files."
            )
    decoded = decoded.lstrip("/")
    while decoded.startswith("./"):
        decoded = decoded[2:]
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
