"""T1 of the telemetry storage plan: a model call's context is recorded once.

Measured on the repository steward: 63% of its trace file was the model's
context written four times per call — on the context assembly span and its
event, and on the model call span and its event — each a copy of the whole
conversation. The model call span keeps the one copy (exporters read it);
the others record digests and point to it, and the trajectory still shows
each call's full request.
"""

from __future__ import annotations

import json

import pytest

from test_run_suspend import RecordingModel
from test_telemetry_tool_record import _agent, _trace

TURNS = [[("c1", "lookup", '{"key": "a"}')], [("c2", "lookup", '{"key": "b"}')], [("c3", "lookup", '{"key": "c"}')]]


def _has_messages(value) -> bool:
    return isinstance(value, dict) and "messages" in value


@pytest.mark.asyncio
async def test_each_model_call_records_its_messages_once():
    agent = await _agent(RecordingModel(*TURNS, "done"), telemetry_config={"capture": "full"})
    trace = await _trace(agent)

    model_spans = [s for s in trace.spans if s.kind == "model.call"]
    assert len(model_spans) == 4
    # Since T2 no record carries a conversation: each message is its own
    # context_message event, once.
    copies = [record for record in [*trace.spans, *trace.events] if _has_messages(record.input)]
    assert copies == [], [getattr(r, "kind", None) or r.event_type for r in copies]


@pytest.mark.asyncio
async def test_the_trajectory_still_shows_each_calls_full_request():
    agent = await _agent(RecordingModel(*TURNS, "done"), telemetry_config={"capture": "full"})
    trace = await _trace(agent)
    trajectory = await agent.get_trajectory(trace.trace_id)

    calls = [c for step in trajectory["steps"] for c in step["model_calls"]]
    assert len(calls) == 4
    for number, call in enumerate(calls):
        assert len(call["request"]["messages"]) == 2 + 2 * number, call["model_span_id"]
        assert [t["function"]["name"] for t in call["request"]["tools"]].count("lookup") == 1
        assert call["request_capture"]["state"] == "available"
    assert "lookup" in json.dumps(calls[-1]["request"]["messages"])
