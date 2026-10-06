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
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.workspace.paths import normalize_workspace_path
from omnicoreagent.governance.capabilities import tool_authority_requests
from omnicoreagent.governance.models import AuthorityTarget
from omnicoreagent.governance.errors import (
    ApprovalRequiredError,
    PolicyDeniedError,
    UnknownCapabilityError,
)

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
# First every nested ``.git`` (a checkout: size 0, no digest), then the files.
_LIST_SCRIPT = (
    "find . -mindepth 2 -name .git -prune -exec sh -c "
    "'for f do printf \"0 - %s\\n\" \"$f\"; done' sh {} + ; "
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
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        self.storage = storage
        # Globs over workspace paths (``*`` crosses folders): what the bridge
        # copies either way. No include copies everything not excluded.
        self.include = list(include or [])
        self.exclude = list(exclude or [])
        self.governance_engine = governance_engine
        # Checks made during one copy, recorded as one summary after it.
        self._checks: dict[str, dict[str, Any]] = {}
        self.privacy_filter = privacy_filter
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        # Hash of each file's content as the sandbox has it, and the workspace
        # modification time last copied in, so only changes are copied.
        self._in_sandbox: dict[str, str] = {}
        self._copied_in_at: dict[str, Any] = {}
        # Files the last copy in left behind over max_files: reported with
        # the copy back, so the model is told (the rc7 gate, D F2: 104 of 600
        # files never went in and nothing said so).
        self._not_copied_in: list[str] = []
        # One copy at a time. A model's parallel execute calls each copied in;
        # the second skipped files the first was still uploading and its
        # command ran on a partial workspace (the 0.5.0rc4 gate).
        self._sync_lock = asyncio.Lock()

    def forget(self) -> None:
        """The sandbox is gone: nothing the bridge copied is in the next one."""
        self._in_sandbox.clear()
        self._copied_in_at.clear()

    # --- workspace -> sandbox -------------------------------------------------

    async def push(self, service: "SandboxExecutionService", session: "SandboxSession") -> list[str]:
        """Copy workspace files that changed since the last copy into the sandbox.

        Returns the paths copied in. A copy already in progress is waited for,
        so the command that follows sees every file.
        """
        async with self._sync_lock:
            return await self._push(service, session)

    async def _push(self, service: "SandboxExecutionService", session: "SandboxSession") -> list[str]:
        entries = await asyncio.to_thread(self._workspace_files)
        self._checks = {}
        uploads: dict[str, bytes] = {}
        total = 0
        # Only what is not in the sandbox already counts against the limit.
        changed = [(p, m) for p, m in entries if self._copied_in_at.get(p) != m]
        self._not_copied_in = [p for p, _ in changed[self.max_files :]]
        for path, modified_at in changed[: self.max_files]:
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
        await self._record_checks()
        return sorted(uploads)

    def _workspace_files(self) -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        pending: list[str | None] = [None]
        while pending:
            folder = pending.pop()
            try:
                items = self.storage.list_files(folder)
            except ValueError:
                # A link that leads out of the workspace: not the workspace's,
                # skipped as links are (the 0.5.0rc5 gate: it failed every
                # execute before its command ran).
                continue
            for item in items:
                path = str(item.path).replace("\\", "/").strip("/")
                if _hidden(path) or _run_record(path):
                    continue
                if not item.is_dir and not self._wanted(path):
                    continue
                if item.is_dir:
                    pending.append(path)
                else:
                    found.append((path, item.modified_at))
        return sorted(found)

    # --- sandbox -> workspace -------------------------------------------------

    async def pull(
        self,
        service: "SandboxExecutionService",
        session: "SandboxSession",
        *,
        after: dict[str, Any] | None = None,
    ) -> dict[str, list]:
        """Copy files the last command created or changed back into the workspace.

        ``after`` is that command's authority (its metadata ``authority``): the
        listing follows its decision unless a rule names the sync.
        """
        async with self._sync_lock:
            return await self._pull(service, session, after)

    async def _pull(
        self,
        service: "SandboxExecutionService",
        session: "SandboxSession",
        after: dict[str, Any] | None = None,
    ) -> dict[str, list]:
        from omnicoreagent.sandbox.execution import SandboxCommandSpec

        written: list[str] = []
        skipped: list[dict[str, str]] = []
        self._checks = {}
        spec = SandboxCommandSpec(
            command=["sh", "-c", _LIST_SCRIPT, str(self.max_file_bytes)],
            timeout_seconds=LIST_TIMEOUT_SECONDS,
            metadata={"purpose": "workspace_sync"},
            follows_decision=after,
        )
        spec.authority_request = sync_authority_request(spec)
        listing = await service.execute(spec, session=session)
        if listing.metadata.get("session_terminated"):
            # Lost after the command ran: said so, and the scope opens a
            # fresh sandbox for the next command (the rc9 gate, D).
            return {
                "written": written,
                "skipped": [{"path": "*", "reason": "the sandbox was lost before its files were copied back"}],
                "session_lost": True,
            }
        if listing.exit_code != 0:
            return {"written": written, "skipped": [{"path": ".", "reason": "could not list the sandbox files"}]}
        runtime = service._runtime()
        total = 0
        listed = _parse_listing(listing.stdout)
        # A folder the command made that holds a .git is a checkout, not the
        # agent's output: a steward worker cloned the repository inside the
        # bridged folder and its 470 files came back into the workspace, and
        # into every sandbox after. The workspace itself may be a repository.
        checkouts = sorted(
            {path.rpartition("/")[0] for _, _, path in listed if path.endswith("/.git")} - {""}
        )
        for checkout in checkouts:
            skipped.append(
                {
                    "path": checkout,
                    "reason": "a git checkout; not copied back: clone repositories "
                    "outside the workspace folder",
                }
            )
        for path in self._not_copied_in:
            skipped.append({"path": path, "reason": f"not copied in: over the bridge's limit of {self.max_files} files"})
        self._not_copied_in = []
        # Only what the sandbox changed counts against the limit; the rest is
        # reported, not dropped (the rc7 gate, D F2).
        changed: list[tuple[int, str, str]] = []
        for size, digest, raw_path in listed:
            if raw_path.endswith("/.git"):
                continue
            try:
                path = normalize_workspace_path(raw_path)
            except ValueError:
                continue
            if not path or _hidden(path) or self._in_sandbox.get(path) == digest:
                continue
            changed.append((size, digest, path))
        for _, _, path in changed[self.max_files :]:
            skipped.append({"path": path, "reason": f"not copied back: over the bridge's limit of {self.max_files} files"})
        for size, digest, path in changed[: self.max_files]:
            if any(path == c or path.startswith(c + "/") for c in checkouts):
                continue
            if _run_record(path):
                skipped.append({"path": path, "reason": "a background run's record; the runtime's own"})
                self._in_sandbox[path] = digest
                continue
            if not self._wanted(path):
                skipped.append({"path": path, "reason": "outside the bridge's include/exclude patterns"})
                self._in_sandbox[path] = digest
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
            try:
                await asyncio.to_thread(self.storage.write_text, path, text)
            except ValueError:
                # Its workspace path is a link out of the workspace: never
                # written through (the 0.5.0rc5 gate: the command's output
                # became an error).
                skipped.append({"path": path, "reason": "a link out of the workspace; not written"})
                continue
            total += size
            written.append(path)
            # The copy is now current in the workspace; do not send it back in.
            self._copied_in_at.pop(path, None)
            entries = await asyncio.to_thread(self.storage.list_files, _parent(path))
            for item in entries:
                if str(item.path).replace("\\", "/").strip("/") == path:
                    self._copied_in_at[path] = item.modified_at
        await self._record_checks()
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
        checks = self._checks.setdefault(
            tool_name, {"capability": requests[0].capability if requests else "", "allowed": 0, "denied": []}
        )
        # A file an ask rule covers is skipped, never asked about: a copy
        # cannot pause a command mid-way, and asking created pending
        # approvals the run then waited on, missing from its result (the
        # rc7 gate, F: three .pyc files under an ask on project/*).
        from omnicoreagent.governance.models import PolicyEffect

        engine = self.governance_engine
        try:
            decided = [engine.evaluator.evaluate(engine.policy, r).effect for r in requests]
        except Exception:  # noqa: BLE001 - a policy that cannot say refuses
            decided = [PolicyEffect.DENY]
        if PolicyEffect.ASK in decided:
            checks["denied"].append(path)
            return False
        # A refusal goes through the engine, so it is recorded on its own.
        try:
            # Each file is checked; only a refusal is recorded on its own. The
            # rest are one summary per copy: the steward's trace held 8,812
            # recorded "allow"s for files copied into its sandboxes.
            await self.governance_engine.authorize_all(requests, record_allows=False)
        except (PolicyDeniedError, ApprovalRequiredError, UnknownCapabilityError):
            # A strict policy that names no file rule refuses by not matching
            # (the 0.5.0rc2 gate: the docs' own strict example failed every
            # execute). Anything else (budget, audit, evaluation failure)
            # stops the command.
            checks["denied"].append(path)
            return False
        checks["allowed"] += 1
        return True

    def _wanted(self, path: str) -> bool:
        if self.include and not any(fnmatchcase(path, p) for p in self.include):
            return False
        return not any(fnmatchcase(path, p) for p in self.exclude)

    async def _record_checks(self) -> None:
        from omnicoreagent.governance.telemetry import emit_policy_summary

        recorder = getattr(self.governance_engine, "telemetry_recorder", None)
        for checks in self._checks.values():
            await emit_policy_summary(
                recorder,
                purpose="sandbox workspace bridge",
                capability=checks["capability"],
                allowed=checks["allowed"],
                denied=checks["denied"],
            )
        self._checks = {}


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


_RUN_RECORDS = frozenset({"run.json", "events.jsonl"})


def sync_authority_request(spec, surface: str | None = None):
    """The runtime's own listing after a command, as ``sandbox.workspace.sync``.

    It is governed like everything else, but it is not a command the agent
    asked for: judged as ``process.exec``, command rules found its script
    opaque and denied it, and under a policy of command rules every execute
    failed after the agent's command had already run (the 0.5.0rc1 gate).
    """
    from omnicoreagent.governance.models import AuthorityRequest, AuthorityTarget

    name = spec.command[0] if spec.command else ""
    return AuthorityRequest(
        capability="sandbox.workspace.sync",
        actor="runtime",
        provider="sandbox",
        execution_surface=surface,
        target=AuthorityTarget(resource=name),
        risk_level="low",
        metadata={"command": {"name": name, "argc": len(spec.command)}, "purpose": "workspace_sync"},
    )


def _run_record(path: str) -> bool:
    """A background run's own record (``run_<id>/run.json``, ``events.jsonl``):
    the runtime's, not the agent's; every sandbox got every earlier run's."""
    parent, _, name = path.rpartition("/")
    return name in _RUN_RECORDS and parent.rpartition("/")[2].startswith("run_")


def _hidden(path: str) -> bool:
    return any(part.startswith(".") for part in path.split("/"))


def _parent(path: str) -> str | None:
    parent, _, _ = path.rpartition("/")
    return parent or None
