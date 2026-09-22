"""Workspace files in the sandbox; command outputs back as governed writes.

The workspace stays on the host and stays the source of truth. The sandbox
never mounts it: before each command, workspace files that changed since the
last copy are uploaded into the sandbox working directory, and after it, files
the command created or changed are copied back.

Every copy goes through governance exactly as the workspace tools do: a file
goes in only if a ``read_file`` of it would be allowed, and comes back only if a
``write_file`` of it would be. Output is untrusted, so it is also bounded:

- hidden paths (any component starting with ``.``, such as ``.skills``) and
  links are never copied back, and a path must stay inside the workspace;
- a file over ``max_file_bytes`` or not valid UTF-8 text is skipped, with the
  reason reported to the model;
- at most ``max_files`` files are considered in either direction;
- content is checked against the hash the sandbox reported, and the workspace
  privacy filter is applied before it is written.

Deletions are not propagated in either direction.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.workspace.paths import normalize_workspace_path
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.models import AuthorityTarget
from omnicoreagent.governance.errors import ApprovalRequiredError, PolicyDeniedError

if TYPE_CHECKING:
    from omnicoreagent.core.privacy import PrivacyFilter
    from omnicoreagent.core.workspace.storage import WorkspaceStorage
    from omnicoreagent.sandbox.execution import SandboxExecutionService
    from omnicoreagent.sandbox.models import SandboxSession

DEFAULT_MAX_FILES = 500
DEFAULT_MAX_FILE_BYTES = 2_000_000
DEFAULT_MAX_TOTAL_BYTES = 20_000_000
LIST_TIMEOUT_SECONDS = 60

# Lists regular files under the working directory, skipping hidden paths, as
# "<size> <sha256 or -> <./path>" lines. `find` does not follow links, and a
# link is not a regular file, so links are never listed.
_LIST_SCRIPT = (
    "find . \\( -name '.*' ! -name . \\) -prune -o -type f -exec sh -c '"
    "limit=$1; shift; "
    "for f do "
    's=$(($(wc -c < "$f"))); h=-; '
    'if [ "$s" -le "$limit" ]; then h=$(sha256sum < "$f"); h=${h%% *}; fi; '
    'printf \"%s %s %s\\n\" "$s" "$h" "$f"; '
    "done' sh \"$0\" {} +"
)


class WorkspaceBridge:
    def __init__(
        self,
        storage: "WorkspaceStorage",
        *,
        governance_engine: Any,
        privacy_filter: "PrivacyFilter | None" = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self.storage = storage
        self.governance_engine = governance_engine
        self.privacy_filter = privacy_filter
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        # Hash of each file's content as the sandbox has it, and the workspace
        # modification time last copied in, so only changes are copied.
        self._in_sandbox: dict[str, str] = {}
        self._copied_in_at: dict[str, Any] = {}

    def forget(self) -> None:
        """The sandbox is gone: nothing the bridge copied is in the next one."""
        self._in_sandbox.clear()
        self._copied_in_at.clear()

    # --- workspace -> sandbox -------------------------------------------------

    async def push(self, service: "SandboxExecutionService", session: "SandboxSession") -> list[str]:
        """Copy workspace files that changed since the last copy into the sandbox.

        Returns the paths copied in.
        """
        entries = await asyncio.to_thread(self._workspace_files)
        uploads: dict[str, bytes] = {}
        total = 0
        for path, modified_at in entries[: self.max_files]:
            if self._copied_in_at.get(path) == modified_at:
                continue
            if not await self._permitted("read_file", path):
                continue
            try:
                content = (await asyncio.to_thread(self.storage.read_text, path)).encode("utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            self._copied_in_at[path] = modified_at
            if len(content) > self.max_file_bytes or total + len(content) > self.max_total_bytes:
                continue
            digest = hashlib.sha256(content).hexdigest()
            if self._in_sandbox.get(path) == digest:
                continue
            uploads[path] = content
            total += len(content)
            self._in_sandbox[path] = digest
        if uploads:
            await service._runtime().upload_files(session.session_id, uploads)
        return sorted(uploads)

    def _workspace_files(self) -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        pending: list[str | None] = [None]
        while pending and len(found) < self.max_files:
            for item in self.storage.list_files(pending.pop()):
                path = str(item.path).replace("\\", "/").strip("/")
                if _hidden(path):
                    continue
                if item.is_dir:
                    pending.append(path)
                else:
                    found.append((path, item.modified_at))
        return sorted(found)

    # --- sandbox -> workspace -------------------------------------------------

    async def pull(
        self, service: "SandboxExecutionService", session: "SandboxSession"
    ) -> dict[str, list]:
        """Copy files the last command created or changed back into the workspace."""
        from omnicoreagent.sandbox.execution import SandboxCommandSpec

        written: list[str] = []
        skipped: list[dict[str, str]] = []
        listing = await service.execute(
            SandboxCommandSpec(
                command=["sh", "-c", _LIST_SCRIPT, str(self.max_file_bytes)],
                timeout_seconds=LIST_TIMEOUT_SECONDS,
                metadata={"purpose": "workspace_sync"},
            ),
            session=session,
        )
        if listing.exit_code != 0:
            return {"written": written, "skipped": [{"path": ".", "reason": "could not list the sandbox files"}]}
        runtime = service._runtime()
        total = 0
        for size, digest, raw_path in _parse_listing(listing.stdout)[: self.max_files]:
            try:
                path = normalize_workspace_path(raw_path)
            except ValueError:
                continue
            if not path or _hidden(path) or self._in_sandbox.get(path) == digest:
                continue
            if size > self.max_file_bytes or total + size > self.max_total_bytes:
                skipped.append({"path": path, "reason": f"too large to copy back ({size} bytes)"})
                self._in_sandbox[path] = digest
                continue
            if not await self._permitted("write_file", path):
                skipped.append({"path": path, "reason": "not permitted by policy"})
                self._in_sandbox[path] = digest
                continue
            content = await runtime.read_file(session.session_id, path)
            if hashlib.sha256(content).hexdigest() != digest:
                skipped.append({"path": path, "reason": "changed while it was being copied"})
                continue
            self._in_sandbox[path] = digest
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"path": path, "reason": "not text (only UTF-8 files are copied back)"})
                continue
            if "\x00" in text:
                skipped.append({"path": path, "reason": "not text (only UTF-8 files are copied back)"})
                continue
            if self.privacy_filter is not None:
                text = self.privacy_filter.redact_text(text, boundary="workspace")
            await asyncio.to_thread(self.storage.write_text, path, text)
            total += size
            written.append(path)
            # The copy is now current in the workspace; do not send it back in.
            self._copied_in_at.pop(path, None)
            entries = await asyncio.to_thread(self.storage.list_files, _parent(path))
            for item in entries:
                if str(item.path).replace("\\", "/").strip("/") == path:
                    self._copied_in_at[path] = item.modified_at
        return {"written": written, "skipped": skipped}

    # --- helpers --------------------------------------------------------------

    async def _permitted(self, tool_name: str, path: str) -> bool:
        requests = tool_authority_requests(
            tool_name=tool_name,
            tool_args={"path": path},
            tool_provider="workspace",
            actor="agent",
        )
        for request in requests:
            # The exact path read or written: the tools strip prefixes such as
            # "files/" from their arguments, the bridge does not.
            request.target = AuthorityTarget(path=path, tool_name=tool_name)
            request.metadata["purpose"] = "sandbox workspace bridge"
        try:
            await self.governance_engine.authorize_all(requests)
        except (PolicyDeniedError, ApprovalRequiredError):
            # Anything else (budget, audit, evaluation failure) stops the command.
            return False
        return True


def _parse_listing(stdout: str) -> list[tuple[int, str, str]]:
    entries = []
    for line in stdout.splitlines():
        size, _, rest = line.partition(" ")
        digest, _, path = rest.partition(" ")
        if not size.isdigit() or not path.startswith("./"):
            continue
        if digest != "-" and (len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest)):
            continue
        entries.append((int(size), digest, path[2:]))
    return sorted(entries, key=lambda entry: entry[2])


def _hidden(path: str) -> bool:
    return any(part.startswith(".") for part in path.split("/"))


def _parent(path: str) -> str | None:
    parent, _, _ = path.rpartition("/")
    return parent or None
