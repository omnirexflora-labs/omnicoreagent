from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
import time
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from omnicoreagent.governance.capabilities import secret_authority_request
from omnicoreagent.governance.models import AuthorityRequest, AuthorityTarget
from omnicoreagent.sandbox.base import SandboxRuntime
from omnicoreagent.sandbox.errors import SandboxUnsupportedError
from omnicoreagent.sandbox.models import (
    SandboxSession,
    SandboxFilesystemDefault,
    SandboxAuthorityContext,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxEnvironment,
    SandboxLifecycleCleanup,
    SandboxManifest,
    SandboxNetworkDefault,
    WorkspaceMountMode,
)

_ALLOWED_AUTHORITY_METADATA_KEYS = frozenset(
    {"request_id", "operation", "purpose", "caller", "trace_id", "run_id"}
)


@dataclass(slots=True)
class SandboxCommandSpec:
    command: list[str] | tuple[str, ...]
    manifest: SandboxManifest | dict[str, Any] | None = None
    authority_request: AuthorityRequest | None = None
    cwd: str | None = None
    stdin: str | None = None
    timeout_seconds: int | None = None
    environment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.command, str) or not isinstance(self.command, list | tuple):
            raise ValueError("sandbox command must be a list of argv strings")
        if not self.command:
            raise ValueError("sandbox command is required")
        if not all(isinstance(item, str) for item in self.command):
            raise ValueError("sandbox command argv items must be strings")
        self.command = list(self.command)
        if any(not item.strip() for item in self.command):
            raise ValueError("sandbox command argv items must be non-empty")
        if any(_has_control_character(item) for item in self.command):
            raise ValueError("sandbox command argv items must not contain control characters")
        if self.cwd is not None:
            self.cwd = _normalize_sandbox_path(self.cwd)
        if self.timeout_seconds is not None:
            if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
                raise ValueError("sandbox timeout_seconds must be a positive integer")
        self.environment = SandboxEnvironment(plain=self.environment).plain
        if isinstance(self.manifest, dict):
            self.manifest = SandboxManifest(**self.manifest)


