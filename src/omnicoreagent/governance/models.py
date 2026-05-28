from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def governance_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PolicyMode(str, Enum):
    PERMISSIVE = "permissive"
    INTERACTIVE = "interactive"
    STRICT = "strict"


class PolicyProfile(str, Enum):
    PERMISSIVE_DEV = "permissive-dev"
    INTERACTIVE_DEV = "interactive-dev"
    STRICT_PRODUCTION = "strict-production"


class ReasonCode(str, Enum):
    MATCHED_ALLOW = "matched_allow"
    MATCHED_DENY = "matched_deny"
    MATCHED_ASK = "matched_ask"
    UNKNOWN_CAPABILITY = "unknown_capability"
    UNKNOWN_TARGET = "unknown_target"
    POLICY_ERROR = "policy_error"
    APPROVAL_REQUIRED = "approval_required"
    SANDBOX_REQUIRED = "sandbox_required"
    BUDGET_EXCEEDED = "budget_exceeded"
    EXPIRED_POLICY = "expired_policy"


class PolicySource(str, Enum):
    DEFAULT = "default"
    FILE = "file"
    CODE = "code"
    REMOTE = "remote"
    INHERITED = "inherited"


class DescriptorSource(str, Enum):
    BUILTIN = "builtin"
    APP_CODE = "app_code"
    MCP_SCHEMA = "mcp_schema"
    GENERATED = "generated"
    USER_CONFIG = "user_config"


class DescriptorTrust(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    INFERRED = "inferred"


@dataclass
class PolicyProvenance:
    source: PolicySource | str = PolicySource.DEFAULT
    source_ref: str | None = None
    created_by: str | None = None
    loaded_at: datetime = field(default_factory=utc_now)
    policy_hash: str = ""
    parent_policy_id: str | None = None

    def __post_init__(self) -> None:
        self.source = PolicySource(self.source)


@dataclass
class PolicyConstraints:
    sandbox_required: bool = False
    audit_required: bool = False
    strict_telemetry: bool = False
    approval_expires_seconds: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyRuleConditions:
    risk_level: list[str] | None = None
    data_classes: list[str] | None = None
    provider: str | None = None
    execution_surface: str | None = None
    mcp_server: str | None = None
    method: str | None = None
    host: str | None = None


@dataclass
class TargetMatcher:
    path: str | None = None
    host: str | None = None
    resource: str | None = None
    tool_name: str | None = None
    mcp_server: str | None = None


@dataclass
class PolicyRule:
    rule_id: str
    effect: PolicyEffect | str
    capability: str
    target: TargetMatcher | dict[str, Any] | None = None
    conditions: PolicyRuleConditions | dict[str, Any] | None = None
    constraints: PolicyConstraints | dict[str, Any] = field(default_factory=PolicyConstraints)
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.effect = PolicyEffect(self.effect)
        if isinstance(self.target, dict):
            self.target = TargetMatcher(**self.target)
        if isinstance(self.conditions, dict):
            self.conditions = PolicyRuleConditions(**self.conditions)
        if isinstance(self.constraints, dict):
            self.constraints = PolicyConstraints(**self.constraints)


@dataclass
class PolicyRuleSet:
    deny: list[PolicyRule] = field(default_factory=list)
    ask: list[PolicyRule] = field(default_factory=list)
    allow: list[PolicyRule] = field(default_factory=list)

    def all_rules(self) -> list[PolicyRule]:
        return [*self.deny, *self.ask, *self.allow]


@dataclass
class PolicyBudget:
    max_requests: int | None = None
    max_cost: float | None = None
    used_requests: int = 0
    used_cost: float = 0.0
    count_failed_attempts: bool = True


@dataclass
class PolicyEnvelope:
    version: str = "1"
    name: str = "default-policy"
    mode: PolicyMode | str = PolicyMode.INTERACTIVE
    rules: PolicyRuleSet | dict[str, Any] = field(default_factory=PolicyRuleSet)
    policy_id: str = field(default_factory=lambda: governance_id("policy"))
    profile: PolicyProfile | str | None = None
    provenance: PolicyProvenance | dict[str, Any] = field(default_factory=PolicyProvenance)
    budget: PolicyBudget | dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    policy_id_supplied: bool = False

    def __post_init__(self) -> None:
        self.mode = PolicyMode(self.mode)
        if self.profile is not None:
            self.profile = PolicyProfile(self.profile)
        if isinstance(self.rules, dict):
            self.rules = _rules_from_mapping(self.rules)
        if isinstance(self.provenance, dict):
            self.provenance = PolicyProvenance(**self.provenance)
        if isinstance(self.budget, dict):
            self.budget = PolicyBudget(**self.budget)


@dataclass
class CapabilityDescriptor:
    capability: str
    provider: str
    execution_surface: str
    descriptor_source: DescriptorSource | str = DescriptorSource.APP_CODE
    descriptor_trust: DescriptorTrust | str = DescriptorTrust.TRUSTED
    risk_level: str = "low"
    data_classes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.descriptor_source = DescriptorSource(self.descriptor_source)
        self.descriptor_trust = DescriptorTrust(self.descriptor_trust)


@dataclass
class AuthorityTarget:
    path: str | None = None
    host: str | None = None
    resource: str | None = None
    tool_name: str | None = None
    mcp_server: str | None = None


@dataclass
class AuthorityRequest:
    capability: str
    actor: str = "agent"
    target: AuthorityTarget | dict[str, Any] | None = None
    request_id: str = field(default_factory=lambda: governance_id("authreq"))
    provider: str | None = None
    execution_surface: str | None = None
    risk_level: str = "low"
    data_classes: list[str] = field(default_factory=list)
    method: str | None = None
    host: str | None = None
    mcp_server: str | None = None
    budget_cost: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.target, dict):
            self.target = AuthorityTarget(**self.target)


