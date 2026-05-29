from __future__ import annotations

import asyncio
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
    emit_policy_violation,
)
from omnicoreagent.sandbox.base import SandboxRuntime


class GovernanceEngine:
    def __init__(
        self,
        policy: PolicyEnvelope,
        *,
        evaluator: PolicyEvaluator | None = None,
        approval_resolver: ApprovalResolver | None = None,
        telemetry_recorder: TelemetryRecorder | None = None,
        sandbox_runtime=None,
        allow_test_sandbox_runtime: bool = False,
        allow_static_high_risk_approvals: bool = False,
    ) -> None:
        self.policy = policy
        if not self.policy.provenance.policy_hash:
            attach_policy_hash(self.policy)
        self.evaluator = evaluator or PolicyEvaluator()
        self.approval_resolver = approval_resolver
        self.telemetry_recorder = telemetry_recorder
        self.sandbox_runtime = sandbox_runtime
        self.allow_test_sandbox_runtime = allow_test_sandbox_runtime
        self.allow_static_high_risk_approvals = allow_static_high_risk_approvals
        self._budget_lock = asyncio.Lock()

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
        decisions = await self.authorize_all([request])
        return decisions[0]

    async def authorize_all(
        self,
        requests: list[AuthorityRequest],
    ) -> list[PolicyDecision]:
        return await self._authorize_all(requests, sandbox_route=False)

    async def authorize_sandboxed(self, request: AuthorityRequest) -> PolicyDecision:
        decisions = await self.authorize_all_sandboxed([request])
        return decisions[0]

    async def authorize_all_sandboxed(
        self,
        requests: list[AuthorityRequest],
    ) -> list[PolicyDecision]:
        return await self._authorize_all(requests, sandbox_route=True)

    async def _authorize_all(
        self,
        requests: list[AuthorityRequest],
        *,
        sandbox_route: bool,
    ) -> list[PolicyDecision]:
        if not requests:
            return []

        async with self._budget_lock:
            budget_decision = self._budget_exceeded_decision(requests)
            if budget_decision is not None:
                await emit_policy_decision(self.telemetry_recorder, budget_decision)
                raise BudgetExceededError(
                    budget_decision.reason,
                    metadata=_decision_metadata(budget_decision),
                )
            decisions = [await self.evaluate(request) for request in requests]
            self._raise_first_denied(decisions)
            for decision in decisions:
                await self._raise_if_sandbox_required_without_route(
                    decision, sandbox_route
                )

            ask_indexes = [
                index
                for index, decision in enumerate(decisions)
                if decision.effect == PolicyEffect.ASK
            ]
            if not ask_indexes:
                await self._consume_budget_or_raise_many(requests)
                return decisions

        for index in ask_indexes:
            decisions[index] = await self._resolve_approval(
                requests[index],
                decisions[index],
                emit_decision=False,
            )

        async with self._budget_lock:
            for decision in decisions:
                await self._raise_if_sandbox_required_without_route(
                    decision, sandbox_route
                )
            await self._consume_budget_or_raise_many(requests)
            for index in ask_indexes:
                await emit_policy_decision(
                    self.telemetry_recorder,
                    decisions[index],
                    strict=decisions[index].constraints.strict_telemetry,
                )
        return decisions

    async def _raise_if_sandbox_required_without_route(
        self,
        decision: PolicyDecision,
        sandbox_route: bool,
    ) -> None:
        if not decision.constraints.sandbox_required:
            return
        if sandbox_route and self._sandbox_runtime_satisfies_required_boundary():
            return
        metadata = _decision_metadata(
            decision,
            reason_code=ReasonCode.SANDBOX_REQUIRED,
        )
        metadata["sandbox_provider"] = getattr(self.sandbox_runtime, "provider", None)
        metadata["sandbox_route"] = sandbox_route
        await emit_policy_violation(
            self.telemetry_recorder,
            decision,
            reason_code=ReasonCode.SANDBOX_REQUIRED.value,
            metadata=metadata,
            strict=decision.constraints.strict_telemetry,
        )
        raise SandboxRequiredError(
            "Policy requires routing through the sandbox execution boundary.",
            metadata=metadata,
        )

    def _sandbox_runtime_satisfies_required_boundary(self) -> bool:
        if not isinstance(self.sandbox_runtime, SandboxRuntime):
            return False
        if not getattr(self.sandbox_runtime, "supports_required_sandbox", False):
            return False
        if getattr(self.sandbox_runtime, "is_test_adapter", False):
            return self.allow_test_sandbox_runtime
        return True

    def _raise_first_denied(self, decisions: list[PolicyDecision]) -> None:
        for decision in decisions:
            if decision.effect != PolicyEffect.DENY:
                continue
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

    async def _resolve_approval(
        self,
        request: AuthorityRequest,
        decision: PolicyDecision,
        *,
        emit_decision: bool = True,
    ) -> PolicyDecision:
        approval = ApprovalRequest(
            request_id=request.request_id,
            decision_id=decision.decision_id,
            capability=request.capability,
            actor=request.actor,
            target=request.target,
            provider=request.provider,
            execution_surface=request.execution_surface,
            risk_level=request.risk_level,
            data_classes=list(request.data_classes),
            method=request.method,
            host=request.host,
            mcp_server=request.mcp_server,
            reason=decision.reason,
            expires_at=(
                utc_now() + timedelta(seconds=decision.constraints.approval_expires_seconds)
                if decision.constraints.approval_expires_seconds is not None
                else None
            ),
            metadata=dict(request.metadata),
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
        if emit_decision:
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

    async def _consume_budget_or_raise(self, request: AuthorityRequest) -> None:
        await self._consume_budget_or_raise_many([request])

    async def _consume_budget_or_raise_many(
        self,
        requests: list[AuthorityRequest],
    ) -> None:
        decision = self._budget_exceeded_decision(requests)
        if decision is not None:
            await emit_policy_decision(self.telemetry_recorder, decision)
            raise BudgetExceededError(
                decision.reason,
                metadata=_decision_metadata(decision),
            )
        for request in requests:
            self._consume_budget(request)

    def _budget_exceeded_decision(
        self,
        requests: list[AuthorityRequest],
    ) -> PolicyDecision | None:
        request = requests[0]
        budget = self.policy.budget
        if budget is None:
            return None
        request_count = len(requests)
        budget_cost = sum(item.budget_cost for item in requests)
        if (
            budget.max_requests is not None
            and budget.used_requests + request_count > budget.max_requests
        ):
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                request_id=request.request_id,
                policy_id=self.policy.policy_id,
                policy_hash=self.policy.provenance.policy_hash,
                reason_code=ReasonCode.BUDGET_EXCEEDED,
                reason="Policy request budget exceeded.",
            )
        if (
            budget.max_cost is not None
            and budget.used_cost + budget_cost > budget.max_cost
        ):
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                request_id=request.request_id,
                policy_id=self.policy.policy_id,
                policy_hash=self.policy.provenance.policy_hash,
                reason_code=ReasonCode.BUDGET_EXCEEDED,
                reason="Policy cost budget exceeded.",
            )
        return None


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
