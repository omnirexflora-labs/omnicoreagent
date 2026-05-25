from __future__ import annotations

from datetime import timedelta

from omnicoreagent.core.telemetry import TelemetryRecorder
from omnicoreagent.governance.approvals import ApprovalResolver
from omnicoreagent.governance.errors import (
    ApprovalExpiredError,
    ApprovalRequiredError,
    BudgetExceededError,
    PolicyDeniedError,
    PolicyEvaluationError,
    SandboxRequiredError,
    UnknownCapabilityError,
)
from omnicoreagent.governance.evaluator import PolicyEvaluator
from omnicoreagent.governance.hashing import attach_policy_hash
from omnicoreagent.governance.models import (
    ApprovalRequest,
    AuthorityRequest,
    PolicyDecision,
    PolicyEffect,
    PolicyEnvelope,
    ReasonCode,
    utc_now,
)
from omnicoreagent.governance.telemetry import (
    emit_policy_decision,
    emit_policy_request,
)


class GovernanceEngine:
    def __init__(
        self,
        policy: PolicyEnvelope,
        *,
        evaluator: PolicyEvaluator | None = None,
        approval_resolver: ApprovalResolver | None = None,
        telemetry_recorder: TelemetryRecorder | None = None,
        allow_static_high_risk_approvals: bool = False,
    ) -> None:
        self.policy = policy
        if not self.policy.provenance.policy_hash:
            attach_policy_hash(self.policy)
        self.evaluator = evaluator or PolicyEvaluator()
        self.approval_resolver = approval_resolver
        self.telemetry_recorder = telemetry_recorder
        self.allow_static_high_risk_approvals = allow_static_high_risk_approvals

    async def evaluate(self, request: AuthorityRequest) -> PolicyDecision:
        await emit_policy_request(self.telemetry_recorder, request)
        try:
            decision = self.evaluator.evaluate(self.policy, request)
        except Exception as exc:  # noqa: BLE001 - governance must fail closed.
            decision = PolicyDecision(
                effect=PolicyEffect.DENY,
                request_id=request.request_id,
                policy_id=self.policy.policy_id,
                policy_hash=self.policy.provenance.policy_hash,
                reason_code=ReasonCode.POLICY_ERROR,
                reason=str(exc) or "Policy evaluation failed closed.",
            )
            await emit_policy_decision(self.telemetry_recorder, decision)
            if isinstance(exc, PolicyEvaluationError):
                raise
            raise PolicyEvaluationError(
                decision.reason,
                metadata=_decision_metadata(decision),
            ) from exc
        await emit_policy_decision(
            self.telemetry_recorder,
            decision,
            strict=decision.constraints.strict_telemetry,
        )
        return decision

    async def authorize(self, request: AuthorityRequest) -> PolicyDecision:
        decision = await self.evaluate(request)
        if decision.effect == PolicyEffect.DENY:
            if decision.reason_code == ReasonCode.BUDGET_EXCEEDED:
                raise BudgetExceededError(
                    decision.reason,
                    metadata=_decision_metadata(decision),
                )
            if decision.reason_code == ReasonCode.UNKNOWN_CAPABILITY:
                raise UnknownCapabilityError(
                    decision.reason,
                    metadata=_decision_metadata(decision),
                )
            raise PolicyDeniedError(decision.reason, metadata=_decision_metadata(decision))
        if decision.effect == PolicyEffect.ASK:
            decision = await self._resolve_approval(request, decision)
        if decision.constraints.sandbox_required:
            raise SandboxRequiredError(
                "Policy requires routing through the sandbox execution boundary.",
                metadata=_decision_metadata(
                    decision,
                    reason_code=ReasonCode.SANDBOX_REQUIRED,
                ),
            )
        self._consume_budget(request)
        return decision

    async def _resolve_approval(
        self,
        request: AuthorityRequest,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        approval = ApprovalRequest(
            request_id=request.request_id,
            decision_id=decision.decision_id,
            capability=request.capability,
            actor=request.actor,
            reason=decision.reason,
            expires_at=(
                utc_now() + timedelta(seconds=decision.constraints.approval_expires_seconds)
                if decision.constraints.approval_expires_seconds is not None
                else None
            ),
        )
        if self.approval_resolver is None:
            decision.approval_id = approval.approval_id
            raise ApprovalRequiredError(
                decision.reason or "Approval required.",
                metadata=_decision_metadata(
                    decision,
                    reason_code=ReasonCode.APPROVAL_REQUIRED,
                ),
            )
        if (
            request.risk_level in {"high", "critical"}
            and getattr(self.approval_resolver, "is_static", False)
            and not self.allow_static_high_risk_approvals
        ):
            decision.approval_id = approval.approval_id
            raise ApprovalRequiredError(
                "Static approvals are not allowed for high-risk requests.",
                metadata=_decision_metadata(
                    decision,
                    reason_code=ReasonCode.APPROVAL_REQUIRED,
                ),
            )
        result = await self.approval_resolver.resolve(approval)
        if result is None or not result.approved:
            decision.approval_id = approval.approval_id
            raise ApprovalRequiredError(
                result.reason if result is not None and result.reason else decision.reason,
                metadata=_decision_metadata(
                    decision,
                    reason_code=ReasonCode.APPROVAL_REQUIRED,
                ),
            )
        if approval.expires_at is not None and result.resolved_at > approval.expires_at:
            decision.approval_id = approval.approval_id
            raise ApprovalExpiredError(
                "Approval expired before it was resolved.",
                metadata=_decision_metadata(
                    decision,
                    reason_code=ReasonCode.EXPIRED_POLICY,
                ),
            )
        decision.effect = PolicyEffect.ALLOW
        decision.approval_id = result.approval_id
        decision.reason_code = ReasonCode.MATCHED_ALLOW
        decision.reason = result.reason or "Approved by resolver."
        await emit_policy_decision(
            self.telemetry_recorder,
            decision,
            strict=decision.constraints.strict_telemetry,
        )
        return decision

    def _consume_budget(self, request: AuthorityRequest) -> None:
        budget = self.policy.budget
        if budget is None:
            return
        budget.used_requests += 1
        budget.used_cost += request.budget_cost


def _decision_metadata(
    decision: PolicyDecision,
    *,
    reason_code: ReasonCode | None = None,
) -> dict[str, object]:
    return {
        "request_id": decision.request_id,
        "decision_id": decision.decision_id,
        "policy_id": decision.policy_id,
        "policy_hash": decision.policy_hash,
        "reason_code": (reason_code or decision.reason_code).value,
        "matched_rule_ids": list(decision.matched_rule_ids),
    }
