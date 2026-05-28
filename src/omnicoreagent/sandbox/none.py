from __future__ import annotations

from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxProvider,
    SandboxSession,
    SandboxSnapshot,
)


class NoneSandboxRuntime(SandboxRuntime):
    """No-op adapter for policy-only mode.

    This adapter intentionally does not satisfy `sandbox_required` constraints.
    It exists so applications can pass an explicit "no external sandbox"
    runtime while still using the provider-neutral interface.
    """

    provider = SandboxProvider.NONE.value
    supports_required_sandbox = False

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        manifest.provider = SandboxProvider.NONE
        return SandboxSession(
            session_id=manifest.sandbox_id or f"sandbox_{uuid4().hex}",
            provider=SandboxProvider.NONE,
            manifest=manifest,
        )

    async def execute(
        self, session_id: str, request: SandboxExecRequest
    ) -> SandboxExecResult:
        raise SandboxUnsupportedError("none sandbox runtime cannot execute commands")

    async def read_file(self, session_id: str, path: str) -> bytes:
        raise SandboxUnsupportedError("none sandbox runtime cannot read files")

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        raise SandboxUnsupportedError("none sandbox runtime cannot write files")

    async def set_network_policy(
        self, session_id: str, policy: NetworkPolicy
    ) -> None:
        return None

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        return None

    async def terminate(self, session_id: str) -> None:
        return None
