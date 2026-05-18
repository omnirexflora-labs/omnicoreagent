from __future__ import annotations

from typing import Any

from omnicoreagent.core.events.base import Event, EventType
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
)


LEGACY_EVENT_TYPE_MAP = {
    EventType.USER_MESSAGE: "user_message",
    EventType.AGENT_MESSAGE: "model_response",
    EventType.AGENT_THOUGHT: "planning_step",
    EventType.TOOL_CALL_STARTED: "tool_call",
    EventType.TOOL_CALL_RESULT: "tool_result",
    EventType.TOOL_CALL_ERROR: "tool_error",
    EventType.SUB_AGENT_CALL_STARTED: "subagent_spawn",
    EventType.SUB_AGENT_CALL_RESULT: "subagent_result",
    EventType.SUB_AGENT_CALL_ERROR: "subagent_error",
    EventType.FINAL_ANSWER: "final_answer",
}


def legacy_event_to_telemetry_event(
    event: Event,
    *,
    trace_id: str,
    span_id: str | None = None,
    legacy_unbound: bool = False,
) -> TelemetryEvent:
    payload = event.payload.model_dump()
    return TelemetryEvent(
        trace_id=trace_id,
        span_id=span_id,
        event_id=event.event_id,
        sequence_number=event.sequence or 0,
        timestamp=event.timestamp,
        event_type=_legacy_event_type(event),
        actor=_legacy_actor(event),
        input=_legacy_input(event.type, payload),
        output=_legacy_output(event.type, payload),
        error=_legacy_error(event.type, payload),
        metadata={
            "legacy_event_type": event.type.value,
            "legacy_agent_name": event.agent_name,
            "legacy_run_id": event.run_id,
            "legacy_unbound": legacy_unbound,
        },
    )


def _legacy_actor(event: Event) -> TelemetryActor:
    if event.type == EventType.USER_MESSAGE:
        return TelemetryActor(type=ActorType.USER)
    if event.type in {
        EventType.TOOL_CALL_STARTED,
        EventType.TOOL_CALL_RESULT,
        EventType.TOOL_CALL_ERROR,
    }:
        return TelemetryActor(type=ActorType.TOOL, name=getattr(event.payload, "tool_name", None))
    if event.type == EventType.BACKGROUND_AGENT_STATUS:
        return TelemetryActor(type=ActorType.BACKGROUND, name=event.agent_name)
    return TelemetryActor(type=ActorType.AGENT, name=event.agent_name)


def _legacy_event_type(event: Event) -> str:
    if event.type == EventType.BACKGROUND_AGENT_STATUS:
        payload = event.payload.model_dump()
        lifecycle = (
            payload.get("event")
            or payload.get("run_status")
            or payload.get("status")
            or "heartbeat"
        )
        normalized = str(lifecycle).lower()
        if "queued" in normalized:
            return "background_run_queued"
        if "started" in normalized or "running" in normalized:
            return "background_run_started"
        if "completed" in normalized or "succeeded" in normalized:
            return "background_run_completed"
        if "failed" in normalized or "error" in normalized:
            return "background_run_failed"
        if "cancelled" in normalized or "canceled" in normalized:
            return "background_run_cancelled"
        if "timeout" in normalized or "timed_out" in normalized:
            return "background_run_timeout"
        return "background_run_heartbeat"
    return LEGACY_EVENT_TYPE_MAP[event.type]


def _legacy_input(event_type: EventType, payload: dict[str, Any]) -> dict[str, Any] | None:
    if event_type in {EventType.USER_MESSAGE, EventType.TOOL_CALL_STARTED}:
        return payload
    return None


def _legacy_output(
    event_type: EventType,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if event_type in {
        EventType.AGENT_MESSAGE,
        EventType.AGENT_THOUGHT,
        EventType.TOOL_CALL_RESULT,
        EventType.SUB_AGENT_CALL_RESULT,
        EventType.BACKGROUND_AGENT_STATUS,
        EventType.FINAL_ANSWER,
    }:
        return payload
    return None


def _legacy_error(event_type: EventType, payload: dict[str, Any]) -> dict[str, Any] | None:
    if event_type == EventType.TOOL_CALL_ERROR:
        return {
            "type": "ToolCallError",
            "message": payload.get("error_message") or "Tool call failed",
            "retryable": None,
            "metadata": payload,
            "stack": None,
        }
    if event_type == EventType.SUB_AGENT_CALL_ERROR:
        return {
            "type": "SubAgentError",
            "message": payload.get("error") or "Subagent failed",
            "retryable": None,
            "metadata": payload,
            "stack": None,
        }
    return None
