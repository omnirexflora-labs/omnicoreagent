"""Vercel Sandbox backend (hosted).

Each session is one Vercel Sandbox: the manifest's image, environment, vCPUs,
and memory, with the network denied unless the manifest allows it. Commands
run through the sandbox's process API with the argv the harness sends; files
move through its filesystem API and must stay inside the working directory.

Vercel's network policy has three modes (`deny-all`, `allow-all`, `custom`).
A host allowlist needs `custom` rules, which this adapter does not build, so
an allowlist is refused rather than pretended.

Requires the optional extra ``pip install "omnicoreagent[vercel]"`` and Vercel
credentials (`VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `VERCEL_PROJECT_ID`).
"""

from __future__ import annotations

import posixpath
from typing import Any
from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxNetworkDefault,
    SandboxSession,
    SandboxSnapshot,
)

DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000


class VercelSandboxRuntime(SandboxRuntime):
    provider = "vercel"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.image = options.pop("image", None)
        self.project_id = options.pop("project_id", None)
        self.timeout_seconds = int(options.pop("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        if options:
            raise ValueError(f"Unknown vercel sandbox option(s): {', '.join(sorted(options))}")
        self.telemetry_recorder = telemetry_recorder
        self._sessions: dict[str, SandboxSession] = {}
        self._sandboxes: dict[str, Any] = {}

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        vercel = _vercel()
        manifest.provider = self.provider
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        options: dict[str, Any] = {
            "execution_time_limit": self.timeout_seconds,
            "env": dict(manifest.environment.plain),
            "network_policy": _network_policy(vercel, manifest.network_policy),
        }
        image = manifest.image or self.image
        if image:
            options["image"] = image
        if self.project_id:
            options["project_id"] = self.project_id
        resources = _resources(vercel, manifest)
        if resources is not None:
            options["resources"] = resources
        sandbox = await vercel.create_sandbox(**options)
        await sandbox.fs.mkdir(manifest.working_dir, recursive=True)
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"sandbox_id": getattr(sandbox, "name", None), "image": image},
        )
        self._sessions[session_id] = session
        self._sandboxes[session_id] = sandbox
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            await sandbox.destroy()

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        sandbox = self._sandbox(session_id)
        session = self._sessions[session_id]
        command = list(request.command)
        if request.stdin:
            path = posixpath.join(session.manifest.working_dir, f".stdin_{uuid4().hex}")
            await self.write_file(session_id, path, request.stdin.encode())
            command = ["sh", "-c", 'exec "$@" < "$0"', path, *command]
        options: dict[str, Any] = {
            "cwd": request.cwd or session.manifest.working_dir,
            "env": {str(key): str(value) for key, value in (request.environment or {}).items()},
        }
        if request.timeout_seconds:
            # Vercel stops the process after this long.
            options["kill_after"] = float(request.timeout_seconds)
        finished = await sandbox.run_process(command[0], command[1:], **options)
        exit_code = finished.returncode
        stdout, stdout_truncated = self._bounded(finished.stdout)
        stderr, stderr_truncated = self._bounded(finished.stderr)
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            # A process Vercel stopped at its limit reports the kill signal.
            timed_out=bool(request.timeout_seconds) and exit_code in {124, 137, -9},
            metadata={
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        )

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        sandbox = self._sandbox(session_id)
        resolved = self._inside_workdir(session_id, path)
        directory = posixpath.dirname(resolved)
        if directory:
            await sandbox.fs.mkdir(directory, recursive=True)
        await sandbox.fs.write_bytes(resolved, content)

    async def read_file(self, session_id: str, path: str) -> bytes:
        sandbox = self._sandbox(session_id)
        data = await sandbox.fs.read_bytes(self._inside_workdir(session_id, path))
        return data if isinstance(data, bytes) else bytes(data or b"")

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        raise SandboxUnsupportedError(
            "Vercel fixes a sandbox's network policy when it is created; set it in "
            "the manifest before the session starts"
        )

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        sandbox = self._sandbox(session_id)
        take = getattr(sandbox, "snapshot", None)
        if take is None:
            return None
        snapshot = await take()
        return SandboxSnapshot(
            snapshot_id=getattr(snapshot, "id", str(snapshot)),
            session_id=session_id,
            metadata={"provider": self.provider},
        )

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


def _vercel():
    from omnicoreagent._optional import load_optional

    return load_optional(
        "the Vercel sandbox",
        "vercel",
        lambda: __import__("vercel.sandbox", fromlist=["create_sandbox"]),
    )


def _network_policy(vercel: Any, policy: NetworkPolicy) -> Any:
    if policy.allowed_hosts:
        raise SandboxUnsupportedError(
            "This Vercel adapter does not build custom network rules; use network "
            "default 'deny' or 'allow'"
        )
    default = getattr(policy.default, "value", policy.default)
    mode = "allow-all" if default == SandboxNetworkDefault.ALLOW.value else "deny-all"
    return vercel.NetworkPolicy(mode=mode, allow={})


def _resources(vercel: Any, manifest: SandboxManifest) -> Any:
    resources = manifest.resources
    settings: dict[str, Any] = {}
    if resources.cpu:
        settings["vcpus"] = int(float(resources.cpu))
    if resources.memory:
        settings["memory"] = _megabytes(resources.memory)
    if not settings:
        return None
    models = __import__("vercel.sandbox._internal.models", fromlist=["SandboxResources"])
    return models.SandboxResources(**settings)


def _megabytes(size: str) -> int:
    text = str(size).strip().lower().rstrip("b")
    units = {"k": 1 / 1024, "m": 1, "g": 1024}
    if text and text[-1] in units:
        return max(1, int(float(text[:-1]) * units[text[-1]]))
    return max(1, int(int(text) / (1024 * 1024)))
