"""A resumed run's trace says what happened, once, and without false errors.

Read from real resumed runs in the support desk chaos and ramp (2026-10-07):
the approval a person decided was asked again, with a new event id, in the
resumed segment; and a call that paused for a person read as an error in the
trace backend, which paged anyone alerting on errors at every ordinary pause.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    OTelTraceMapper,
    SpanStatus,
    TelemetryActor,
    TelemetrySpan,
    TelemetryTrace,
)
from omnicoreagent.core.telemetry.exporters import _to_otlp_status
from omnicoreagent.core.telemetry.models import TraceFilter
from test_resumed_run_evidence import _approved_and_resumed


async def _events(agent, run_id, event_type):
    found = []
    traces = await agent.telemetry_store.list_traces(TraceFilter(run_id=run_id))
    for trace in sorted(traces, key=lambda t: t.started_at):
        full = await agent.telemetry_store.get_trace(trace.trace_id)
        found.extend(e for e in full.events if e.event_type == event_type)
    return found


@pytest.mark.asyncio
async def test_a_resumed_run_does_not_ask_the_same_question_again(tmp_path):
    agent = await _approved_and_resumed(tmp_path)

    asks = await _events(agent, "run_s1", "approval_request_created")

    assert len(asks) == 1, "the approval was asked once, in the segment that paused"
    # The decision is still recorded, against the ask it answers.
    (resolved,) = await _events(agent, "run_s1", "approval_resolved")
    assert resolved.output["result"]["approved"] is True


@pytest.mark.asyncio
async def test_a_call_waiting_for_a_person_is_not_an_error_in_the_trajectory(tmp_path):
    agent = await _approved_and_resumed(tmp_path)

    story = await agent.get_run_trajectory("run_s1")
    paused = story["segments"][0]["trajectory"]
    (waiting,) = [
        c for s in paused["steps"] for c in s["tool_calls"] if c["tool_name"] == "send_invoice"
    ]

    assert waiting["outcome"] == "awaiting_approval"
    assert waiting["error"] is None


def test_a_skipped_span_is_not_an_error_in_otlp():
    from opentelemetry.proto.trace.v1.trace_pb2 import Status

    assert _to_otlp_status(SpanStatus.SKIPPED.value).code == Status.STATUS_CODE_UNSET
    assert _to_otlp_status(SpanStatus.ERROR.value).code == Status.STATUS_CODE_ERROR


def test_a_skipped_span_carries_no_error_attributes():
    actor = TelemetryActor(type=ActorType.TOOL, name="send_invoice")
    span = TelemetrySpan(
        trace_id="t",
        span_id="s",
        name="tool.call",
        kind="tool.call",
        actor=actor,
        status=SpanStatus.SKIPPED,
        error={"type": "ApprovalRequiredError", "message": "Matched ask policy rule."},
    )
    trace = TelemetryTrace(trace_id="t", root_span_id="s", spans=[span], events=[])

    (record,) = OTelTraceMapper().map_trace(trace)

    assert "error.type" not in record.attributes
