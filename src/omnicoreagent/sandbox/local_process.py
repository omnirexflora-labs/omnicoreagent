"""The local backend: commands run as processes on this machine.

This is not a sandbox. There is no boundary between the command and the
machine the agent runs on: a command can reach the network, read and write
any file the agent's user can, and start processes that outlive the run. It
exists for places where the machine is already the boundary — a disposable
container such as an evaluation task (Harbor), a CI job, a VM — and for
people who choose to run commands directly.

So the backend says what it is, and refuses what it cannot keep:

- It declares ``execution_surface = "host"`` and does not satisfy a policy
  that requires a sandbox (``supports_required_sandbox = False``). Commands
  are authorized as host process execution, which every built-in policy
  profile refuses or asks about until a rule allows it.
- A manifest must ask for what this backend really gives: network ``allow``
  and filesystem ``allow``, with no host or path lists. Images, mounts, CPU,
  memory, GPU, lifetimes and secret references are refused. A manifest that
  asks for isolation is refused rather than silently not isolated.
- The host's environment is not passed to commands unless the option
  ``inherit_environment`` is on, so the agent's own credentials (model API
  keys, tokens) are not handed to the model's commands by default. Files are
  another matter: a command can read whatever the user can.

What it does enforce: each command's time limit (the whole process group is
killed), a cap on captured output, and file reads and writes through the
runtime stay inside the working directory.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any
from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxFilesystemDefault,
    SandboxManifest,
    SandboxNetworkDefault,
    SandboxSession,
    SandboxSnapshot,
)

DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
# Exit code reported for a command killed at its time limit, as `timeout -s KILL` does.
KILLED_EXIT_CODE = 137
# Passed through even without inherit_environment, so ordinary tools work.
_BASE_ENVIRONMENT = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR", "TZ")
_READ_CHUNK = 65536
# How long output is still read after a command is killed.
_DRAIN_SECONDS = 5


class LocalProcessSandboxRuntime(SandboxRuntime):
    provider = "local"
    # Commands run, but nothing isolates them: a policy that requires a
    # sandbox is never satisfied by this backend.
    supports_required_sandbox = False
    supports_execution = True
    execution_surface = "host"

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        self.inherit_environment = options.pop("inherit_environment", False)
        self.create_working_dir = options.pop("create_working_dir", False)
        if options:
            raise ValueError(f"Unknown local sandbox option(s): {', '.join(sorted(options))}")
        for name in ("inherit_environment", "create_working_dir"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"local sandbox option {name!r} must be true or false")
        if self.max_output_bytes <= 0:
            raise ValueError("local sandbox option 'max_output_bytes' must be positive")
        self.telemetry_recorder = telemetry_recorder
        self._sessions: dict[str, SandboxSession] = {}
        # Process groups still running per session, killed when it closes.
        self._running: dict[str, set[asyncio.subprocess.Process]] = {}

    # --- lifecycle ----------------------------------------------------------

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        _refuse_unenforceable(manifest)
        working_dir = os.path.realpath(manifest.working_dir)
        if not os.path.isdir(working_dir):
            if not self.create_working_dir:
                raise SandboxUnsupportedError(
                    f"The local sandbox's working directory {manifest.working_dir} does not "
                    "exist; create it, set sandbox_manifest.working_dir to one that does, "
                    "or set the option create_working_dir=true"
                )
            await asyncio.to_thread(os.makedirs, working_dir, exist_ok=True)
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        manifest.provider = self.provider
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"working_dir": working_dir, "isolation": "none"},
        )
        self._sessions[session_id] = session
        self._running[session_id] = set()
        return session

    async def terminate(self, session_id: str) -> None:
        # The working directory is the user's; it is left as it is.
        self._sessions.pop(session_id, None)
        for process in self._running.pop(session_id, set()):
            await _kill(process)

    # --- execution ----------------------------------------------------------

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        session = self._session(session_id)
        manifest = session.manifest
        environment = self._environment(manifest, request)
        cwd = request.cwd or session.metadata["working_dir"]
        try:
            process = await asyncio.create_subprocess_exec(
                *request.command,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.PIPE if request.stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Its own process group, so a time limit kills what it started too.
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            # As a shell reports a command it cannot run.
            return SandboxExecResult(exit_code=126 if isinstance(exc, PermissionError) else 127,
                                     stderr=f"{exc.__class__.__name__}: {exc}")
        running = self._running.setdefault(session_id, set())
        running.add(process)
        try:
            return await self._collect(process, request)
        finally:
            running.discard(process)

    async def _collect(
        self, process: asyncio.subprocess.Process, request: SandboxExecRequest
    ) -> SandboxExecResult:
        stdout = _BoundedReader(process.stdout, self.max_output_bytes)
        stderr = _BoundedReader(process.stderr, self.max_output_bytes)

        async def feed() -> None:
            if request.stdin and process.stdin is not None:
                try:
                    process.stdin.write(request.stdin.encode())
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()

        work = asyncio.gather(feed(), stdout.read(), stderr.read(), process.wait())
        timed_out = False
        try:
            if request.timeout_seconds:
                await asyncio.wait_for(asyncio.shield(work), timeout=request.timeout_seconds)
            else:
                await asyncio.shield(work)
        except asyncio.TimeoutError:
            timed_out = True
            await _kill(process)
            try:
                # A process that left the group can still hold the output open.
                await asyncio.wait_for(work, timeout=_DRAIN_SECONDS)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            await _kill(process)
            work.cancel()
            raise
        exit_code = KILLED_EXIT_CODE if timed_out else process.returncode
        if exit_code is not None and exit_code < 0:
            # Killed by a signal: report it as a shell does.
            exit_code = 128 - exit_code
        return SandboxExecResult(
            exit_code=exit_code if exit_code is not None else 1,
            stdout=stdout.text(),
            stderr=stderr.text(),
            timed_out=timed_out,
            metadata={
                "stdout_truncated": stdout.truncated,
                "stderr_truncated": stderr.truncated,
            },
        )

    def _environment(self, manifest: SandboxManifest, request: SandboxExecRequest) -> dict[str, str]:
        if self.inherit_environment:
            environment = dict(os.environ)
        else:
            environment = {name: os.environ[name] for name in _BASE_ENVIRONMENT if name in os.environ}
        environment.update(manifest.environment.plain)
        environment.update({str(key): str(value) for key, value in (request.environment or {}).items()})
        return environment

    # --- files ----------------------------------------------------------------

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        target = self._inside_workdir(session_id, path)
        await asyncio.to_thread(_write, target, content)

    async def read_file(self, session_id: str, path: str) -> bytes:
        target = self._inside_workdir(session_id, path)
        return await asyncio.to_thread(_read, target)

    # --- network and snapshots ------------------------------------------------

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        self._session(session_id)
        _refuse_network_limits(policy)

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        self._session(session_id)
        return None

    # --- helpers --------------------------------------------------------------

    def _session(self, session_id: str) -> SandboxSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SandboxUnsupportedError(f"Sandbox session {session_id} is not open")
        return session

    def _inside_workdir(self, session_id: str, path: str) -> str:
        workdir = self._session(session_id).metadata["working_dir"]
        # Links are resolved, so a link cannot carry a write outside.
        resolved = os.path.realpath(os.path.join(workdir, path))
        if resolved != workdir and not resolved.startswith(workdir.rstrip("/") + "/"):
            raise PermissionError(f"Path {path} is outside the sandbox working directory")
        return resolved


class _BoundedReader:
    """Reads a stream to its end, keeping at most ``limit`` bytes of it."""

    def __init__(self, stream: asyncio.StreamReader | None, limit: int) -> None:
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    async def read(self) -> None:
        if self.stream is None:
            return
        while chunk := await self.stream.read(_READ_CHUNK):
            room = self.limit - len(self.data)
            if len(chunk) > room:
                self.truncated = True
            if room > 0:
                self.data.extend(chunk[:room])

    def text(self) -> str:
        return bytes(self.data).decode(errors="ignore" if self.truncated else "replace")


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if not hasattr(os, "killpg"):  # pragma: no cover - Windows has no process groups
            raise ProcessLookupError
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


def _write(target: str, content: bytes) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(content)


def _read(target: str) -> bytes:
    if os.path.isdir(target):
        raise IsADirectoryError(target)
    with open(target, "rb") as handle:
        return handle.read()


def _refuse_unenforceable(manifest: SandboxManifest) -> None:
    """Refuse a manifest that asks for isolation this backend cannot give."""
    _refuse_network_limits(manifest.network_policy)
    filesystem = manifest.filesystem_policy
    if (
        filesystem.default != SandboxFilesystemDefault.ALLOW
        or filesystem.readable_paths
        or filesystem.writable_paths
        or filesystem.denied_paths
    ):
        raise SandboxUnsupportedError(
            "The local sandbox cannot limit which files a command reaches; set "
            "sandbox_manifest.filesystem_policy to {'default': 'allow'} with no path "
            "lists, or use an isolating provider such as docker"
        )
    unsupported = []
    if manifest.image:
        unsupported.append("an image")
    if manifest.workspace_mount is not None:
        unsupported.append("a workspace mount (use working_dir)")
    resources = manifest.resources
    if resources.cpu or resources.memory or resources.gpu:
        unsupported.append("CPU, memory or GPU limits")
    if resources.timeout_seconds:
        unsupported.append("a sandbox lifetime (each command still has its own time limit)")
    if manifest.environment.secret_refs:
        unsupported.append("secret references")
    if unsupported:
        raise SandboxUnsupportedError(
            f"The local sandbox cannot provide {', '.join(unsupported)}; remove "
            "them from sandbox_manifest or use another provider"
        )


def _refuse_network_limits(policy: NetworkPolicy) -> None:
    if (
        policy.default != SandboxNetworkDefault.ALLOW
        or policy.allowed_hosts
        or policy.denied_hosts
    ):
        raise SandboxUnsupportedError(
            "The local sandbox cannot restrict the network; set "
            "sandbox_manifest.network_policy to {'default': 'allow'} with no host "
            "lists, or use an isolating provider such as docker"
        )
