"""Docker sandbox backend (the open-source default).

Each session is one long-lived container; commands run in it with
``docker exec``. The container runs as an unprivileged user (``65534``,
"nobody") unless another numeric user is configured, has no network unless the
manifest allows it, a read-only root filesystem, a size-limited writable
working directory and ``/tmp`` (both in memory, removed with the container),
all Linux capabilities dropped, no privilege escalation, and memory, CPU, and
process limits. Only the manifest's environment reaches it; the host
environment never does. gVisor can be used as the container runtime
(``runtime="runsc"``) for a boundary that does not share the host kernel's
system call surface.

Host paths that would hand over the host are never mounted, whatever the
policy says: the container engine's socket, the root and system directories,
and credential directories in the user's home (or any directory containing
them), after resolving links.

All Docker SDK calls run in a worker thread so the event loop never blocks.
Requires the Docker SDK: ``pip install omnicoreagent[docker]``.
"""

from __future__ import annotations

import asyncio
import io
import os
import posixpath
import tarfile
from typing import Any
from uuid import uuid4

from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import (
    NetworkPolicy,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxManifest,
    SandboxSession,
    SandboxSnapshot,
    SandboxNetworkDefault,
)

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_PIDS_LIMIT = 256
DEFAULT_USER = "65534:65534"
DEFAULT_WORKDIR_SIZE = "1g"
LABEL = "omnicoreagent.sandbox"
# Extra time the host waits beyond a command's own limit before giving up on it.
HOST_TIMEOUT_GRACE_SECONDS = 10
_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


