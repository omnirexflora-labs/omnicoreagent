"""Read one run as an ordered trajectory: request to final answer.

``build_trajectory`` is a pure function of stored traces. Every event of the
trace appears exactly once: in the request, a step, a tool call, the final
section, or an ``other_events`` list at the level where it occurred. Payloads
appear only when the capture policy recorded them; identifiers, links, and
facts come from event metadata, which every policy keeps.
"""

from __future__ import annotations

from typing import Any

from omnicoreagent.core.telemetry.models import TelemetryEvent, TelemetryTrace
from omnicoreagent.core.telemetry.recorder import _capture_gaps
from omnicoreagent.core.telemetry.summary import (
    final_model_response_event_id,
    summarize_trace,
    tool_outcomes,
)

TRAJECTORY_VERSION = "omnicoreagent.trajectory/v1"

_TERMINAL_EVENTS = {"final_answer", "final_state", "runtime_error"}
_EXECUTION_OUTCOMES = {"sandbox_exec_completed", "sandbox_exec_failed"}


def build_trajectory(
    trace: TelemetryTrace,
    *,
    children: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the trajectory of one trace.

    ``children`` maps a child trace ID to its already-built trajectory; each
    is nested under the tool call that delegated to it.
    """
    children = children or {}
    spans = {span.span_id: span for span in trace.spans}
    placed: set[str] = set()

    def take(event: TelemetryEvent) -> str:
        placed.add(event.event_id)
        return event.event_id

    step_spans = sorted(
        (span for span in trace.spans if span.kind == "agent.step"),
        key=lambda span: span.started_at,
    )
    step_of = {span.span_id: index for index, span in enumerate(step_spans)}

    def ancestor(span_id: str | None, predicate) -> Any:
        seen: set[str] = set()
        while span_id and span_id not in seen:
            seen.add(span_id)
            span = spans.get(span_id)
            if span is None:
                return None
            if predicate(span):
                return span
            span_id = span.parent_span_id
        return None

    events = sorted(trace.events, key=lambda event: event.sequence_number)
    outcomes = tool_outcomes(trace)

    # Tool calls: grouped by explicit tool_call_id, then by tool span ancestry.
    calls: dict[str, dict[str, Any]] = {}
    call_order: list[str] = []
    span_call: dict[str, str] = {}
    for event in events:
        call_id = event.metadata.get("tool_call_id")
        if not call_id:
            continue
        if call_id not in calls:
            calls[call_id] = {"tool_call_id": call_id, "_events": []}
            call_order.append(call_id)
        calls[call_id]["_events"].append(event)
        if event.metadata.get("tool_span_id"):
            span_call[event.metadata["tool_span_id"]] = call_id
    for event in events:
        if event.metadata.get("tool_call_id"):
            continue
        tool_span = ancestor(event.span_id, lambda span: span.span_id in span_call)
        if tool_span is not None:
            calls[span_call[tool_span.span_id]]["_events"].append(event)

    steps: list[dict[str, Any]] = [
        {
            "step": index + 1,
            "span_id": span.span_id,
            "status": _value(span.status),
            "started_at": _iso(span.started_at),
            "ended_at": _iso(span.ended_at),
            "duration_ms": span.duration_ms,
            "runtime_messages": [],
            "context": [],
            "model_calls": [],
            "tool_calls": [],
            "other_events": [],
        }
        for index, span in enumerate(step_spans)
    ]

    def step_for(event: TelemetryEvent) -> dict[str, Any] | None:
        span = ancestor(event.span_id, lambda s: s.span_id in step_of)
        return steps[step_of[span.span_id]] if span is not None else None

    # Tool calls are placed in the step whose span contains their records;
    # a call recorded outside every step is kept separately, never dropped.
    calls_outside_steps: list[dict[str, Any]] = []
    for call_id in call_order:
        call = _tool_call(calls[call_id], outcomes.get(call_id), children, take)
        owner = next(
            (step_for(event) for event in calls[call_id]["_events"] if step_for(event)),
            None,
        )
        (owner["tool_calls"] if owner else calls_outside_steps).append(call)

    responses = {
        event.metadata.get("model_call_event_id"): event
        for event in events
        if event.event_type in {"model_response", "model_error"}
    }
    request: dict[str, Any] = {}
    header: dict[str, Any] | None = None
    final: dict[str, Any] = {}
    top_level_runtime: list[dict[str, Any]] = []
    other_events: list[dict[str, Any]] = []

    for event in events:
        if event.event_id in placed:
            continue
        step = step_for(event)
        kind = event.event_type
        if kind == "user_message" and not request:
            request = {
                "event_id": take(event),
                "message": (event.input or {}).get("message"),
                "capture": _capture(event.input_capture),
            }
        elif kind == "run_configuration" and header is None:
            header = {
                "event_id": take(event),
                **(event.metadata.get("run_configuration") or {}),
                "system_prompt_text": (event.input or {}).get("system_prompt"),
            }
        elif kind == "runtime_message":
            entry = {
                "event_id": take(event),
                **{k: event.metadata.get(k) for k in ("kind", "role", "content", "message_digest")},
            }
            (step["runtime_messages"] if step else top_level_runtime).append(entry)
        elif kind in {"context_assembly", "context_compression"} and step is not None:
            step["context"].append(
                {
                    "event_id": take(event),
                    "type": kind,
                    **_context_fields(event),
                }
            )
        elif kind == "model_call":
            response = responses.get(event.event_id)
            entry = {
                "model_call_event_id": take(event),
                "model_span_id": event.metadata.get("model_span_id"),
                "purpose": event.metadata.get("purpose", "agent_turn"),
                "context_digest": event.metadata.get("context_digest"),
                "new_observation_event_ids": event.metadata.get(
                    "new_observation_event_ids", []
                ),
                "request": event.input,
                "request_capture": _capture(event.input_capture),
            }
            if response is not None:
                entry.update(
                    {
                        "response_event_id": take(response),
                        "outcome": "error" if response.event_type == "model_error" else "ok",
                        "facts": response.metadata.get("model_call"),
                        "response": response.output,
                        "response_capture": _capture(response.output_capture),
                        "error": _error(response),
                    }
                )
            (step["model_calls"] if step else other_events).append(entry)
        elif kind in _TERMINAL_EVENTS and not final:
            final = {
                "event_id": take(event),
                "type": kind,
                "output": event.output,
                "output_capture": _capture(event.output_capture),
                "error": _error(event),
                "final_model_response_event_id": event.metadata.get(
                    "final_model_response_event_id"
                ),
            }
        else:
            entry = {
                "event_id": take(event),
                "event_type": kind,
                "span_id": event.span_id,
            }
            (step["other_events"] if step else other_events).append(entry)

    summary = next(
        (
            event.metadata.get("run_summary")
            for event in events
            if event.event_type in _TERMINAL_EVENTS and event.metadata.get("run_summary")
        ),
        None,
    ) or summarize_trace(trace)
    if final and final.get("final_model_response_event_id") is None:
        final["final_model_response_event_id"] = final_model_response_event_id(trace)

    return {
        "trajectory_version": TRAJECTORY_VERSION,
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "session_id": trace.session_id,
        "task_id": trace.task_id,
        "agent_id": trace.agent_id,
        "parent_trace_id": trace.parent_trace_id,
        "parent_span_id": trace.parent_span_id,
        "execution_surface": trace.execution_surface,
        "status": _value(trace.status),
        "evidence_status": _value(trace.evidence_status),
        "incomplete": trace.incomplete,
        "started_at": _iso(trace.started_at),
        "ended_at": _iso(trace.ended_at),
        "versions": {
            key: getattr(trace.metadata, key)
            for key in (
                "agent_version",
                "prompt_version",
                "tool_schema_version",
                "memory_config_version",
                "telemetry_config_version",
                "privacy_config_version",
            )
        },
        "tags": list(trace.metadata.tags),
        "provenance": trace.provenance.model_dump(),
        "request": request or None,
        "harness": header,
        "runtime_messages": top_level_runtime,
        "steps": steps,
        "tool_calls_outside_steps": calls_outside_steps,
        "final": final or None,
        "totals": summary,
        "capture_gaps": _capture_gaps(trace),
        "other_events": other_events,
    }


def trajectory_event_ids(trajectory: dict[str, Any]) -> list[str]:
    """Every event ID a trajectory accounts for, nested child runs excluded."""
    ids: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "child_trajectory":
                    continue
                if key in {"event_id", "model_call_event_id", "response_event_id"} and item:
                    ids.append(item)
                elif key == "event_ids" and isinstance(item, list):
                    ids.extend(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit({k: v for k, v in trajectory.items() if k not in {"totals", "capture_gaps"}})
    return ids


def _tool_call(
    call: dict[str, Any],
    outcome: str | None,
    children: dict[str, dict[str, Any]],
    take,
) -> dict[str, Any]:
    events: list[TelemetryEvent] = call["_events"]
    by_type: dict[str, list[TelemetryEvent]] = {}
    for event in events:
        by_type.setdefault(event.event_type, []).append(event)
    requested = (by_type.get("tool_requested") or [None])[0]
    observation = (by_type.get("tool_observation") or [None])[0]
    execution = [
        event
        for event in events
        if event.metadata.get("phase") in {"execution", "result", "exception", "timeout", "authorization"}
    ]
    outcome_event = next(
        (
            event
            for event in reversed(execution)
            if event.metadata.get("phase") in {"result", "exception", "timeout", "authorization"}
        ),
        None,
    )
    subagent_events = [
        event for event in events if event.event_type in {"subagent_result", "subagent_error"}
    ]
    child_id = next(
        (event.metadata.get("child_trace_id") for event in subagent_events if event.metadata.get("child_trace_id")),
        None,
    )
    requested_input = (requested.input or {}) if requested else {}
    record = {
        "tool_call_id": call["tool_call_id"],
        "tool_name": requested_input.get("tool_name")
        or (outcome_event.metadata.get("tool_name") if outcome_event else None),
        "provider": outcome_event.metadata.get("tool_provider") if outcome_event else None,
        "server": next(
            (e.metadata.get("tool_server") for e in events if e.metadata.get("tool_server")),
            None,
        ),
        "outcome": outcome,
        "rejection_reason": requested.metadata.get("rejection_reason") if requested else None,
        "raw_arguments": requested_input.get("raw_arguments"),
        "arguments": requested_input.get("arguments"),
        "requested_event_id": requested.event_id if requested else None,
        "tool_span_id": outcome_event.metadata.get("tool_span_id") if outcome_event else None,
        "result": outcome_event.output if outcome_event else None,
        "result_capture": _capture(outcome_event.output_capture) if outcome_event else None,
        "error": _error(outcome_event) if outcome_event else None,
        "governance": [
            {
                "event_id": event.event_id,
                "effect": event.metadata.get("effect"),
                "capability": event.metadata.get("capability"),
                "reason_code": event.metadata.get("reason_code"),
            }
            for event in events
            if event.event_type.startswith("policy_decision_")
        ],
        "observation": (
            {
                "event_id": observation.event_id,
                "content": ((observation.output or {}).get("message") or {}).get("content"),
                "capture": _capture(observation.output_capture),
                "tool_result_event_id": observation.metadata.get("tool_result_event_id"),
            }
            if observation
            else None
        ),
        "subagent": (
            {
                "child_trace_id": child_id,
                "child_run_id": next(
                    (e.metadata.get("child_run_id") for e in subagent_events if e.metadata.get("child_run_id")),
                    None,
                ),
                "child_trajectory": children.get(child_id),
            }
            if subagent_events
            else None
        ),
        "reconnects": [
            {
                "event_id": event.event_id,
                "mcp_server": event.metadata.get("mcp_server"),
                "outcome": event.metadata.get("outcome"),
                "reason": event.metadata.get("reason"),
                "error": _error(event),
            }
            for event in events
            if event.event_type == "mcp_reconnect"
        ],
        "executions": [
            _execution(event)
            for event in events
            if event.event_type in _EXECUTION_OUTCOMES
            and event.metadata.get("purpose") != "workspace_sync"
        ],
        "workspace_sync": _workspace_sync(events),
        "event_ids": [take(event) for event in events],
    }
    return record


def _execution(event: TelemetryEvent) -> dict[str, Any]:
    """One sandboxed command: facts always, command and output when captured."""
    facts = event.metadata
    output = event.output or {}
    return {
        "event_id": event.event_id,
        **{
            key: facts.get(key)
            for key in (
                "execution_id",
                "sandbox_session_id",
                "sandbox_provider",
                "exit_code",
                "timed_out",
                "duration_ms",
                "stdout_bytes",
                "stderr_bytes",
                "stdout_truncated",
                "stderr_truncated",
                "matched_rule_ids",
            )
        },
        "command": (event.input or {}).get("command"),
        "stdout": output.get("stdout"),
        "stderr": output.get("stderr"),
        "output_capture": _capture(event.output_capture),
        "error": _error(event),
    }


def _workspace_sync(events: list[TelemetryEvent]) -> dict[str, Any] | None:
    syncs = [event for event in events if event.event_type == "sandbox_workspace_sync"]
    if not syncs:
        return None
    return {
        "event_ids": [event.event_id for event in syncs],
        "copied_in": [path for event in syncs for path in event.metadata.get("copied_in") or []],
        "written": [path for event in syncs for path in event.metadata.get("written") or []],
        "skipped": [item for event in syncs for item in event.metadata.get("skipped") or []],
    }


def _context_fields(event: TelemetryEvent) -> dict[str, Any]:
    output = event.output or {}
    fields = {
        key: event.metadata.get(key)
        for key in ("context_digest", "observation_event_ids", "new_observation_event_ids")
        if key in event.metadata
    }
    for key in ("message_count", "tool_count", "tool_names", "tool_catalog_digest", "role_counts"):
        if key in output:
            fields[key] = output[key]
    if event.event_type == "context_compression":
        fields["before"] = output.get("before")
        fields["after"] = output.get("after")
    fields["capture"] = _capture(event.output_capture)
    return fields


def _capture(capture: Any) -> dict[str, Any] | None:
    if capture is None:
        return None
    return {
        "state": _value(capture.state),
        "reason": capture.reason,
        "reference": capture.reference,
    }


def _error(event: TelemetryEvent | None) -> dict[str, Any] | None:
    if event is None or event.error is None:
        return None
    return {"type": event.error.type, "message": event.error.message}


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
