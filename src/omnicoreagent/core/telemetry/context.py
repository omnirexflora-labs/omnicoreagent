from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
    # Set for a background attempt and inherited by the traces it starts.
    attempt_id: str | None = None
    attempt_number: int | None = None
    # How the run was entered (interactive, serve, background, headless, or
    # controlled for an imported evaluation trace);
    # traces started beneath it inherit it.
    execution_surface: str | None = None

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


# How the code calling agent.run entered it, when that is not a telemetry
# parent of its own: the headless CLI. A trace started without one, and with
# no surface inherited, is "interactive" (a direct agent.run).
_ENTRY_SURFACE: ContextVar[str | None] = ContextVar("omnicoreagent_entry_surface", default=None)


def current_entry_surface() -> str | None:
    return _ENTRY_SURFACE.get()


@contextmanager
def entry_surface(name: str) -> Iterator[None]:
    """Label the runs started inside this block with how they were entered."""
    token = _ENTRY_SURFACE.set(name)
    try:
        yield
    finally:
        _ENTRY_SURFACE.reset(token)