class DockerSandboxRuntime(SandboxRuntime):
    provider = "docker"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.image = options.pop("image", DEFAULT_IMAGE)
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        self.pids_limit = int(options.pop("pids_limit", DEFAULT_PIDS_LIMIT))
        self.pull = options.pop("pull", "if_missing")
        self.user = str(options.pop("user", DEFAULT_USER))
        self.uid, self.gid = _numeric_user(self.user)
        self.workdir_size = str(options.pop("workdir_size", DEFAULT_WORKDIR_SIZE))
        _bytes(self.workdir_size)
        self.container_runtime = options.pop("runtime", None)
        if options:
            raise ValueError(f"Unknown docker sandbox option(s): {', '.join(sorted(options))}")
        if self.pull not in {"if_missing", "never"}:
            raise ValueError("docker sandbox option 'pull' must be 'if_missing' or 'never'")
        self.telemetry_recorder = telemetry_recorder
        self._client = None
        self._sessions: dict[str, SandboxSession] = {}
        self._containers: dict[str, Any] = {}

    # --- lifecycle ----------------------------------------------------------

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        network_mode = _network_mode(manifest.network_policy)
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        image = manifest.image or self.image
        manifest.provider = self.provider
        client = await self._docker()
        await asyncio.to_thread(self._ensure_image, client, image)
        from docker.types import Mount

        from docker.types import DriverConfig

        # The working directory is an anonymous volume kept in memory, owned
        # by the sandbox user, and removed with the container. (A plain tmpfs
        # mount would not accept file copies.)
        mounts = [
            Mount(
                target=manifest.working_dir,
                source=None,
                type="volume",
                driver_config=DriverConfig(
                    "local",
                    options={
                        "type": "tmpfs",
                        "device": "tmpfs",
                        "o": f"size={self.workdir_size},uid={self.uid},gid={self.gid},mode=0700",
                    },
                ),
            )
        ]
        if manifest.workspace_mount is not None:
            _refuse_sensitive_mount(manifest.workspace_mount.source)
            mounts.append(
                Mount(
                    target=manifest.workspace_mount.target,
                    source=manifest.workspace_mount.source,
                    type="bind",
                    read_only=str(getattr(manifest.workspace_mount.mode, "value", manifest.workspace_mount.mode))
                    == "read_only",
                )
            )
        resources = manifest.resources
        run_options: dict[str, Any] = {
            "image": image,
            "command": ["sleep", "infinity"],
            "detach": True,
            "working_dir": manifest.working_dir,
            "network_mode": network_mode,
            "read_only": True,
            "tmpfs": {"/tmp": "rw,size=64m"},
            "mounts": mounts,
            "environment": {"HOME": "/tmp", **dict(manifest.environment.plain)},
            "user": self.user,
            "labels": {LABEL: "1", "omnicoreagent.session": session_id},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "pids_limit": self.pids_limit,
        }
        if self.container_runtime:
            run_options["runtime"] = self.container_runtime
        if resources.memory:
            run_options["mem_limit"] = _bytes(resources.memory)
        if resources.cpu:
            run_options["nano_cpus"] = int(float(resources.cpu) * 1_000_000_000)
        creating = asyncio.ensure_future(asyncio.to_thread(client.containers.run, **run_options))
        try:
            container = await asyncio.shield(creating)
        except Exception as exc:
            from docker.errors import APIError

            if isinstance(exc, APIError) and self.container_runtime and "runtime" in str(exc).lower():
                raise SandboxUnsupportedError(
                    f"Container runtime {self.container_runtime!r} is not available to Docker "
                    "(for gVisor, install runsc and register it with the Docker daemon)"
                ) from exc
            raise
        except asyncio.CancelledError:
            # The thread still creates the container; remove it before letting
            # the cancellation through, or it would be orphaned.
            from omnicoreagent.core.runtime.deadline import complete_despite_cancellation

            await complete_despite_cancellation(self._discard(creating))
            raise
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"container_id": container.id, "image": image},
        )
        self._sessions[session_id] = session
        self._containers[session_id] = container
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        container = self._containers.pop(session_id, None)
        if container is None:
            return
        from docker.errors import NotFound

        try:
            await asyncio.to_thread(container.remove, force=True, v=True)
        except NotFound:
            pass

    async def _discard(self, creating: "asyncio.Future") -> None:
        try:
            container = await creating
        except Exception:
            return
        await asyncio.to_thread(container.remove, force=True, v=True)

    async def cleanup_orphans(self) -> int:
        """Remove every OmniCoreAgent sandbox container left by earlier processes."""
        client = await self._docker()
        containers = await asyncio.to_thread(
            client.containers.list, all=True, filters={"label": LABEL}
        )
        for container in containers:
            await asyncio.to_thread(container.remove, force=True, v=True)
        return len(containers)

    # --- execution ----------------------------------------------------------

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        container = self._container(session_id)
        session = self._sessions[session_id]
        workdir = request.cwd or session.manifest.working_dir
        command = list(request.command)
        stdin_path = None
        if request.stdin:
            stdin_path = posixpath.join(session.manifest.working_dir, f".stdin_{uuid4().hex}")
            await self.write_file(session_id, stdin_path, request.stdin.encode())
            command = ["sh", "-c", 'exec "$@" < "$0"', stdin_path, *command]
        limit = request.timeout_seconds
        if limit:
            # Inside the container, so the process is killed where it runs.
            command = ["timeout", "-s", "KILL", str(int(limit)), *command]
        try:
            run = asyncio.to_thread(
                container.exec_run,
                command,
                workdir=workdir,
                environment=dict(request.environment or {}),
                demux=True,
            )
            if limit:
                exit_code, output = await asyncio.wait_for(
                    run, timeout=limit + HOST_TIMEOUT_GRACE_SECONDS
                )
            else:
                exit_code, output = await run
        except asyncio.TimeoutError:
            # The command ignored its limit; the session cannot be trusted.
            await self.terminate(session_id)
            return SandboxExecResult(
                exit_code=137,
                stderr="Command exceeded its time limit and the sandbox was stopped",
                timed_out=True,
                metadata={"session_terminated": True},
            )
        finally:
            if stdin_path:
                await asyncio.to_thread(container.exec_run, ["rm", "-f", stdin_path])
        stdout_bytes, stderr_bytes = output if output else (b"", b"")
        stdout, stdout_truncated = self._bounded(stdout_bytes or b"")
        stderr, stderr_truncated = self._bounded(stderr_bytes or b"")
        # `timeout -s KILL` exits 137 when it had to kill the command.
        timed_out = bool(limit) and exit_code == 137
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
        container = self._container(session_id)
        path = self._inside_workdir(session_id, path)
        directory, name = posixpath.split(path)
        await asyncio.to_thread(container.exec_run, ["mkdir", "-p", directory])
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            # Owned by the sandbox user, so its commands can change the file.
            info.uid, info.gid, info.mode = self.uid, self.gid, 0o644
            tar.addfile(info, io.BytesIO(content))
        archive.seek(0)
        if not await asyncio.to_thread(container.put_archive, directory, archive.getvalue()):
            raise OSError(f"Could not write {path} in the sandbox")

    async def read_file(self, session_id: str, path: str) -> bytes:
        container = self._container(session_id)
        path = self._inside_workdir(session_id, path)
        stream, _ = await asyncio.to_thread(container.get_archive, path)
        data = b"".join(stream)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                raise IsADirectoryError(path)
            return extracted.read()

    # --- network and snapshots ------------------------------------------------

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        container = self._container(session_id)
        mode = _network_mode(policy)
        client = await self._docker()
        await asyncio.to_thread(container.reload)
        attached = list((container.attrs.get("NetworkSettings") or {}).get("Networks") or {})
        for name in attached:
            if name != mode:
                await asyncio.to_thread(client.networks.get(name).disconnect, container)
        if mode == "bridge" and "bridge" not in attached:
            await asyncio.to_thread(client.networks.get("bridge").connect, container)

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        container = self._container(session_id)
        image = await asyncio.to_thread(
            container.commit, repository="omnicoreagent-snapshot", tag=session_id
        )
        return SandboxSnapshot(
            snapshot_id=image.id, session_id=session_id, metadata={"image": image.id}
        )

    # --- helpers --------------------------------------------------------------

    async def _docker(self):
        if self._client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise SandboxUnsupportedError(
                    "The Docker sandbox needs the Docker SDK: pip install omnicoreagent[docker]"
                ) from exc
            self._client = await asyncio.to_thread(docker.from_env)
        return self._client

    def _ensure_image(self, client, image: str) -> None:
        from docker.errors import ImageNotFound

        try:
            client.images.get(image)
        except ImageNotFound:
            if self.pull == "never":
                raise SandboxUnsupportedError(
                    f"Sandbox image {image} is not present and pulling is disabled"
                ) from None
            client.images.pull(image)

    def _container(self, session_id: str):
        container = self._containers.get(session_id)
        if container is None:
            raise SandboxUnsupportedError(f"Sandbox session {session_id} is not open")
        return container

    def _inside_workdir(self, session_id: str, path: str) -> str:
        workdir = self._sessions[session_id].manifest.working_dir
        resolved = posixpath.normpath(posixpath.join(workdir, path))
        if resolved != workdir and not resolved.startswith(workdir.rstrip("/") + "/"):
            raise PermissionError(f"Path {path} is outside the sandbox working directory")
        return resolved

    def _bounded(self, data: bytes) -> tuple[str, bool]:
        if len(data) <= self.max_output_bytes:
            return data.decode(errors="replace"), False
        return data[: self.max_output_bytes].decode(errors="ignore"), True


