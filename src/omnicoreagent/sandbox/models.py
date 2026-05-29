from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SandboxProvider(str, Enum):
    NONE = "none"
    LOCAL_TEST = "local_test"


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
        self.mode = WorkspaceMountMode(self.mode)


@dataclass(slots=True)
class NetworkPolicy:
    default: str = "deny"
    allowed_hosts: list[str] = field(default_factory=list)
    denied_hosts: list[str] = field(default_factory=list)


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


@dataclass(slots=True)
class SandboxManifest:
    sandbox_id: str | None = None
    provider: SandboxProvider | str = SandboxProvider.NONE
    image: str | None = None
    working_dir: str = "/workspace"
    workspace_mount: WorkspaceMount | None = None
    filesystem_policy: dict[str, Any] = field(default_factory=dict)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    environment: SandboxEnvironment = field(default_factory=SandboxEnvironment)
    resources: SandboxResources = field(default_factory=SandboxResources)
    lifecycle: SandboxLifecycle = field(default_factory=SandboxLifecycle)

    def __post_init__(self) -> None:
        self.provider = SandboxProvider(self.provider)
        if isinstance(self.workspace_mount, dict):
            self.workspace_mount = WorkspaceMount(**self.workspace_mount)
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
