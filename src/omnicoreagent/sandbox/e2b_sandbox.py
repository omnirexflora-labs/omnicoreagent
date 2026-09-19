"""E2B sandbox backend (hosted).

Each session is one E2B sandbox: the manifest's template (image), environment,
and working directory, with internet access off unless the manifest allows it.
Commands run through E2B's command API (argv is quoted into one shell command,
so the arguments the harness sends are the arguments that run); files move
through its filesystem API and must stay inside the working directory.

E2B turns internet access on or off for a sandbox but does not enforce a host
allowlist, so an allowlist is refused rather than pretended, as in Docker.

Requires the optional extra ``pip install "omnicoreagent[e2b]"`` and
``E2B_API_KEY``.
"""

from __future__ import annotations

import posixpath
import shlex
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


class E2BSandboxRuntime(SandboxRuntime):
    provider = "e2b"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.template = options.pop("template", None)
        self.api_key = options.pop("api_key", None)
        self.timeout_seconds = int(options.pop("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        if options:
            raise ValueError(f"Unknown e2b sandbox option(s): {', '.join(sorted(options))}")
        self.telemetry_recorder = telemetry_recorder
        self._sessions: dict[str, SandboxSession] = {}
        self._sandboxes: dict[str, Any] = {}

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        e2b = _e2b()
        manifest.provider = self.provider
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        options: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "envs": dict(manifest.environment.plain),
            "allow_internet_access": _internet_allowed(manifest.network_policy),
        }
        template = manifest.image or self.template
        if template:
            options["template"] = template
        if self.api_key:
            options["api_key"] = self.api_key
        sandbox = await e2b.AsyncSandbox.create(**options)
        # E2B sandboxes start in the user's home; the working directory is ours.
        await sandbox.files.make_dir(manifest.working_dir)
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"sandbox_id": getattr(sandbox, "sandbox_id", None), "template": template},
        )
        self._sessions[session_id] = session
        self._sandboxes[session_id] = sandbox
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            await sandbox.kill()

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        sandbox = self._sandbox(session_id)
        session = self._sessions[session_id]
        command = shlex.join(request.command)
        if request.stdin:
            path = posixpath.join(session.manifest.working_dir, f".stdin_{uuid4().hex}")
            await self.write_file(session_id, path, request.stdin.encode())
            command = f"{command} < {shlex.quote(path)}"
        options: dict[str, Any] = {
            "cwd": request.cwd or session.manifest.working_dir,
            "envs": {str(key): str(value) for key, value in (request.environment or {}).items()},
        }
        if request.timeout_seconds:
            options["timeout"] = int(request.timeout_seconds)
        timed_out = False
        try:
            result = await sandbox.commands.run(command, **options)
            exit_code, stdout, stderr = result.exit_code, result.stdout, result.stderr
        except Exception as exc:  # E2B raises for a non-zero exit and for timeouts
            failure = getattr(exc, "result", None)
            if failure is None and not _is_timeout(exc):
                raise
            timed_out = _is_timeout(exc)
            exit_code = getattr(failure, "exit_code", 124 if timed_out else 1)
            stdout = getattr(failure, "stdout", "")
            stderr = getattr(failure, "stderr", str(exc))
        stdout, stdout_truncated = self._bounded(stdout)
        stderr, stderr_truncated = self._bounded(stderr)
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            metadata={
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        )

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        sandbox = self._sandbox(session_id)
        await sandbox.files.write(self._inside_workdir(session_id, path), content)

    async def read_file(self, session_id: str, path: str) -> bytes:
        sandbox = self._sandbox(session_id)
        data = await sandbox.files.read(self._inside_workdir(session_id, path), format="bytes")
        return data if isinstance(data, bytes) else bytes(data)

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        sandbox = self._sandbox(session_id)
        update = getattr(sandbox, "update_network", None)
        if update is None:
            raise SandboxUnsupportedError("This E2B version cannot change a sandbox's network")
        await update(allow_internet_access=_internet_allowed(policy))

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        sandbox = self._sandbox(session_id)
        create = getattr(sandbox, "create_snapshot", None)
        if create is None:
            return None
        snapshot = await create()
        return SandboxSnapshot(
            snapshot_id=getattr(snapshot, "snapshot_id", str(snapshot)),
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


def _e2b():
    from omnicoreagent._optional import load_optional

    return load_optional("the E2B sandbox", "e2b", lambda: __import__("e2b"))


def _is_timeout(exc: Exception) -> bool:
    return "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()


def _internet_allowed(policy: NetworkPolicy) -> bool:
    if policy.allowed_hosts:
        # E2B has no host allowlist; refusing is the only honest answer.
        raise SandboxUnsupportedError(
            "The E2B sandbox cannot enforce a network host allowlist; use network "
            "default 'deny' or 'allow'"
        )
    return getattr(policy.default, "value", policy.default) == SandboxNetworkDefault.ALLOW.value
