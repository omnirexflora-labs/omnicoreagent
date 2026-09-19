from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from numbers import Real
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


RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


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

    def __post_init__(self) -> None:
        self.sandbox_required = _strict_bool(
            self.sandbox_required, "sandbox_required"
        )
        self.audit_required = _strict_bool(self.audit_required, "audit_required")
        self.strict_telemetry = _strict_bool(
            self.strict_telemetry, "strict_telemetry"
        )
        if self.approval_expires_seconds is not None:
            self.approval_expires_seconds = _non_negative_int(
                self.approval_expires_seconds,
                "approval_expires_seconds",
                minimum=1,
            )
        if not isinstance(self.metadata, dict):
            raise ValueError("constraints.metadata must be a dict")


@dataclass
class PolicyRuleConditions:
    risk_level: list[str] | None = None
    data_classes: list[str] | None = None
    provider: str | None = None
    execution_surface: str | None = None
    # The rule does not match requests on these surfaces (for example, an ask
    # rule for process execution that should not apply inside a sandbox).
    exclude_execution_surface: list[str] | None = None
    mcp_server: str | None = None
    method: str | None = None
    host: str | None = None

    def __post_init__(self) -> None:
        if self.risk_level is not None:
            self.risk_level = _risk_levels(self.risk_level, "risk_level")
        if self.exclude_execution_surface is not None:
            self.exclude_execution_surface = _string_list(
                self.exclude_execution_surface, "exclude_execution_surface"
            )
        if self.data_classes is not None:
            self.data_classes = _string_list(self.data_classes, "data_classes")
        for name in ("provider", "execution_surface", "mcp_server", "method", "host"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _non_empty_string(value, name))


@dataclass
class TargetMatcher:
    path: str | None = None
    host: str | None = None
    resource: str | None = None
    tool_name: str | None = None
    mcp_server: str | None = None

    def __post_init__(self) -> None:
        for name in ("path", "host", "resource", "tool_name", "mcp_server"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _non_empty_string(value, name))


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
        self.rule_id = _non_empty_string(self.rule_id, "rule_id")
        self.effect = PolicyEffect(self.effect)
        self.capability = _non_empty_string(self.capability, "capability")
        if isinstance(self.target, dict):
            self.target = TargetMatcher(**self.target)
        elif self.target is not None and not isinstance(self.target, TargetMatcher):
            raise ValueError("rule.target must be a TargetMatcher or dict")
        if isinstance(self.conditions, dict):
            self.conditions = PolicyRuleConditions(**self.conditions)
        elif self.conditions is not None and not isinstance(
            self.conditions, PolicyRuleConditions
        ):
            raise ValueError("rule.conditions must be PolicyRuleConditions or dict")
        if isinstance(self.constraints, dict):
            self.constraints = PolicyConstraints(**self.constraints)
        elif not isinstance(self.constraints, PolicyConstraints):
            raise ValueError("rule.constraints must be PolicyConstraints or dict")
        if not isinstance(self.metadata, dict):
            raise ValueError("rule.metadata must be a dict")


