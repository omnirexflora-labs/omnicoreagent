from __future__ import annotations

from fnmatch import fnmatchcase

from omnicoreagent.governance.errors import PolicyEvaluationError
from omnicoreagent.governance.models import (
    AuthorityRequest,
    PolicyConstraints,
    PolicyDecision,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    ReasonCode,
)


class PolicyEvaluator:
    def evaluate(
        self,
        policy: PolicyEnvelope,
        request: AuthorityRequest,
    ) -> PolicyDecision:
        try:
            return self._evaluate(policy, request)
        except Exception as exc:  # noqa: BLE001 - policy failures must fail closed.
            if isinstance(exc, PolicyEvaluationError):
                raise
            raise PolicyEvaluationError(str(exc)) from exc

    def _evaluate(
        self,
        policy: PolicyEnvelope,
        request: AuthorityRequest,
    ) -> PolicyDecision:
        if policy.budget and policy.budget.max_requests is not None:
            if policy.budget.used_requests >= policy.budget.max_requests:
                return _decision(
                    policy,
                    request,
                    PolicyEffect.DENY,
                    ReasonCode.BUDGET_EXCEEDED,
                    "Policy request budget exceeded.",
                )
        if policy.budget and policy.budget.max_cost is not None:
            if policy.budget.used_cost + request.budget_cost > policy.budget.max_cost:
                return _decision(
                    policy,
                    request,
                    PolicyEffect.DENY,
                    ReasonCode.BUDGET_EXCEEDED,
                    "Policy cost budget exceeded.",
                )

        deny = _matching_rules(policy.rules.deny, request)
        if deny:
            return _matched_decision(
                policy,
                request,
                PolicyEffect.DENY,
                ReasonCode.MATCHED_DENY,
                deny,
            )

        ask = _matching_rules(policy.rules.ask, request)
        if ask:
            return _matched_decision(
                policy,
                request,
                PolicyEffect.ASK,
                ReasonCode.MATCHED_ASK,
                ask,
            )

        allow = _matching_rules(policy.rules.allow, request)
        if allow:
            return _matched_decision(
                policy,
                request,
                PolicyEffect.ALLOW,
                ReasonCode.MATCHED_ALLOW,
                allow,
            )

        if policy.mode == PolicyMode.PERMISSIVE:
            return _decision(
                policy,
                request,
                PolicyEffect.ALLOW,
                ReasonCode.MATCHED_ALLOW,
                "Permissive policy allowed unmatched capability.",
            )
        if policy.mode == PolicyMode.INTERACTIVE:
            return _decision(
                policy,
                request,
                PolicyEffect.ASK,
                ReasonCode.UNKNOWN_CAPABILITY,
                "Unknown capability requires approval.",
            )
        return _decision(
            policy,
            request,
            PolicyEffect.DENY,
            ReasonCode.UNKNOWN_CAPABILITY,
            "Unknown capability denied by strict policy.",
        )


def _matching_rules(rules: list[PolicyRule], request: AuthorityRequest) -> list[PolicyRule]:
    return [rule for rule in rules if _rule_matches(rule, request)]


def _rule_matches(rule: PolicyRule, request: AuthorityRequest) -> bool:
    if not fnmatchcase(request.capability, rule.capability):
        return False
    if rule.conditions and not _conditions_match(rule, request, rule.effect):
        return False
    if rule.target and not _target_matches(rule, request):
        return False
    return True


def _conditions_match(
    rule: PolicyRule,
    request: AuthorityRequest,
    effect: PolicyEffect,
) -> bool:
    conditions = rule.conditions
    if conditions is None:
        return True
    if conditions.risk_level and request.risk_level not in conditions.risk_level:
        return False
    if conditions.data_classes:
        request_classes = set(request.data_classes)
        condition_classes = set(conditions.data_classes)
        if effect == PolicyEffect.ALLOW and not request_classes.issubset(
            condition_classes
        ):
            return False
        if effect != PolicyEffect.ALLOW and not request_classes.intersection(
            condition_classes
        ):
            return False
    if conditions.provider and request.provider != conditions.provider:
        return False
    if conditions.execution_surface and request.execution_surface != conditions.execution_surface:
        return False
    if conditions.mcp_server and request.mcp_server != conditions.mcp_server:
        return False
    if conditions.method and (request.method or "").lower() != conditions.method.lower():
        return False
    request_host = request.host or (request.target.host if request.target else None)
    if conditions.host and not fnmatchcase(request_host or "", conditions.host):
        return False
    return True


def _target_matches(rule: PolicyRule, request: AuthorityRequest) -> bool:
    target = rule.target
    if target is None:
        return True
    request_target = request.target
    if target.path:
        if request_target is None or not fnmatchcase(request_target.path or "", target.path):
            return False
    if target.host:
        request_host = request.host or (request_target.host if request_target else None)
        if not fnmatchcase(request_host or "", target.host):
            return False
    if target.resource:
        if request_target is None or not fnmatchcase(request_target.resource or "", target.resource):
            return False
    if target.tool_name:
        if request_target is None or not fnmatchcase(request_target.tool_name or "", target.tool_name):
            return False
    if target.mcp_server:
        request_mcp_server = request.mcp_server or (
            request_target.mcp_server if request_target else None
        )
        if not fnmatchcase(request_mcp_server or "", target.mcp_server):
            return False
    return True


def _matched_decision(
    policy: PolicyEnvelope,
    request: AuthorityRequest,
    effect: PolicyEffect,
    reason_code: ReasonCode,
    rules: list[PolicyRule],
) -> PolicyDecision:
    constraints = _merge_constraints([rule.constraints for rule in rules])
    reason = next((rule.reason for rule in rules if rule.reason), "")
    if not reason:
        reason = f"Matched {effect.value} policy rule."
    return _decision(
        policy,
        request,
        effect,
        reason_code,
        reason,
        matched_rule_ids=[rule.rule_id for rule in rules],
        constraints=constraints,
    )


def _merge_constraints(constraints: list[PolicyConstraints]) -> PolicyConstraints:
    merged = PolicyConstraints()
    for item in constraints:
        merged.sandbox_required = merged.sandbox_required or item.sandbox_required
        merged.audit_required = merged.audit_required or item.audit_required
        merged.strict_telemetry = merged.strict_telemetry or item.strict_telemetry
        if item.approval_expires_seconds is not None:
            if merged.approval_expires_seconds is None:
                merged.approval_expires_seconds = item.approval_expires_seconds
            else:
                merged.approval_expires_seconds = min(
                    merged.approval_expires_seconds,
                    item.approval_expires_seconds,
                )
        merged.metadata.update(item.metadata)
    return merged


def _decision(
    policy: PolicyEnvelope,
    request: AuthorityRequest,
    effect: PolicyEffect,
    reason_code: ReasonCode,
    reason: str,
    *,
    matched_rule_ids: list[str] | None = None,
    constraints: PolicyConstraints | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        effect=effect,
        request_id=request.request_id,
        policy_id=policy.policy_id,
        policy_hash=policy.provenance.policy_hash,
        reason_code=reason_code,
        reason=reason,
        matched_rule_ids=matched_rule_ids or [],
        constraints=constraints or PolicyConstraints(),
    )