@dataclass
class PolicyDecision:
    effect: PolicyEffect | str
    request_id: str
    decision_id: str = field(default_factory=lambda: governance_id("decision"))
    policy_id: str | None = None
    policy_hash: str | None = None
    reason_code: ReasonCode | str = ReasonCode.POLICY_ERROR
    reason: str = ""
    matched_rule_ids: list[str] = field(default_factory=list)
    constraints: PolicyConstraints = field(default_factory=PolicyConstraints)
    approval_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.effect = PolicyEffect(self.effect)
        self.reason_code = ReasonCode(self.reason_code)
        if isinstance(self.constraints, dict):
            self.constraints = PolicyConstraints(**self.constraints)


@dataclass
class ApprovalRequest:
    request_id: str
    decision_id: str
    capability: str
    actor: str
    approval_id: str = field(default_factory=lambda: governance_id("approval"))
    target: AuthorityTarget | dict[str, Any] | None = None
    provider: str | None = None
    execution_surface: str | None = None
    risk_level: str = "low"
    data_classes: list[str] = field(default_factory=list)
    method: str | None = None
    host: str | None = None
    mcp_server: str | None = None
    reason: str = ""
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.target, dict):
            self.target = AuthorityTarget(**self.target)


@dataclass
class ApprovalResult:
    approved: bool
    approval_id: str
    resolved_by: str
    reason: str | None = None
    resolved_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if is_dataclass(value):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def _rules_from_mapping(data: dict[str, Any]) -> PolicyRuleSet:
    return PolicyRuleSet(
        deny=[_rule_from_mapping(item, PolicyEffect.DENY) for item in data.get("deny", [])],
        ask=[_rule_from_mapping(item, PolicyEffect.ASK) for item in data.get("ask", [])],
        allow=[_rule_from_mapping(item, PolicyEffect.ALLOW) for item in data.get("allow", [])],
    )


def _rule_from_mapping(data: dict[str, Any], expected_effect: PolicyEffect) -> PolicyRule:
    payload = dict(data)
    payload.setdefault("effect", expected_effect.value)
    return PolicyRule(**payload)
