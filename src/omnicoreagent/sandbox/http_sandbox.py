"""HTTP sandbox backend: bring your own sandbox service.

For a sandbox this library has no native adapter for — a Cloudflare Worker
using Cloudflare's Sandbox SDK, a function on your own infrastructure, a
company sandbox service — run a small service that speaks this contract and
point the agent at it. Governance, telemetry, credentials, and the workspace
stay here; the service only starts sandboxes and runs commands in them.

The contract (every request carries the configured bearer token, if any):

- ``POST   {base}/sessions``                  ``{"manifest": {...}}`` -> ``{"session_id": "..."}``
- ``POST   {base}/sessions/{id}/exec``        ``{"command": [...], "cwd": str|null,
  "env": {...}, "timeout_seconds": int|null, "stdin": str|null}`` ->
  ``{"exit_code": int, "stdout": str, "stderr": str, "timed_out": bool}``
- ``PUT    {base}/sessions/{id}/files?path=`` raw bytes -> 204
- ``GET    {base}/sessions/{id}/files?path=`` -> raw bytes
- ``DELETE {base}/sessions/{id}``             -> 204

The service is responsible for the isolation it promises: the manifest states
the image, working directory, environment, resources, and network policy, and
a service that cannot honour the network policy must refuse the session.
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
    SandboxSession,
    SandboxSnapshot,
)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000


class HttpSandboxRuntime(SandboxRuntime):
    provider = "http"
    supports_required_sandbox = True
    supports_execution = True

    def __init__(self, *, options: dict[str, Any] | None = None, telemetry_recorder=None):
        options = dict(options or {})
        self.base_url = str(options.pop("base_url", "")).rstrip("/")
        if not self.base_url:
            raise ValueError("The http sandbox needs a 'base_url' option")
        self.token = options.pop("token", None)
        self.headers = dict(options.pop("headers", {}) or {})
        self.request_timeout = float(options.pop("request_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_output_bytes = int(options.pop("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
        self.verify = bool(options.pop("verify_tls", True))
        if options:
            raise ValueError(f"Unknown http sandbox option(s): {', '.join(sorted(options))}")
        self.telemetry_recorder = telemetry_recorder
        self._client: Any = None
        self._sessions: dict[str, SandboxSession] = {}
        self._remote_ids: dict[str, str] = {}

    async def create(self, manifest: SandboxManifest) -> SandboxSession:
        manifest.provider = self.provider
        session_id = manifest.sandbox_id or f"sandbox_{uuid4().hex}"
        payload = await self._request(
            "POST", "/sessions", json={"manifest": _manifest_payload(manifest)}
        )
        remote_id = str(payload.get("session_id") or session_id)
        session = SandboxSession(
            session_id=session_id,
            provider=self.provider,
            manifest=manifest,
            metadata={"remote_session_id": remote_id, "base_url": self.base_url},
        )
        self._sessions[session_id] = session
        self._remote_ids[session_id] = remote_id
        return session

    async def terminate(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        remote_id = self._remote_ids.pop(session_id, None)
        if remote_id is None:
            return
        await self._request("DELETE", f"/sessions/{remote_id}", expect_json=False)
        if self._client is not None and not self._remote_ids:
            await self._client.aclose()
            self._client = None

    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult:
        session = self._sessions[session_id]
        payload = await self._request(
            "POST",
            f"/sessions/{self._remote(session_id)}/exec",
            json={
                "command": list(request.command),
                "cwd": request.cwd or session.manifest.working_dir,
                "env": {str(k): str(v) for k, v in (request.environment or {}).items()},
                "timeout_seconds": request.timeout_seconds,
                "stdin": request.stdin,
            },
            timeout=self._deadline(request.timeout_seconds),
        )
        stdout, stdout_truncated = self._bounded(payload.get("stdout"))
        stderr, stderr_truncated = self._bounded(payload.get("stderr"))
        return SandboxExecResult(
            exit_code=int(payload.get("exit_code", 1)),
            stdout=stdout,
            stderr=stderr,
            timed_out=bool(payload.get("timed_out")),
            metadata={
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        )

    async def write_file(self, session_id: str, path: str, content: bytes) -> None:
        await self._request(
            "PUT",
            f"/sessions/{self._remote(session_id)}/files",
            params={"path": self._inside_workdir(session_id, path)},
            content=content,
            expect_json=False,
        )

    async def read_file(self, session_id: str, path: str) -> bytes:
        return await self._request(
            "GET",
            f"/sessions/{self._remote(session_id)}/files",
            params={"path": self._inside_workdir(session_id, path)},
            expect_bytes=True,
        )

    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None:
        raise SandboxUnsupportedError(
            "An HTTP sandbox service sets the network policy when the session "
            "starts; put it in the manifest"
        )

    async def snapshot(self, session_id: str) -> SandboxSnapshot | None:
        return None

    # --- helpers --------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content: bytes | None = None,
        timeout: float | None = None,
        expect_json: bool = True,
        expect_bytes: bool = False,
    ) -> Any:
        import httpx

        if self._client is None:
            headers = dict(self.headers)
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.request_timeout,
                verify=self.verify,
            )
        try:
            response = await self._client.request(
                method, path, json=json, params=params, content=content, timeout=timeout
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SandboxUnsupportedError(
                f"The sandbox service answered {exc.response.status_code} for {method} {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SandboxUnsupportedError(
                f"The sandbox service could not be reached: {type(exc).__name__}"
            ) from exc
        if expect_bytes:
            return response.content
        if not expect_json or not response.content:
            return {}
        return response.json()

    def _deadline(self, timeout_seconds: int | None) -> float:
        # The service enforces the command's limit; the request waits a little longer.
        if not timeout_seconds:
            return self.request_timeout
        return max(self.request_timeout, float(timeout_seconds) + 10.0)

    def _remote(self, session_id: str) -> str:
        remote_id = self._remote_ids.get(session_id)
        if remote_id is None:
            raise SandboxUnsupportedError(f"Sandbox session {session_id} is not open")
        return remote_id

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


def _manifest_payload(manifest: SandboxManifest) -> dict[str, Any]:
    """What the service needs to start the session.

    Only the plain environment is sent: secret references are resolved by the
    harness and never leave it.
    """
    policy = manifest.network_policy
    resources = manifest.resources
    return {
        "image": manifest.image,
        "working_dir": manifest.working_dir,
        "environment": dict(manifest.environment.plain),
        "network": {
            "default": getattr(policy.default, "value", policy.default),
            "allowed_hosts": list(policy.allowed_hosts),
            "denied_hosts": list(policy.denied_hosts),
        },
        "resources": {"cpu": resources.cpu, "memory": resources.memory, "gpu": resources.gpu},
    }
