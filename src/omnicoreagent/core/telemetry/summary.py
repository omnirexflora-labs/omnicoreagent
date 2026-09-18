"""Run totals derived from a stored trace.

Every figure is computed from the trace's own records (event metadata, which
is kept under every capture policy), so the same summary is produced at run
time, from a reloaded JSONL file, or by an independent reader.
"""

from __future__ import annotations

from typing import Any

from omnicoreagent.core.telemetry.models import SpanStatus, TelemetryTrace, utc_now

TOOL_OUTCOMES = ("success", "error", "rejected", "timeout", "cancelled", "denied")
_WORKSPACE_OPERATIONS = {
    "workspace_write": "write",
    "workspace_delete": "delete",
}
_EXECUTION_EVENTS = {
    "tool_call",
    "tool_result",
    "tool_error",
    "mcp_tool_call",
    "mcp_tool_result",
    "mcp_tool_error",
    "workspace_read",
    "workspace_write",
    "workspace_delete",
}


def summarize_trace(trace: TelemetryTrace) -> dict[str, Any]:
    """Return the run totals for one trace (child traces are not included)."""
    events = trace.events
    spans = trace.spans
    model_calls = [e for e in events if e.event_type == "model_call"]
    model_facts = [
        e.metadata.get("model_call") or {}
        for e in events
        if e.event_type in {"model_response", "model_error"}
    ]
    responses = [e for e in events if e.event_type == "model_response"]

    tokens = {"input": 0, "output": 0, "total": 0, "cached_input": 0, "reasoning": 0}
    cost = 0.0
    cost_complete = bool(responses)
    for event in responses:
        facts = event.metadata.get("model_call") or {}
        for key, value in (facts.get("tokens") or {}).items():
            if key in tokens and isinstance(value, (int, float)):
                tokens[key] += value
        if isinstance(facts.get("estimated_cost_usd"), (int, float)):
            cost += facts["estimated_cost_usd"]
        else:
            cost_complete = False

    purposes = [e.metadata.get("purpose", "agent_turn") for e in model_calls]
    runtime_messages: dict[str, int] = {}
    for event in events:
        if event.event_type == "runtime_message":
            kind = str(event.metadata.get("kind"))
            runtime_messages[kind] = runtime_messages.get(kind, 0) + 1

    tool_calls = tool_outcomes(trace)
    by_outcome = {outcome: 0 for outcome in TOOL_OUTCOMES}
    for outcome in tool_calls.values():
        by_outcome[outcome] += 1

    subagent_spans = [s for s in spans if s.kind == "subagent.run"]
    child_trace_ids = [
        e.metadata.get("child_trace_id")
        for e in events
        if e.event_type in {"subagent_result", "subagent_error"}
        and e.metadata.get("child_trace_id")
    ]
    ended = trace.ended_at or utc_now()
    return {
        "steps": sum(1 for s in spans if s.kind == "agent.step"),
        "model_calls": {
            "total": len(model_calls),
            "agent_turn": purposes.count("agent_turn"),
            "context_summary": purposes.count("context_summary"),
            "failed": sum(1 for e in events if e.event_type == "model_error"),
        },
        "tokens": tokens,
        "estimated_cost_usd": round(cost, 10) if responses else None,
        "cost_complete": cost_complete,
        "model_latency_ms": round(
            sum(
                facts.get("latency_ms") or 0
                for facts in model_facts
                if isinstance(facts.get("latency_ms"), (int, float))
            ),
            3,
        ),
        "model_retries": sum(len(facts.get("retries") or []) for facts in model_facts),
        "tool_calls": {"total": len(tool_calls), "by_outcome": by_outcome},
        "compressions": sum(1 for e in events if e.event_type == "context_compression"),
        "runtime_messages": runtime_messages,
        "subagents": {"count": len(subagent_spans), "child_trace_ids": child_trace_ids},
        "workspace_changes": _workspace_changes(events),
        "offloaded_results": [
            {
                "tool_call_id": e.metadata.get("tool_call_id"),
                "reference": (e.output or {}).get("reference"),
            }
            for e in events
            if e.event_type == "workspace_offload"
        ],
        "duration_ms": round((ended - trace.started_at).total_seconds() * 1000, 3),
    }


def final_model_response_event_id(trace: TelemetryTrace) -> str | None:
    """The last agent-turn model response: the one that produced the answer."""
    agent_turn_calls = {
        e.event_id
        for e in trace.events
        if e.event_type == "model_call"
        and e.metadata.get("purpose", "agent_turn") == "agent_turn"
    }
    responses = [
        e
        for e in trace.events
        if e.event_type == "model_response"
        and e.metadata.get("model_call_event_id") in agent_turn_calls
    ]
    return responses[-1].event_id if responses else None


def tool_outcomes(trace: TelemetryTrace) -> dict[str, str]:
    """Outcome of every tool call in the trace, keyed by tool call ID."""
    outcomes: dict[str, str] = {}
    span_status = {span.span_id: span.status for span in trace.spans}
    tool_spans: dict[str, str] = {}
    for event in trace.events:
        call_id = event.metadata.get("tool_call_id")
        if event.event_type == "tool_requested" and call_id:
            outcomes.setdefault(call_id, "cancelled")
            if event.metadata.get("rejection_reason"):
                outcomes[call_id] = "rejected"
            continue
        if event.event_type not in _EXECUTION_EVENTS or not call_id:
            continue
        if event.metadata.get("tool_span_id"):
            tool_spans[call_id] = event.metadata["tool_span_id"]
        phase = event.metadata.get("phase")
        if phase == "authorization":
            outcomes[call_id] = "denied"
        elif phase == "timeout":
            outcomes[call_id] = "timeout"
        elif phase in {"result", "exception"}:
            failed = event.error is not None or event.event_type.endswith("_error")
            outcomes[call_id] = "error" if failed else "success"
    for call_id, outcome in outcomes.items():
        if outcome == "cancelled" and call_id in tool_spans:
            status = span_status.get(tool_spans[call_id])
            if status == SpanStatus.TIMEOUT:
                outcomes[call_id] = "timeout"
    return outcomes


def _workspace_changes(events) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for event in events:
        operation = _WORKSPACE_OPERATIONS.get(event.event_type)
        if operation is None or event.metadata.get("phase") != "result":
            continue
        arguments = (event.input or {}).get("tool_args")
        changes.append(
            {
                "tool_call_id": event.metadata.get("tool_call_id"),
                "tool_name": event.metadata.get("tool_name"),
                "operation": operation,
                "path": arguments.get("path") if isinstance(arguments, dict) else None,
                "succeeded": event.error is None,
            }
        )
    return changes
