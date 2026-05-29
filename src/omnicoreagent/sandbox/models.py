from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import posixpath
import re
from typing import Any
from urllib.parse import unquote, urlparse


class SandboxProvider(str, Enum):
    NONE = "none"
    LOCAL_TEST = "local_test"


class SandboxNetworkDefault(str, Enum):
    DENY = "deny"
    ALLOW = "allow"


class SandboxFilesystemDefault(str, Enum):
    DENY = "deny"
    ALLOW = "allow"


class WorkspaceMountMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class SandboxLifecycleCleanup(str, Enum):
    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    NEVER = "never"


@dataclass(slots=True)
class WorkspaceMount:
    source: str
    target: str
    mode: WorkspaceMountMode | str = WorkspaceMountMode.READ_ONLY

    def __post_init__(self) -> None:
        self.source = _normalize_mount_source(self.source)
        self.target = _normalize_sandbox_path(self.target)
        self.mode = WorkspaceMountMode(self.mode)


@dataclass(slots=True)
class NetworkPolicy:
    default: SandboxNetworkDefault | str = SandboxNetworkDefault.DENY
    allowed_hosts: list[str] = field(default_factory=list)
    denied_hosts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.default = SandboxNetworkDefault(self.default)
        self.allowed_hosts = [
            _normalize_host_pattern(host) for host in self.allowed_hosts
        ]
        self.denied_hosts = [_normalize_host_pattern(host) for host in self.denied_hosts]


@dataclass(slots=True)
class SandboxFilesystemPolicy:
    default: SandboxFilesystemDefault | str = SandboxFilesystemDefault.DENY
    readable_paths: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.default = SandboxFilesystemDefault(self.default)
        self.readable_paths = [
            _normalize_sandbox_path(path) for path in self.readable_paths
        ]
        self.writable_paths = [
            _normalize_sandbox_path(path) for path in self.writable_paths
        ]
        self.denied_paths = [_normalize_sandbox_path(path) for path in self.denied_paths]


@dataclass(slots=True)
class SandboxResources:
    cpu: str | None = None
    memory: str | None = None
    timeout_seconds: int | None = None
    gpu: bool = False


@dataclass(slots=True)
class SandboxLifecycle:
    cleanup: SandboxLifecycleCleanup | str = SandboxLifecycleCleanup.ALWAYS
    snapshot: bool = False

    def __post_init__(self) -> None:
        self.cleanup = SandboxLifecycleCleanup(self.cleanup)


@dataclass(slots=True)
class SandboxEnvironment:
    plain: dict[str, str] = field(default_factory=dict)
    secret_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.plain = {
            _normalize_env_name(key): str(value) for key, value in self.plain.items()
        }
        self.secret_refs = [_normalize_secret_ref(ref) for ref in self.secret_refs]


@dataclass(slots=True)
class SandboxManifest:
    sandbox_id: str | None = None
    provider: SandboxProvider | str = SandboxProvider.NONE
    image: str | None = None
    working_dir: str = "/workspace"
    workspace_mount: WorkspaceMount | dict[str, Any] | None = None
    filesystem_policy: SandboxFilesystemPolicy | dict[str, Any] = field(
        default_factory=SandboxFilesystemPolicy
    )
    network_policy: NetworkPolicy | dict[str, Any] = field(default_factory=NetworkPolicy)
    environment: SandboxEnvironment | dict[str, Any] = field(
        default_factory=SandboxEnvironment
    )
    resources: SandboxResources | dict[str, Any] = field(default_factory=SandboxResources)
    lifecycle: SandboxLifecycle | dict[str, Any] = field(default_factory=SandboxLifecycle)

    def __post_init__(self) -> None:
        self.provider = SandboxProvider(self.provider)
        self.working_dir = _normalize_sandbox_path(self.working_dir)
        if isinstance(self.workspace_mount, dict):
            self.workspace_mount = WorkspaceMount(**self.workspace_mount)
        if isinstance(self.filesystem_policy, dict):
            self.filesystem_policy = SandboxFilesystemPolicy(**self.filesystem_policy)
        if isinstance(self.network_policy, dict):
            self.network_policy = NetworkPolicy(**self.network_policy)
        if isinstance(self.environment, dict):
            self.environment = SandboxEnvironment(**self.environment)
        if isinstance(self.resources, dict):
            self.resources = SandboxResources(**self.resources)
        if isinstance(self.lifecycle, dict):
            self.lifecycle = SandboxLifecycle(**self.lifecycle)


