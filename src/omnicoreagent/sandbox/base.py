from __future__ import annotations

from abc import ABC, abstractmethod

from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxSession,
    SandboxSnapshot,
)


class SandboxRuntime(ABC):
    """Provider-neutral sandbox adapter contract."""

    provider: str = "unknown"
    supports_required_sandbox: bool = False

    @abstractmethod
    async def create(self, manifest: SandboxManifest) -> SandboxSession: ...

    @abstractmethod
    async def execute(
        self, session_id: str, request: SandboxExecRequest
    ) -> SandboxExecResult: ...

    @abstractmethod
    async def read_file(self, session_id: str, path: str) -> bytes: ...

    @abstractmethod
    async def write_file(self, session_id: str, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def set_network_policy(
        self, session_id: str, policy: NetworkPolicy
    ) -> None: ...

    @abstractmethod
    async def snapshot(self, session_id: str) -> SandboxSnapshot | None: ...

    @abstractmethod
    async def terminate(self, session_id: str) -> None: ...
