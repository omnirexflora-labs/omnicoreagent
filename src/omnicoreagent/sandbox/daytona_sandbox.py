"""Daytona sandbox backend (hosted).

Each session is one Daytona sandbox: the manifest's image, environment, CPU,
and memory, with all network blocked unless the manifest allows it. Daytona
can enforce a host allow list, so an allowlist is honoured. Commands run
through its process API (argv is quoted into one shell command); files move
through its filesystem API and must stay inside the working directory.

Daytona returns a command's output as one stream, so ``stdout`` carries the
program's output and ``stderr`` is empty; the exit code is exact.

Requires the optional extra ``pip install "omnicoreagent[daytona]"`` and
``DAYTONA_API_KEY``.
"""

from __future__ import annotations

import posixpath
import shlex
from typing import Any
from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.network_check import (
    CHECK_TIMEOUT_SECONDS,
    NETWORK_CHECK_COMMAND,
    isolation_verdict,
    refuse_open_sandbox,
)
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxNetworkDefault,
    SandboxSession,
    SandboxSnapshot,
)

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000


class DaytonaSandboxRuntime(SandboxRuntime):
    provider = "daytona"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.image = options.pop("image", DEFAULT_IMAGE)
        self.api_key = options.pop("api_key", None)
        self.api_url = options.pop("api_url", None)
        self.target = options.pop("target", None)
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        self.verify_network_isolation = bool(options.pop("verify_network_isolation", True))
        if options:
            raise ValueError(f"Unknown daytona sandbox option(s): {', '.join(sorted(options))}")
        self.telemetry_recorder = telemetry_recorder
        self._client: Any = None
        self._sessions: dict[str, SandboxSession] = {}
        self._sandboxes: dict[str, Any] = {}

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        daytona = _daytona()
        manifest.provider = self.provider
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        client = await self._daytona_client(daytona)
        parameters: dict[str, Any] = {
            "image": manifest.image or self.image,
            "env_vars": dict(manifest.environment.plain),
            # Ephemeral: the sandbox is deleted when the session ends.
            "ephemeral": True,
            **_network_parameters(manifest.network_policy),
        }
        resources = _resources(daytona, manifest)
        if resources is not None:
            parameters["resources"] = resources
        sandbox = await client.create(daytona.CreateSandboxFromImageParams(**parameters))
        await sandbox.fs.create_folder(manifest.working_dir, "755")
        isolation = await self._check_isolation(client, sandbox, manifest)
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={
                "sandbox_id": getattr(sandbox, "id", None),
                "network_isolation": isolation,
            },
        )
        self._sessions[session_id] = session
        self._sandboxes[session_id] = sandbox
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None and self._client is not None:
            await self._client.delete(sandbox)
        await self._close_client_if_idle()

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        sandbox = self._sandbox(session_id)
        session = self._sessions[session_id]
        command = shlex.join(request.command)
        if request.stdin:
            path = posixpath.join(session.manifest.working_dir, f".stdin_{uuid4().hex}")
            await self.write_file(session_id, path, request.stdin.encode())
            command = f"{command} < {shlex.quote(path)}"
        timed_out = False
        try:
            response = await sandbox.process.exec(
                command,
                cwd=request.cwd or session.manifest.working_dir,
                env={str(key): str(value) for key, value in (request.environment or {}).items()},
                timeout=int(request.timeout_seconds) if request.timeout_seconds else None,
            )
            exit_code = response.exit_code
            output = response.result or ""
        except Exception as exc:
            if "timeout" not in type(exc).__name__.lower():
                raise
            timed_out, exit_code, output = True, 124, str(exc)
        stdout, truncated = self._bounded(output)
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
            timed_out=timed_out,
            # Daytona returns one combined stream, so stderr is empty here.
            metadata={"stdout_truncated": truncated, "combined_output": True},
        )

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        sandbox = self._sandbox(session_id)
        resolved = self._inside_workdir(session_id, path)
        directory = posixpath.dirname(resolved)
        if directory:
            await sandbox.fs.create_folder(directory, "755")
        await sandbox.fs.upload_file(content, resolved)

    async def read_file(self, session_id: str, path: str) -> bytes:
        sandbox = self._sandbox(session_id)
        data = await sandbox.fs.download_file(self._inside_workdir(session_id, path))
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        raise SandboxUnsupportedError(
            "Daytona fixes a sandbox's network when it is created; set the network "
            "policy in the manifest before the session starts"
        )

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        sandbox = self._sandbox(session_id)
        create = getattr(sandbox, "create_snapshot", None)
        if create is None:
            return None
        snapshot = await create()
        return SandboxSnapshot(
            snapshot_id=getattr(snapshot, "id", str(snapshot)),
            session_id=session_id,
            metadata={"provider": self.provider},
        )

    async def _daytona_client(self, daytona: Any):
        if self._client is None:
            settings = {
                key: value
                for key, value in (
                    ("api_key", self.api_key),
                    ("api_url", self.api_url),
                    ("target", self.target),
                )
                if value
            }
            self._client = daytona.AsyncDaytona(
                daytona.DaytonaConfig(**settings) if settings else None
            )
        return self._client

    async def _check_isolation(
        self, client: Any, sandbox: Any, manifest: SandboxManifest
    ) -> str:
        """Refuse a sandbox that was asked to have no network and still has one."""
        if not _restricts_traffic(manifest.network_policy):
            return "not required"
        if not self.verify_network_isolation:
            return "unchecked"
        response = await sandbox.process.exec(
            NETWORK_CHECK_COMMAND, timeout=CHECK_TIMEOUT_SECONDS
        )
        verdict = isolation_verdict(response.exit_code)
        if verdict != "isolated":
            await client.delete(sandbox)
            await self._close_client_if_idle()
        refuse_open_sandbox(self.provider, verdict)
        return "checked"

    async def _close_client_if_idle(self) -> None:
        """The client holds an HTTP session; with no sandbox left to talk to, it
        is closed rather than left open for the life of the process."""
        if self._sandboxes or self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
        self._client = None

    def _sandbox(self, session_id: str):
        sandbox = self._sandboxes.get(session_id)
        if sandbox is None:
            raise SandboxUnsupportedError(f"Sandbox session {session_id} is not open")
        return sandbox

    def _inside_workdir(self, session_id: str, path: str) -> str:
        workdir = self._sessions[session_id].manifest.working_dir
        resolved = posixpath.normpath(posixpath.join(workdir, path))
        if resolved != workdir and not resolved.startswith(workdir.rstrip("/") + "/"):
            raise PermissionError(f"Path {path} is outside the sandbox working directory")
        return resolved

    def _bounded(self, data: Any) -> tuple[str, bool]:
        text = data if isinstance(data, str) else (data or b"").decode(errors="replace")
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output_bytes:
            return text, False
        return encoded[: self.max_output_bytes].decode(errors="ignore"), True


