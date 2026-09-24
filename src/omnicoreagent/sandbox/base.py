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
    # Whether the backend can run commands; tools that execute are hidden from
    # the model when it cannot.
    supports_execution: bool = False
    # Where commands really run, as governance sees it: "sandbox" for an
    # isolated backend, "host" for one that runs them on this machine.
    execution_surface: str = "sandbox"

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

    async def upload_files(self, session_id: str, files: dict[str, bytes]) -> None:
        """Write several files into a session; providers may batch this."""
        for path, content in files.items():
            await self.write_file(session_id, path, content)

    async def download_files(self, session_id: str, paths: list[str]) -> dict[str, bytes]:
        """Read several files from a session; providers may batch this."""
        return {path: await self.read_file(session_id, path) for path in paths}