class SandboxExecutionService:
    """Explicit governed route for sandbox execution.

    The service keeps the harness outside the sandbox: policy is evaluated in the
    host runtime, then execution is routed through the configured sandbox adapter.
    Existing tool paths do not call this service implicitly.
    """

    def __init__(self, governance_engine: Any):
        self.governance_engine = governance_engine
        self._open_sessions: set[str] = set()
        # Session ID -> (monotonic start, commands run), for the close record.
        self._session_started: dict[str, tuple[float, int]] = {}

    async def open_session(
        self, manifest: SandboxManifest | dict[str, Any] | None = None
    ) -> SandboxSession:
        """Authorize a manifest's scope once and create a session to reuse.

        Each command in the session is still authorized on its own
        (``process.exec``) by ``execute(..., session=...)``.
        """
        if isinstance(manifest, dict):
            manifest = SandboxManifest(**manifest)
        manifest = manifest or SandboxManifest()
        runtime = self._runtime()
        requests = _manifest_authority_requests(manifest, SandboxCommandSpec(command=["session"]))
        if requests:
            await self.governance_engine.authorize_all(requests)
        session = await runtime.create(manifest)
        self._open_sessions.add(session.session_id)
        self._session_started[session.session_id] = (time.monotonic(), 0)
        await self._emit(
            "sandbox_session_created",
            metadata={
                **_session_facts(session, runtime),
                "image": session.metadata.get("image") or manifest.image,
                "network": _value(manifest.network_policy.default),
                "working_dir": manifest.working_dir,
            },
        )
        return session

    async def close_session(self, session: SandboxSession) -> None:
        if session.session_id not in self._open_sessions:
            return
        self._open_sessions.discard(session.session_id)
        started, commands = self._session_started.pop(session.session_id, (None, 0))
        runtime = self._runtime()
        error: BaseException | None = None
        try:
            await runtime.terminate(session.session_id)
        except Exception as exc:
            error = exc
            raise
        finally:
            await self._emit(
                "sandbox_session_closed",
                metadata={
                    **_session_facts(session, runtime),
                    "commands": commands,
                    "duration_ms": _elapsed_ms(started) if started is not None else None,
                },
                error=error,
            )

    async def execute(
        self,
        spec: SandboxCommandSpec | dict[str, Any],
        *,
        session: SandboxSession | None = None,
    ) -> SandboxExecResult:
        if isinstance(spec, dict):
            spec = SandboxCommandSpec(**spec)
        if session is not None:
            return await self._execute_in_session(spec, session)
        runtime = self._runtime()
        authority_request = _sandbox_authority_request(spec)
        manifest = spec.manifest or SandboxManifest()
        manifest_requests = _manifest_authority_requests(manifest, spec)
        if manifest_requests:
            await self.governance_engine.authorize_all(manifest_requests)
        decision = await self.governance_engine.authorize_sandboxed(authority_request)
        authority = SandboxAuthorityContext.from_policy_decision(decision)
        session = await runtime.create(manifest)
        try:
            result = await self._run_recorded(runtime, session, spec, authority)
        except Exception as exc:
            if _should_cleanup(manifest, None):
                await _terminate_preserving_original(runtime, session.session_id, exc)
            raise
        result.metadata = {
            **dict(result.metadata),
            "sandbox_session_id": session.session_id,
            "sandbox_provider": getattr(runtime, "provider", session.provider),
            "authority": authority.to_metadata(),
        }
        if _should_cleanup(manifest, result):
            await runtime.terminate(session.session_id)
        return result

    async def _execute_in_session(
        self, spec: SandboxCommandSpec, session: SandboxSession
    ) -> SandboxExecResult:
        if session.session_id not in self._open_sessions:
            raise SandboxUnsupportedError(
                f"Sandbox session {session.session_id} is closed or was not opened here"
            )
        if spec.manifest is not None:
            raise ValueError("A command in an open session uses the session's manifest")
        runtime = self._runtime()
        decision = await self.governance_engine.authorize_sandboxed(
            _sandbox_authority_request(spec)
        )
        authority = SandboxAuthorityContext.from_policy_decision(decision)
        started, commands = self._session_started.get(session.session_id, (time.monotonic(), 0))
        self._session_started[session.session_id] = (started, commands + 1)
        result = await self._run_recorded(runtime, session, spec, authority)
        result.metadata = {
            **dict(result.metadata),
            "sandbox_session_id": session.session_id,
            "sandbox_provider": getattr(runtime, "provider", session.provider),
            "authority": authority.to_metadata(),
        }
        return result

    async def _run_recorded(
        self,
        runtime: SandboxRuntime,
        session: SandboxSession,
        spec: SandboxCommandSpec,
        authority: SandboxAuthorityContext,
    ) -> SandboxExecResult:
        """Run one command, recording its start and its outcome.

        Facts are metadata (kept under every capture policy); the command and
        its output are payloads (subject to the capture policy).
        """
        facts = {
            **_session_facts(session, runtime),
            "execution_id": f"exec_{uuid4().hex}",
            "command_name": spec.command[0],
            "argc": len(spec.command),
            "timeout_seconds": spec.timeout_seconds,
            "purpose": spec.metadata.get("purpose", "command"),
            "decision_id": authority.decision_id,
            "matched_rule_ids": list(authority.matched_rule_ids),
        }
        await self._emit("sandbox_exec_started", metadata=facts)
        started = time.monotonic()
        try:
            result = await runtime.execute(
                session.session_id,
                SandboxExecRequest(
                    command=spec.command,
                    authority=authority,
                    cwd=spec.cwd,
                    stdin=spec.stdin,
                    timeout_seconds=spec.timeout_seconds,
                    environment={str(key): str(value) for key, value in spec.environment.items()},
                    metadata=dict(spec.metadata),
                ),
            )
        except BaseException as exc:
            await self._emit(
                "sandbox_exec_failed",
                input={"command": list(spec.command)},
                metadata={**facts, "duration_ms": _elapsed_ms(started)},
                error=exc,
                duration_ms=_elapsed_ms(started),
            )
            raise
        await self._emit(
            "sandbox_exec_completed" if result.ok else "sandbox_exec_failed",
            input={"command": list(spec.command)},
            output={"stdout": result.stdout, "stderr": result.stderr},
            metadata={
                **facts,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": _elapsed_ms(started),
                "stdout_bytes": len(result.stdout.encode("utf-8", errors="replace")),
                "stderr_bytes": len(result.stderr.encode("utf-8", errors="replace")),
                "stdout_truncated": bool(result.metadata.get("stdout_truncated")),
                "stderr_truncated": bool(result.metadata.get("stderr_truncated")),
                "session_terminated": bool(result.metadata.get("session_terminated")),
            },
            duration_ms=_elapsed_ms(started),
        )
        return result

    async def _emit(self, event_type: str, **fields: Any) -> None:
        from omnicoreagent.sandbox.telemetry import emit_sandbox_event

        recorder = getattr(self.governance_engine, "telemetry_recorder", None)
        if fields.get("duration_ms") is not None:
            fields["duration_ms"] = int(fields["duration_ms"])
        await emit_sandbox_event(recorder, event_type, **fields)

    def _runtime(self) -> SandboxRuntime:
        runtime = getattr(self.governance_engine, "sandbox_runtime", None)
        if not isinstance(runtime, SandboxRuntime):
            raise SandboxUnsupportedError(
                "Governed sandbox execution requires a configured SandboxRuntime."
            )
        return runtime


