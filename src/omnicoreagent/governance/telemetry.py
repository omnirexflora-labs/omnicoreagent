from __future__ import annotations

from typing import Any

from omnicoreagent.core.telemetry import ActorType, TelemetryActor, TelemetryRecorder
from omnicoreagent.governance.models import AuthorityRequest, PolicyDecision, to_plain


GOVERNANCE_EVENT_TYPES = frozenset(
    {
        "policy_request_created",
        "policy_decision_allow",
        "policy_decision_ask",
        "policy_decision_deny",
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
        input={"request": to_plain(request)},
        strict=strict,
    )


async def emit_policy_decision(
    recorder: TelemetryRecorder | None,
    decision: PolicyDecision,
    *,
    strict: bool = False,
) -> None:
    if recorder is None:
        return
    await _emit(
        recorder,
        f"policy_decision_{decision.effect.value}",
        output={"decision": to_plain(decision)},
        strict=strict,
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


async def _emit(
    recorder: TelemetryRecorder,
    event_type: str,
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    strict: bool = False,
) -> None:
    try:
        await recorder.emit_event(
            event_type,
            actor=TelemetryActor(type=ActorType.SYSTEM, name="governance"),
            input=input,
            output=output,
        )
    except RuntimeError:
        # No active trace: governance can be evaluated outside the agent hot path.
        return
    except Exception:
        if strict or getattr(recorder.config, "strict", False):
            raise
        return