@dataclass(slots=True)
class SandboxSession:
    session_id: str
    provider: SandboxProvider | str
    manifest: SandboxManifest
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider = SandboxProvider(self.provider)


@dataclass(slots=True)
class SandboxAuthorityContext:
    authority_request_id: str
    decision_id: str
    policy_id: str | None = None
    policy_hash: str | None = None
    matched_rule_ids: list[str] = field(default_factory=list)
    reason_code: str | None = None

    @classmethod
    def from_policy_decision(cls, decision: Any) -> "SandboxAuthorityContext":
        return cls(
            authority_request_id=decision.request_id,
            decision_id=decision.decision_id,
            policy_id=decision.policy_id,
            policy_hash=decision.policy_hash,
            matched_rule_ids=list(decision.matched_rule_ids),
            reason_code=getattr(decision.reason_code, "value", decision.reason_code),
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "authority_request_id": self.authority_request_id,
            "decision_id": self.decision_id,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "matched_rule_ids": list(self.matched_rule_ids),
            "reason_code": self.reason_code,
        }


@dataclass(slots=True)
class SandboxExecRequest:
    command: list[str]
    authority: SandboxAuthorityContext
    cwd: str | None = None
    stdin: str | None = None
    timeout_seconds: int | None = None
    environment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SandboxExecResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_observation(self, *, max_output_chars: int = 4000) -> dict[str, Any]:
        stdout, stdout_truncated = _truncate_text(self.stdout, max_output_chars)
        stderr, stderr_truncated = _truncate_text(self.stderr, max_output_chars)
        return {
            "status": "success" if self.ok else "error",
            "exit_code": self.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": self.timed_out,
            "metadata": {
                **dict(self.metadata),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        }


@dataclass(slots=True)
class SandboxSnapshot:
    snapshot_id: str
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0:
        max_chars = 0
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_mount_source(path: str) -> str:
    raw = unquote(str(path or "").strip())
    if not raw:
        raise ValueError("workspace mount source is required")
    if "\x00" in raw:
        raise ValueError("workspace mount source contains a null byte")
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        if parsed.username or parsed.password:
            raise ValueError("workspace mount source must not contain credentials")
        normalized_path = _normalize_mount_source_path(parsed.path or "/")
        return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"
    if not raw.startswith("/"):
        raise ValueError(f"workspace mount source must be absolute or URI: {path}")
    return _normalize_mount_source_path(raw)


def _normalize_sandbox_path(path: str) -> str:
    raw = unquote(str(path or "").strip())
    if not raw:
        raise ValueError("sandbox path is required")
    if "\x00" in raw:
        raise ValueError("sandbox path contains a null byte")
    if not raw.startswith("/"):
        raise ValueError(f"sandbox path must be absolute: {path}")
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"sandbox path escapes allowed scope: {path}")
    return posixpath.normpath(raw.replace("\\", "/"))


def _normalize_mount_source_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"workspace mount source escapes allowed scope: {path}")
    return posixpath.normpath(raw.replace("\\", "/"))


def _normalize_host_pattern(host: str) -> str:
    raw = str(host or "").strip().lower()
    if not raw:
        raise ValueError("network host pattern is required")
    wildcard = raw.startswith("*.")
    host_value = raw[2:] if wildcard else raw
    if "/" in host_value or "\\" in host_value or "@" in host_value:
        raise ValueError(f"invalid network host pattern: {host}")
    parsed = urlparse(f"//{host_value}")
    if parsed.username or parsed.password:
        raise ValueError(f"invalid network host pattern: {host}")
    if parsed.port is not None:
        raise ValueError(f"network host pattern must not include a port: {host}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"invalid network host pattern: {host}")
    return f"*.{hostname.lower()}" if wildcard else hostname.lower()


def _normalize_env_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not _ENV_NAME_RE.match(normalized):
        raise ValueError(f"invalid sandbox environment variable name: {name}")
    return normalized


def _normalize_secret_ref(secret_ref: str) -> str:
    normalized = str(secret_ref or "").strip()
    if not normalized:
        raise ValueError("sandbox secret ref is required")
    if "\x00" in normalized or any(char.isspace() for char in normalized):
        raise ValueError("sandbox secret ref must not contain whitespace or null bytes")
    parts = [part for part in normalized.replace("\\", "/").split("/") if part]
    if ".." in parts:
        raise ValueError(f"sandbox secret ref escapes allowed scope: {secret_ref}")
    return normalized