def _session_facts(session: SandboxSession, runtime: SandboxRuntime) -> dict[str, Any]:
    return {
        "sandbox_session_id": session.session_id,
        "sandbox_provider": _value(getattr(runtime, "provider", session.provider)),
    }


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _default_authority_request(spec: SandboxCommandSpec) -> AuthorityRequest:
    command_name = spec.command[0] if spec.command else ""
    return AuthorityRequest(
        capability="process.exec",
        provider="sandbox",
        execution_surface="sandbox",
        target=AuthorityTarget(resource=command_name),
        risk_level="high",
        metadata={
            "command": {"name": command_name, "argc": len(spec.command)},
            **_safe_metadata(spec.metadata),
        },
    )


def _sandbox_authority_request(spec: SandboxCommandSpec) -> AuthorityRequest:
    request = spec.authority_request or _default_authority_request(spec)
    command_name = spec.command[0] if spec.command else ""
    if request.capability != "process.exec":
        raise ValueError("sandbox command execution requires process.exec authority")
    if request.provider not in {None, "sandbox"}:
        raise ValueError("sandbox command authority provider must be sandbox")
    if request.execution_surface not in {None, "sandbox"}:
        raise ValueError("sandbox command authority execution_surface must be sandbox")
    if request.target and request.target.resource not in {None, command_name}:
        raise ValueError("sandbox command authority target must match command name")
    return AuthorityRequest(
        capability=request.capability,
        actor=request.actor,
        target=AuthorityTarget(resource=command_name),
        request_id=request.request_id,
        provider="sandbox",
        execution_surface="sandbox",
        risk_level="high",
        data_classes=list(request.data_classes),
        method=request.method,
        host=request.host,
        mcp_server=request.mcp_server,
        budget_cost=request.budget_cost,
        metadata={
            "command": {"name": command_name, "argc": len(spec.command)},
            **_safe_metadata(request.metadata),
        },
    )


