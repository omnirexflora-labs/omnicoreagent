from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from omnicoreagent.core.events.base import Event, EventType, _to_plain


@dataclass
class TraceStep:
    index: int
    event_id: str
    event_type: str
    agent_name: str
    timestamp: str
    payload: dict[str, Any]
    sequence: int | None = None
    elapsed_ms: float | None = None
    since_previous_ms: float | None = None

    def model_dump(self) -> dict[str, Any]:
        return _to_plain(self)


@dataclass
class TraceSummary:
    session_id: str
    total_events: int
    event_counts: dict[str, int] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    sub_agent_calls: int = 0
    sub_agent_errors: int = 0
    background_errors: int = 0
    final_answer: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return _to_plain(self)


@dataclass
class AgentTrace:
    session_id: str
    summary: TraceSummary
    steps: list[TraceStep] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return _to_plain(self)


def build_event_trace(session_id: str, events: list[Event]) -> AgentTrace:
    """Build a compact internal trace from session events."""
    ordered_events = _order_events(events)
    steps: list[TraceStep] = []

    first_timestamp = ordered_events[0].timestamp if ordered_events else None
    previous_timestamp: datetime | None = None

    for index, event in enumerate(ordered_events, start=1):
        elapsed_ms = (
            _duration_ms(first_timestamp, event.timestamp)
            if first_timestamp is not None
            else None
        )
        since_previous_ms = (
            _duration_ms(previous_timestamp, event.timestamp)
            if previous_timestamp is not None
            else None
        )
        payload = event.payload.model_dump()
        steps.append(
            TraceStep(
                index=index,
                event_id=event.event_id,
                event_type=event.type.value,
                agent_name=event.agent_name,
                timestamp=event.timestamp.isoformat(),
                payload=payload,
                sequence=event.sequence,
                elapsed_ms=elapsed_ms,
                since_previous_ms=since_previous_ms,
            )
        )
        previous_timestamp = event.timestamp

    summary = _build_summary(session_id=session_id, events=ordered_events)
    return AgentTrace(session_id=session_id, summary=summary, steps=steps)


def _order_events(events: list[Event]) -> list[Event]:
    if all(event.sequence is not None for event in events):
        return sorted(events, key=lambda event: (event.sequence, event.timestamp))
    return sorted(events, key=lambda event: event.timestamp)


def _build_summary(session_id: str, events: list[Event]) -> TraceSummary:
    counts = Counter(event.type.value for event in events)
    first_timestamp = events[0].timestamp if events else None
    last_timestamp = events[-1].timestamp if events else None
    final_answer = None

    for event in reversed(events):
        if event.type == EventType.FINAL_ANSWER:
            final_answer = getattr(event.payload, "message", None)
            break

    return TraceSummary(
        session_id=session_id,
        total_events=len(events),
        event_counts=dict(counts),
        started_at=first_timestamp.isoformat() if first_timestamp else None,
        ended_at=last_timestamp.isoformat() if last_timestamp else None,
        duration_ms=(
            _duration_ms(first_timestamp, last_timestamp)
            if first_timestamp and last_timestamp
            else None
        ),
        tool_calls=counts.get(EventType.TOOL_CALL_STARTED.value, 0),
        tool_errors=counts.get(EventType.TOOL_CALL_ERROR.value, 0),
        sub_agent_calls=counts.get(EventType.SUB_AGENT_CALL_STARTED.value, 0),
        sub_agent_errors=counts.get(EventType.SUB_AGENT_CALL_ERROR.value, 0),
        background_errors=sum(1 for event in events if _is_background_error(event)),
        final_answer=final_answer,
    )


def _is_background_error(event: Event) -> bool:
    if event.type != EventType.BACKGROUND_AGENT_STATUS:
        return False
    lifecycle_event = getattr(event.payload, "event", None) or getattr(
        event.payload, "status", None
    )
    return lifecycle_event in {"background_run_failed", "background_run_timeout"}


def _duration_ms(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() * 1000, 3)
