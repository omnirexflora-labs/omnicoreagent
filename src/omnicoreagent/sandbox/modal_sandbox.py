"""Modal sandbox backend (hosted).

Each session is one Modal Sandbox: a container in Modal's infrastructure, with
no network unless the manifest allows it, the manifest's image, environment,
CPU, and memory, and a working directory. Commands run with ``exec``; files
move through Modal's filesystem API and must stay inside the working
directory. The harness, credentials, policy, and telemetry stay here; only the
command and its environment go to Modal.

Unlike Docker, Modal can enforce a network host allowlist
(``outbound_domain_allowlist``), so an allowlist is honoured rather than
refused.

Requires the optional extra: ``pip install "omnicoreagent[modal]"`` and a
configured Modal account (``modal token new``, or ``MODAL_TOKEN_ID`` and
``MODAL_TOKEN_SECRET``).
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

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_APP_NAME = "omnicoreagent-sandbox"
# Modal stops a sandbox after this many seconds; a run's session is short.
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
_UNITS = {"k": 1, "m": 1, "g": 1024}


class ModalSandboxRuntime(SandboxRuntime):
    provider = "modal"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.image = options.pop("image", DEFAULT_IMAGE)
        self.app_name = options.pop("app_name", DEFAULT_APP_NAME)
        self.timeout_seconds = int(options.pop("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        if options:
            raise ValueError(f"Unknown modal sandbox option(s): {', '.join(sorted(options))}")
        self.telemetry_recorder = telemetry_recorder
        self._sessions: dict[str, SandboxSession] = {}
        self._sandboxes: dict[str, Any] = {}

    # --- lifecycle ----------------------------------------------------------

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        modal = _modal()
        manifest.provider = self.provider
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        app = await modal.App.lookup.aio(self.app_name, create_if_missing=True)
        policy = manifest.network_policy
        options: dict[str, Any] = {
            "app": app,
            "image": modal.Image.from_registry(manifest.image or self.image),
            "workdir": manifest.working_dir,
            "timeout": self.timeout_seconds,
            "env": dict(manifest.environment.plain),
            **_network_options(policy),
        }
        resources = manifest.resources
        if resources.cpu:
            options["cpu"] = float(resources.cpu)
        if resources.memory:
            options["memory"] = _megabytes(resources.memory)
        sandbox = await modal.Sandbox.create.aio(**options)
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"sandbox_id": getattr(sandbox, "object_id", None), "app": self.app_name},
        )
        self._sessions[session_id] = session
        self._sandboxes[session_id] = sandbox
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            await sandbox.terminate.aio()

    # --- execution ----------------------------------------------------------

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        sandbox = self._sandbox(session_id)
        session = self._sessions[session_id]
        options: dict[str, Any] = {
            "workdir": request.cwd or session.manifest.working_dir,
            "env": {str(key): str(value) for key, value in (request.environment or {}).items()},
        }
        if request.timeout_seconds:
            options["timeout"] = int(request.timeout_seconds)
        command = list(request.command)
        if request.stdin:
            path = posixpath.join(session.manifest.working_dir, f".stdin_{uuid4().hex}")
            await self.write_file(session_id, path, request.stdin.encode())
            command = ["sh", "-c", 'exec "$@" < "$0"', path, *command]
        process = await sandbox.exec.aio(*command, **options)
        exit_code = await process.wait.aio()
        stdout, stdout_truncated = self._bounded(await process.stdout.read.aio())
        stderr, stderr_truncated = self._bounded(await process.stderr.read.aio())
        # Modal reports a command stopped at its timeout with this exit code.
        timed_out = bool(request.timeout_seconds) and exit_code == 124
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

    # --- files ----------------------------------------------------------------

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        filesystem = self._sandbox(session_id).filesystem
        resolved = self._inside_workdir(session_id, path)
        directory = posixpath.dirname(resolved)
        if directory:
            await filesystem.make_directory.aio(directory, create_parents=True)
        await filesystem.write_bytes.aio(content, resolved)

    async def read_file(self, session_id: str, path: str) -> bytes:
        filesystem = self._sandbox(session_id).filesystem
        return await filesystem.read_bytes.aio(self._inside_workdir(session_id, path))

    # --- network and snapshots ------------------------------------------------

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        raise SandboxUnsupportedError(
            "Modal fixes a sandbox's network when it is created; set the network "
            "policy in the manifest before the session starts"
        )

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        sandbox = self._sandbox(session_id)
        snapshot = getattr(sandbox, "snapshot_filesystem", None)
        if snapshot is None:
            return None
        image = await snapshot.aio()
        return SandboxSnapshot(
            snapshot_id=getattr(image, "object_id", str(image)),
            session_id=session_id,
            metadata={"provider": self.provider},
        )

    # --- helpers --------------------------------------------------------------

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
        if len(text.encode("utf-8", errors="replace")) <= self.max_output_bytes:
            return text, False
        return text.encode("utf-8", errors="replace")[: self.max_output_bytes].decode(
            errors="ignore"
        ), True


def _modal():
    from omnicoreagent._optional import load_optional

    return load_optional("the Modal sandbox", "modal", lambda: __import__("modal"))


def _network_options(policy: NetworkPolicy) -> dict[str, Any]:
    """Modal enforces a domain allowlist, so an allowlist is honoured."""
    default = getattr(policy.default, "value", policy.default)
    if policy.allowed_hosts:
        return {
            "block_network": False,
            "outbound_domain_allowlist": list(policy.allowed_hosts),
        }
    if default == SandboxNetworkDefault.ALLOW.value:
        return {"block_network": False}
    return {"block_network": True}


def _megabytes(size: str) -> int:
    text = str(size).strip().lower().rstrip("b")
    if text and text[-1] in _UNITS:
        return int(float(text[:-1]) * _UNITS[text[-1]])
    # Plain numbers are bytes, as in the Docker backend.
    return max(1, int(int(text) / (1024 * 1024)))
