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
import copy
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from omnicoreagent.governance.errors import PolicyDeniedError, UnknownCapabilityError
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
                elif manifest is not None:
                    # Adapters write onto the manifest they are given.
                    manifest = copy.deepcopy(manifest)
                self._session = await self.service.open_session(manifest)
                from omnicoreagent.core.runs import current_run

                run = current_run()
                if run is not None:
                    # If the run pauses or its process stops, this sandbox is
                    # gone; a resumed run is told so.
                    await run.note_sandbox()
            return self._session

    async def execute(self, command: list[str], **spec: Any) -> SandboxExecResult:
        session = await self.session()
        bridge = self.workspace_bridge
        try:
            copied_in = await bridge.push(self.service, session) if bridge is not None else []
        except Exception:  # noqa: BLE001 - the sandbox went away between commands
            # It died after the last command (out of memory, removed, a
            # provider outage): copying in raised the provider's own error
            # (the rc8 gate, D). Opened afresh once, as a command that finds
            # its sandbox lost does; a second failure is a real one.
            await self._drop(session, lost=True)
            session = await self.session()
            copied_in = await bridge.push(self.service, session)
        result = await self.service.execute(SandboxCommandSpec(command=command, **spec), session=session)
        if result.metadata.get("session_terminated"):
            # The sandbox is gone (it died, or was stopped for ignoring its
            # limit): forget it, so the next command opens a fresh one.
            await self._drop(session, lost=bool(result.metadata.get("session_lost")))
            return result
        if bridge is not None:
            try:
                sync = await bridge.pull(
                    self.service, session, after=result.metadata.get("authority")
                )
            except (PolicyDeniedError, UnknownCapabilityError) as exc:
                # A rule the user wrote refuses the runtime's listing (or the
                # policy has one that does): the sandbox is fine, so it is kept
                # and the model is told why nothing came back, not that it was
                # "lost". Before 0.5.1 a strict policy with no rule did this
                # silently on every command.
                result.metadata["workspace"] = {
                    "written": [],
                    "skipped": [{"path": "*", "reason": f"the policy refused the runtime's listing of the sandbox's files (sandbox.workspace.sync), so nothing was copied back: {exc}"}],
                }
                return result
            except Exception as exc:  # noqa: BLE001 - lost after the command ran
                # The command finished; its sandbox did not survive to give
                # its files back. Said so, and the next command opens afresh.
                await self._drop(session, lost=True)
                result.metadata["workspace"] = {
                    "written": [],
                    "skipped": [{"path": "*", "reason": f"the sandbox was lost before its files were copied back ({type(exc).__name__})"}],
                }
                return result
            if sync.pop("session_lost", False):
                result.metadata["workspace"] = sync
                await self._drop(session, lost=True)
                return result
            result.metadata["workspace"] = sync
            await self._record_sync(session, copied_in, sync)
        return result

    async def _drop(self, session: SandboxSession, *, lost: bool) -> None:
        async with self._lock:
            if self._session is session:
                self._session = None
        if self.workspace_bridge is not None:
            self.workspace_bridge.forget()
        await self.service.close_session(session, lost=lost)

    async def _record_sync(self, session: SandboxSession, copied_in: list[str], sync: dict) -> None:
        # Paths are recorded like the workspace tools' paths: as facts.
        from omnicoreagent.sandbox.telemetry import emit_sandbox_event

        await emit_sandbox_event(
            getattr(self.service.governance_engine, "telemetry_recorder", None),
            "sandbox_workspace_sync",
            metadata={
                "sandbox_session_id": session.session_id,
                "copied_in": copied_in,
                "written": list(sync["written"]),
                "skipped": [dict(item) for item in sync["skipped"]],
            },
        )

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