def _manifest_authority_requests(
    manifest: SandboxManifest,
    spec: SandboxCommandSpec,
) -> list[AuthorityRequest]:
    requests: list[AuthorityRequest] = []
    actor = spec.authority_request.actor if spec.authority_request else "agent"
    if manifest.image:
        requests.append(
            _sandbox_scope_request(
                "sandbox.image.use",
                actor=actor,
                resource=manifest.image,
                risk_level="medium",
            )
        )
    if manifest.workspace_mount is not None:
        mode = manifest.workspace_mount.mode
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.mount",
                actor=actor,
                path=manifest.workspace_mount.target,
                resource=manifest.workspace_mount.source,
                risk_level=(
                    "high" if mode == WorkspaceMountMode.READ_WRITE else "medium"
                ),
                metadata={"mode": mode.value},
            )
        )
    if spec.cwd is not None:
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.cwd",
                actor=actor,
                path=spec.cwd,
                risk_level="medium",
            )
        )
    filesystem_policy = manifest.filesystem_policy
    if filesystem_policy.default == SandboxFilesystemDefault.ALLOW:
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.configure",
                actor=actor,
                risk_level=(
                    "high"
                    if filesystem_policy.default == SandboxFilesystemDefault.ALLOW
                    or filesystem_policy.writable_paths
                    else "medium"
                ),
                metadata={
                    "default": filesystem_policy.default.value,
                    "readable_paths": list(filesystem_policy.readable_paths),
                    "writable_paths": list(filesystem_policy.writable_paths),
                    "denied_paths": list(filesystem_policy.denied_paths),
                },
            )
        )
    for path in filesystem_policy.readable_paths:
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.configure",
                actor=actor,
                path=path,
                risk_level="medium",
                metadata={"mode": "read"},
            )
        )
    for path in filesystem_policy.writable_paths:
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.configure",
                actor=actor,
                path=path,
                risk_level="high",
                metadata={"mode": "write"},
            )
        )
    for path in filesystem_policy.denied_paths:
        requests.append(
            _sandbox_scope_request(
                "sandbox.filesystem.configure",
                actor=actor,
                path=path,
                risk_level="medium",
                metadata={"mode": "deny"},
            )
        )
    network_policy = manifest.network_policy
    if network_policy.default == SandboxNetworkDefault.ALLOW:
        requests.append(
            _sandbox_scope_request(
                "sandbox.network.configure",
                actor=actor,
                host="*" if network_policy.default == SandboxNetworkDefault.ALLOW else None,
                risk_level=(
                    "high"
                    if network_policy.default == SandboxNetworkDefault.ALLOW
                    else "medium"
                ),
                metadata={
                    "default": network_policy.default.value,
                    "allowed_hosts": list(network_policy.allowed_hosts),
                    "denied_hosts": list(network_policy.denied_hosts),
                },
            )
        )
    for host in network_policy.allowed_hosts:
        requests.append(
            _sandbox_scope_request(
                "sandbox.network.configure",
                actor=actor,
                host=host,
                risk_level="medium",
                metadata={"mode": "allow"},
            )
        )
    for host in network_policy.denied_hosts:
        requests.append(
            _sandbox_scope_request(
                "sandbox.network.configure",
                actor=actor,
                host=host,
                risk_level="medium",
                metadata={"mode": "deny"},
            )
        )
    if manifest.environment.plain or spec.environment:
        requests.append(
            _sandbox_scope_request(
                "sandbox.environment.set",
                actor=actor,
                risk_level="medium",
                metadata={
                    "plain_keys": sorted(
                        {str(key) for key in manifest.environment.plain}
                        | {str(key) for key in spec.environment}
                    )
                },
            )
        )
    for secret_ref in manifest.environment.secret_refs:
        requests.append(
            secret_authority_request(
                secret_ref=secret_ref,
                purpose="sandbox environment",
                actor=actor,
                brokered=True,
            )
        )
    if manifest.resources.cpu or manifest.resources.memory or manifest.resources.gpu:
        requests.append(
            _sandbox_scope_request(
                "sandbox.resources.set",
                actor=actor,
                risk_level="medium",
                metadata={
                    "cpu": manifest.resources.cpu,
                    "memory": manifest.resources.memory,
                    "gpu": manifest.resources.gpu,
                },
            )
        )
    return requests


def _sandbox_scope_request(
    capability: str,
    *,
    actor: str,
    path: str | None = None,
    resource: str | None = None,
    host: str | None = None,
    risk_level: str,
    metadata: dict[str, Any] | None = None,
) -> AuthorityRequest:
    return AuthorityRequest(
        capability=capability,
        actor=actor,
        provider="sandbox",
        execution_surface="sandbox",
        target=AuthorityTarget(path=path, resource=resource, host=host),
        risk_level=risk_level,
        host=host,
        metadata=metadata or {},
    )


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key = str(key)
        if key not in _ALLOWED_AUTHORITY_METADATA_KEYS:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


def _has_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _normalize_sandbox_path(path: str) -> str:
    raw = unquote(str(path or "").strip())
    if not raw:
        raise ValueError("sandbox cwd is required")
    if _has_control_character(raw):
        raise ValueError("sandbox cwd must not contain control characters")
    if not raw.startswith("/"):
        raise ValueError(f"sandbox cwd must be absolute: {path}")
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"sandbox cwd escapes allowed scope: {path}")
    return posixpath.normpath(raw.replace("\\", "/"))


async def _terminate_preserving_original(
    runtime: SandboxRuntime,
    session_id: str,
    original_error: BaseException,
) -> None:
    try:
        await runtime.terminate(session_id)
    except Exception as cleanup_error:  # noqa: BLE001 - cleanup must not hide root cause.
        original_error.add_note(
            f"Sandbox cleanup failed after original error: {cleanup_error}"
        )


def _should_cleanup(
    manifest: SandboxManifest,
    result: SandboxExecResult | None,
) -> bool:
    cleanup = manifest.lifecycle.cleanup
    if cleanup == SandboxLifecycleCleanup.NEVER:
        return False
    if cleanup == SandboxLifecycleCleanup.ON_SUCCESS:
        return result is not None and result.ok
    return True