@dataclass
class PolicyRuleSet:
    deny: list[PolicyRule] = field(default_factory=list)
    ask: list[PolicyRule] = field(default_factory=list)
    allow: list[PolicyRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        buckets = {
            PolicyEffect.DENY: self.deny,
            PolicyEffect.ASK: self.ask,
            PolicyEffect.ALLOW: self.allow,
        }
        seen: set[str] = set()
        for effect, rules in buckets.items():
            if not isinstance(rules, list):
                raise ValueError(f"{effect.value} rules must be a list")
            normalized: list[PolicyRule] = []
            for rule in rules:
                if isinstance(rule, dict):
                    rule = _rule_from_mapping(rule, effect)
                if not isinstance(rule, PolicyRule):
                    raise ValueError(f"{effect.value} rules must contain PolicyRule values")
                if rule.effect != effect:
                    raise ValueError(
                        f"Rule '{rule.rule_id}' declares effect '{rule.effect.value}' "
                        f"but is stored in the {effect.value} bucket"
                    )
                if rule.rule_id in seen:
                    raise ValueError(f"Duplicate policy rule_id: {rule.rule_id}")
                seen.add(rule.rule_id)
                normalized.append(rule)
            setattr(self, effect.value, normalized)

    def all_rules(self) -> list[PolicyRule]:
        return [*self.deny, *self.ask, *self.allow]


@dataclass
class PolicyBudget:
    max_requests: int | None = None
    max_cost: float | None = None
    used_requests: int = 0
    used_cost: float = 0.0
    count_failed_attempts: bool = True

    def __post_init__(self) -> None:
        if self.max_requests is not None:
            self.max_requests = _non_negative_int(
                self.max_requests, "max_requests", minimum=0
            )
        if self.max_cost is not None:
            self.max_cost = _finite_non_negative(self.max_cost, "max_cost")
        self.used_requests = _non_negative_int(
            self.used_requests, "used_requests", minimum=0
        )
        self.used_cost = _finite_non_negative(self.used_cost, "used_cost")
        self.count_failed_attempts = _strict_bool(
            self.count_failed_attempts, "count_failed_attempts"
        )


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
        self.version = _non_empty_string(self.version, "version")
        self.name = _non_empty_string(self.name, "name")
        self.mode = PolicyMode(self.mode)
        if self.profile is not None:
            self.profile = PolicyProfile(self.profile)
        if isinstance(self.rules, dict):
            self.rules = _rules_from_mapping(self.rules)
        elif not isinstance(self.rules, PolicyRuleSet):
            raise ValueError("rules must be a PolicyRuleSet or dict")
        if isinstance(self.provenance, dict):
            self.provenance = PolicyProvenance(**self.provenance)
        elif not isinstance(self.provenance, PolicyProvenance):
            raise ValueError("provenance must be a PolicyProvenance or dict")
        if isinstance(self.budget, dict):
            self.budget = PolicyBudget(**self.budget)
        elif self.budget is not None and not isinstance(self.budget, PolicyBudget):
            raise ValueError("budget must be a PolicyBudget or dict")
        self.policy_id = _non_empty_string(self.policy_id, "policy_id")
        self.policy_id_supplied = _strict_bool(
            self.policy_id_supplied, "policy_id_supplied"
        )
        if not isinstance(self.metadata, dict):
            raise ValueError("policy metadata must be a dict")


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
        self.capability = _non_empty_string(self.capability, "capability")
        self.provider = _non_empty_string(self.provider, "provider")
        self.execution_surface = _non_empty_string(
            self.execution_surface, "execution_surface"
        )
        self.descriptor_source = DescriptorSource(self.descriptor_source)
        self.descriptor_trust = DescriptorTrust(self.descriptor_trust)
        self.risk_level = _risk_level(self.risk_level, "risk_level")
        self.data_classes = _string_list(self.data_classes, "data_classes")
        if not isinstance(self.metadata, dict):
            raise ValueError("descriptor.metadata must be a dict")


@dataclass
class AuthorityTarget:
    path: str | None = None
    host: str | None = None
    resource: str | None = None
    tool_name: str | None = None
    mcp_server: str | None = None

    def __post_init__(self) -> None:
        for name in ("path", "host", "resource", "tool_name", "mcp_server"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _non_empty_string(value, name))


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
        self.capability = _non_empty_string(self.capability, "capability")
        self.actor = _non_empty_string(self.actor, "actor")
        if isinstance(self.target, dict):
            self.target = AuthorityTarget(**self.target)
        elif self.target is not None and not isinstance(self.target, AuthorityTarget):
            raise ValueError("request.target must be an AuthorityTarget or dict")
        self.risk_level = _risk_level(self.risk_level, "risk_level")
        self.data_classes = _string_list(self.data_classes, "data_classes")
        self.budget_cost = _finite_non_negative(self.budget_cost, "budget_cost")
        if not isinstance(self.metadata, dict):
            raise ValueError("request.metadata must be a dict")


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
        self.request_id = _non_empty_string(self.request_id, "request_id")
        self.decision_id = _non_empty_string(self.decision_id, "decision_id")
        self.capability = _non_empty_string(self.capability, "capability")
        self.actor = _non_empty_string(self.actor, "actor")
        self.approval_id = _non_empty_string(self.approval_id, "approval_id")
        if isinstance(self.target, dict):
            self.target = AuthorityTarget(**self.target)
        elif self.target is not None and not isinstance(self.target, AuthorityTarget):
            raise ValueError("approval.target must be an AuthorityTarget or dict")
        self.risk_level = _risk_level(self.risk_level, "risk_level")
        self.data_classes = _string_list(self.data_classes, "data_classes")
        if not isinstance(self.metadata, dict):
            raise ValueError("approval.metadata must be a dict")


@dataclass
class ApprovalResult:
    approved: bool
    approval_id: str
    resolved_by: str
    reason: str | None = None
    resolved_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.approved = _strict_bool(self.approved, "approved")
        self.approval_id = _non_empty_string(self.approval_id, "approval_id")
        self.resolved_by = _non_empty_string(self.resolved_by, "resolved_by")
        if not isinstance(self.resolved_at, datetime):
            raise ValueError("resolved_at must be a datetime")
        if not isinstance(self.metadata, dict):
            raise ValueError("approval result metadata must be a dict")


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
    declared_effect = payload.get("effect")
    if declared_effect is not None and PolicyEffect(declared_effect) != expected_effect:
        raise ValueError(
            f"Rule '{payload.get('rule_id', '<unknown>')}' declares effect "
            f"'{declared_effect}' but is stored in the {expected_effect.value} bucket"
        )
    payload["effect"] = expected_effect
    return PolicyRule(**payload)


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _strict_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _non_negative_int(value: Any, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < minimum:
        comparator = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{field_name} must be {comparator}")
    return value


def _finite_non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return [_non_empty_string(item, f"{field_name}[]") for item in value]


def _risk_level(value: Any, field_name: str) -> str:
    normalized = _non_empty_string(value, field_name).lower()
    if normalized not in RISK_LEVELS:
        allowed = ", ".join(sorted(RISK_LEVELS))
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return normalized


def _risk_levels(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_risk_level(item, f"{field_name}[]") for item in value]
