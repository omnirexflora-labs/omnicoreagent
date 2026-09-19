from __future__ import annotations

import inspect
import posixpath
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import (
    SandboxSessionNotFoundError,
    SandboxUnsupportedError,
)
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxProvider,
    SandboxSession,
    SandboxSnapshot,
    WorkspaceMountMode,
)


@dataclass(frozen=True, slots=True)
class SandboxCommandContext:
    session: SandboxSession
    network_policy: NetworkPolicy


SandboxCommand = Callable[..., SandboxExecResult | Awaitable[SandboxExecResult]]


class LocalTestSandboxRuntime(SandboxRuntime):
    """Safe local test adapter for tests and development harness wiring.

    It does not spawn shell processes. Callers explicitly register command
    handlers, which keeps Phase 6 focused on the sandbox interface instead of
    shipping an unrestricted process executor.
    """

    provider = SandboxProvider.LOCAL_TEST.value
    supports_required_sandbox = True
    supports_execution = True
    is_test_adapter = True

    def __init__(
        self,
        *,
        commands: dict[str, SandboxCommand] | None = None,
        telemetry_recorder=None,
    ) -> None:
        self.commands = dict(commands or {})
        self.telemetry_recorder = telemetry_recorder
        self.sessions: dict[str, SandboxSession] = {}
        self.files: dict[str, dict[str, bytes]] = {}
        self.network_policies: dict[str, NetworkPolicy] = {}

    def register_command(self, name: str, handler: SandboxCommand) -> None:
        self.commands[name] = handler

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        manifest.provider = SandboxProvider.LOCAL_TEST
        session = SandboxSession(
            session_id=manifest.sandbox_id or f"sandbox_{uuid4().hex}",
            provider=SandboxProvider.LOCAL_TEST,
            manifest=manifest,
        )
        self.sessions[session.session_id] = session
        self.files[session.session_id] = {}
        self.network_policies[session.session_id] = manifest.network_policy
        return session

    async def execute(
        self, session_id: str, request: SandboxExecRequest
    ) -> SandboxExecResult:
        self._require_session(session_id)
        return await self._run_registered_command(session_id, request)

    async def read_file(self, session_id: str, path: str) -> bytes:
        safe_path = self._validate_file_path(session_id, path, write=False)
        return self.files[session_id][safe_path]

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        safe_path = self._validate_file_path(session_id, path, write=True)
        self.files[session_id][safe_path] = content

    async def set_network_policy(
        self, session_id: str, policy: NetworkPolicy
    ) -> None:
        self._require_session(session_id)
        self.network_policies[session_id] = policy

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        self._require_session(session_id)
        return SandboxSnapshot(
            snapshot_id=f"sandbox_snapshot_{uuid4().hex}",
            session_id=session_id,
            metadata={"provider": self.provider},
        )

    async def terminate(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        self.files.pop(session_id, None)
        self.network_policies.pop(session_id, None)

    async def _run_registered_command(
        self, session_id: str, request: SandboxExecRequest
    ) -> SandboxExecResult:
        command_name = request.command[0] if request.command else ""
        handler = self.commands.get(command_name)
        if handler is None:
            return SandboxExecResult(
                exit_code=127,
                stderr=f"Command is not registered for local sandbox: {command_name}",
            )
        context = SandboxCommandContext(
            session=self._require_session(session_id),
            network_policy=self.network_policies[session_id],
        )
        if len(inspect.signature(handler).parameters) >= 2:
            result = handler(request, context)
        else:
            result = handler(request)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _require_session(self, session_id: str) -> SandboxSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise SandboxSessionNotFoundError(f"Sandbox session not found: {session_id}")
        return session

    def _validate_file_path(self, session_id: str, path: str, *, write: bool) -> str:
        session = self._require_session(session_id)
        mount = session.manifest.workspace_mount
        safe_path = _normalize_sandbox_path(path)
        if mount is None:
            return safe_path
        target = _normalize_sandbox_path(mount.target)
        inside_mount = safe_path == target or safe_path.startswith(f"{target}/")
        if not inside_mount:
            raise SandboxUnsupportedError(
                f"Path escapes sandbox workspace mount: {path}"
            )
        if write and mount.mode == WorkspaceMountMode.READ_ONLY:
            raise SandboxUnsupportedError("Sandbox workspace mount is read-only")
        return safe_path


def _normalize_sandbox_path(path: str) -> str:
    if not path.startswith("/"):
        raise SandboxUnsupportedError(f"Sandbox path must be absolute: {path}")
    normalized = posixpath.normpath(path)
    if normalized == "//":
        return "/"
    return normalized