def _daytona():
    from omnicoreagent._optional import load_optional

    return load_optional("the Daytona sandbox", "daytona", lambda: __import__("daytona"))


def _restricts_traffic(policy: NetworkPolicy) -> bool:
    """Whether the manifest asks for anything less than the open internet."""
    default = getattr(policy.default, "value", policy.default)
    return bool(policy.allowed_hosts) or default != SandboxNetworkDefault.ALLOW.value


def _network_parameters(policy: NetworkPolicy) -> dict[str, Any]:
    """Daytona enforces a host allow list, so an allowlist is honoured."""
    default = getattr(policy.default, "value", policy.default)
    if policy.allowed_hosts:
        return {"network_block_all": False, "network_allow_list": list(policy.allowed_hosts)}
    if default == SandboxNetworkDefault.ALLOW.value:
        return {"network_block_all": False}
    return {"network_block_all": True}


def _resources(daytona: Any, manifest: SandboxManifest) -> Any:
    resources = manifest.resources
    settings: dict[str, Any] = {}
    if resources.cpu:
        settings["cpu"] = int(float(resources.cpu))
    if resources.memory:
        settings["memory"] = _gigabytes(resources.memory)
    return daytona.Resources(**settings) if settings else None


def _gigabytes(size: str) -> int:
    text = str(size).strip().lower().rstrip("b")
    units = {"k": 1 / (1024 * 1024), "m": 1 / 1024, "g": 1}
    if text and text[-1] in units:
        return max(1, int(float(text[:-1]) * units[text[-1]]))
    return max(1, int(int(text) / (1024**3)))
