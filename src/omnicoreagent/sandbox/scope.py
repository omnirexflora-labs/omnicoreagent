"""One sandbox session per agent run.

The run opens an `ExecutionScope`; the first command that needs the sandbox
opens a session through the governed service, later commands in the same run
reuse it (so files persist between them), and the scope closes it when the run
ends, whatever the outcome. With a workspace bridge, workspace files are copied
in before each command and its outputs copied back after it. The scope is a context variable, so concurrent runs
and subagents each have their own.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from omnicoreagent.sandbox.execution import SandboxCommandSpec, SandboxExecutionService
from omnicoreagent.sandbox.models import SandboxExecResult, SandboxManifest, SandboxSession

if TYPE_CHECKING:
    from omnicoreagent.sandbox.workspace_bridge import WorkspaceBridge

_CURRENT: ContextVar["ExecutionScope | None"] = ContextVar("omnicoreagent_execution", default=None)


def current_execution() -> "ExecutionScope | None":
    """The running agent's execution scope, or None when it has no sandbox."""
    return _CURRENT.get()


class ExecutionScope:
    def __init__(
        self,
        service: SandboxExecutionService,
        manifest: SandboxManifest | dict[str, Any] | None = None,
        *,
        workspace_bridge: "WorkspaceBridge | None" = None,
    ) -> None:
        self.service = service
        self.manifest = manifest
        self.workspace_bridge = workspace_bridge
        self._session: SandboxSession | None = None
        self._lock = asyncio.Lock()

    async def session(self) -> SandboxSession:
        async with self._lock:
            if self._session is None:
                manifest = self.manifest
                if isinstance(manifest, dict):
                    manifest = SandboxManifest(**manifest)
                self._session = await self.service.open_session(manifest)
            return self._session

    async def execute(self, command: list[str], **spec: Any) -> SandboxExecResult:
        session = await self.session()
        bridge = self.workspace_bridge
        if bridge is not None:
            await bridge.push(self.service, session)
        result = await self.service.execute(SandboxCommandSpec(command=command, **spec), session=session)
        if bridge is not None and not result.metadata.get("session_terminated"):
            result.metadata["workspace"] = await bridge.pull(self.service, session)
        return result

    async def upload(self, files: dict[str, bytes]) -> None:
        session = await self.session()
        await self.service._runtime().upload_files(session.session_id, files)

    async def close(self) -> None:
        async with self._lock:
            session, self._session = self._session, None
        if session is not None:
            await self.service.close_session(session)

    @asynccontextmanager
    async def active(self):
        """Make this the current scope; close its session on exit."""
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)
            # Closing must finish even when the run is being cancelled.
            from omnicoreagent.core.runtime.deadline import complete_despite_cancellation

            await complete_despite_cancellation(self.close())