def _numeric_user(user: str) -> tuple[int, int]:
    uid, _, gid = user.partition(":")
    if not uid.isdigit() or (gid and not gid.isdigit()):
        raise ValueError(
            "docker sandbox option 'user' must be numeric ('uid' or 'uid:gid') so "
            "the working directory can belong to it"
        )
    return int(uid), int(gid or uid)


# Mounting any of these (or a directory containing one) hands over the host.
_ENGINE_SOCKETS = {"docker.sock", "containerd.sock", "podman.sock", "crio.sock"}
_SYSTEM_DIRS = (
    "/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/run", "/var/run",
    "/var/lib/docker", "/var/lib/containerd", "/usr", "/bin", "/sbin",
    "/lib", "/lib64", "/snap",
)
_CREDENTIAL_NAMES = {
    ".ssh", ".aws", ".azure", ".docker", ".kube", ".gnupg", ".config",
    ".netrc", ".git-credentials", ".pypirc", ".npmrc", ".modal.toml",
}


def _refuse_sensitive_mount(source: str) -> None:
    if "://" in source:
        raise SandboxUnsupportedError(
            f"{source} must not be mounted: the Docker sandbox mounts local paths only"
        )
    real = os.path.realpath(source)
    home = os.path.realpath(os.path.expanduser("~"))
    sensitive = [*_SYSTEM_DIRS, *(os.path.join(home, name) for name in _CREDENTIAL_NAMES)]
    parts = set(real.split("/"))
    reason = None
    if real == "/":
        reason = "it is the host root"
    elif os.path.basename(real) in _ENGINE_SOCKETS:
        reason = "it is a container engine socket"
    elif parts & _CREDENTIAL_NAMES:
        reason = "it is or is inside a credential directory"
    else:
        for path in sensitive:
            if real == path or real.startswith(path + "/"):
                reason = f"it is inside {path}"
                break
            if path.startswith(real + "/"):
                reason = f"it contains {path}"
                break
    if reason:
        raise SandboxUnsupportedError(f"{source} must not be mounted into a sandbox: {reason}")


def _network_mode(policy: NetworkPolicy) -> str:
    default = getattr(policy.default, "value", policy.default)
    if policy.allowed_hosts:
        # Docker cannot restrict traffic to named hosts; refusing is the only
        # safe answer until an egress proxy enforces allowlists.
        raise SandboxUnsupportedError(
            "The Docker sandbox cannot enforce a network host allowlist; "
            "use network default 'deny' or 'allow'"
        )
    return "bridge" if default == SandboxNetworkDefault.ALLOW.value else "none"


def _bytes(size: str) -> int:
    text = str(size).strip().lower().rstrip("b")
    if text and text[-1] in _UNITS:
        return int(float(text[:-1]) * _UNITS[text[-1]])
    return int(text)
