from __future__ import annotations

from typing import Any

from omnicoreagent.core.telemetry import ActorType, TelemetryActor, TelemetryRecorder
from omnicoreagent.governance.models import (
    ApprovalRequest,
    ApprovalResult,
    AuthorityRequest,
    PolicyDecision,
    to_plain,
)


GOVERNANCE_EVENT_TYPES = frozenset(
    {
        "policy_request_created",
        "policy_decision_allow",
        "policy_decision_ask",
        "policy_decision_deny",
        "policy_decisions_summarized",
        "approval_request_created",
        "approval_resolved",
        "sandbox_session_created",
        "sandbox_exec_started",
        "sandbox_exec_completed",
        "sandbox_exec_failed",
        "policy_violation",
        "secret_access_denied",
        "secret_access_brokered",
        "network_access_denied",
        "network_access_allowed",
        "filesystem_access_denied",
        "filesystem_access_allowed",
    }
)


async def emit_policy_request(
    recorder: TelemetryRecorder | None,
    request: AuthorityRequest,
    *,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    await _emit(
        recorder,
        "policy_request_created",
        input={"request": _request_payload(request)},
        metadata=_request_correlation(request),
        strict=strict,
    )


async def emit_policy_decision(
    recorder: TelemetryRecorder | None,
    decision: PolicyDecision,
    *,
    request: AuthorityRequest | None = None,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    metadata = {
        "decision_id": decision.decision_id,
        "request_id": decision.request_id,
        "effect": decision.effect.value,
        "reason_code": getattr(decision.reason_code, "value", decision.reason_code),
        "policy_hash": decision.policy_hash,
        "matched_rule_ids": list(decision.matched_rule_ids),
        "approval_id": decision.approval_id,
        "approved_by": (decision.metadata or {}).get("approved_by"),
    }
    if request is not None:
        metadata.update(_request_correlation(request))
    await _emit(
        recorder,
        f"policy_decision_{decision.effect.value}",
        output={"decision": to_plain(decision)},
        metadata=metadata,
        strict=strict,
    )


async def emit_policy_summary(
    recorder: TelemetryRecorder | None,
    *,
    purpose: str,
    capability: str,
    allowed: int,
    denied: list[str],
) -> None:
    """Routine checks, recorded as one: how many were allowed, which were not
    (each refusal is also recorded on its own)."""
    if recorder is None or not (allowed or denied):
        return
    await _emit(
        recorder,
        "policy_decisions_summarized",
        output={
            "purpose": purpose,
            "capability": capability,
            "allowed": allowed,
            "denied": denied,
        },
        metadata={"purpose": purpose, "capability": capability},
    )


async def emit_policy_violation(
    recorder: TelemetryRecorder | None,
    decision: PolicyDecision,
    *,
    reason_code: str,
    metadata: dict[str, Any] | None = None,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    await _emit(
        recorder,
        "policy_violation",
        output={
            "decision": to_plain(decision),
            "reason_code": reason_code,
            "metadata": metadata or {},
        },
        strict=strict,
    )


async def emit_approval_request(
    recorder: TelemetryRecorder | None,
    request: ApprovalRequest,
    *,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    await _emit(
        recorder,
        "approval_request_created",
        input={"approval": to_plain(request)},
        strict=strict,
    )


async def emit_approval_result(
    recorder: TelemetryRecorder | None,
    request: ApprovalRequest,
    result: ApprovalResult,
    *,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    await _emit(
        recorder,
        "approval_resolved",
        input={"approval_id": request.approval_id},
        output={"approval": to_plain(request), "result": to_plain(result)},
        strict=strict,
    )


# Request metadata fields that carry delegated content rather than authority
# facts. Their size and shape (for example ``task_length``) stay recorded.
_CONTENT_METADATA_KEYS = frozenset({"task"})


def _request_payload(request: AuthorityRequest) -> dict[str, Any]:
    payload = to_plain(request)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        payload["metadata"] = {
            key: "[REDACTED]" if key in _CONTENT_METADATA_KEYS and value else value
            for key, value in metadata.items()
        }
    return payload


def _request_correlation(request: AuthorityRequest) -> dict[str, Any]:
    """Identity of an authority request, recorded under every capture policy."""
    metadata = request.metadata or {}
    return {
        "request_id": request.request_id,
        "capability": request.capability,
        "provider": request.provider,
        "risk_level": request.risk_level,
        "tool_call_id": metadata.get("tool_call_id"),
        "tool_name": metadata.get("tool_name"),
        "mcp_server": request.mcp_server,
    }


async def _emit(
    recorder: TelemetryRecorder,
    event_type: str,
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    strict: bool = False,
) -> None:
    try:
        await recorder.emit_event(
            event_type,
            actor=TelemetryActor(type=ActorType.SYSTEM, name="governance"),
            input=input,
            output=output,
            metadata=metadata,
        )
    except RuntimeError:
        # No active trace: governance can be evaluated outside the agent hot path.
        if strict:
            raise
        return
    except Exception:
        if strict or getattr(recorder.config, "strict", False):
            raise
        return
