from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TelemetryContext:
    trace_id: str
    span_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    suite_id: str | None = None
    agent_id: str | None = None
    workflow_id: str | None = None

    def child(self, span_id: str) -> TelemetryContext:
        return replace(self, span_id=span_id)


_CURRENT_TELEMETRY_CONTEXT: ContextVar[TelemetryContext | None] = ContextVar(
    "omnicoreagent_telemetry_context",
    default=None,
)


def current_telemetry_context() -> TelemetryContext | None:
    return _CURRENT_TELEMETRY_CONTEXT.get()


def set_telemetry_context(context: TelemetryContext | None) -> Token:
    return _CURRENT_TELEMETRY_CONTEXT.set(context)


def reset_telemetry_context(token: Token) -> None:
    _CURRENT_TELEMETRY_CONTEXT.reset(token)
