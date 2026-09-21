"""T2 of the telemetry storage plan: each message is recorded once per trace.

After T1 a model call still recorded the whole conversation, cut at 64 KB:
the repository steward's long runs were recorded *partial*, and the trace
grew with the square of a run's length. Each message is now a
`context_message` event, once per trace; the tool catalog a
`context_tools` event, once; a model call records which messages it was
sent — the previous call's list it extends and what it appends. The
trajectory and the exporters see every call's full request, rebuilt.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry.exporters import InMemoryTelemetryExporter
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from test_run_suspend import _MODEL, RecordingModel

REPORT = "a line of a long report\n" * 130  # about 3 KB per tool result


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("report", description="Return a long report.")
    def report(part: int) -> str:
        return f"part {part}\n{REPORT}"

    return tools


async def _run(tmp_path, steps: int, *, exporter=None):
    turns = [[(f"c{i}", "report", json.dumps({"part": i}))] for i in range(steps)]
    model = RecordingModel(*turns, "done")
    agent = OmniCoreAgent(
        name="reader",
        system_instruction="Read every part of the report.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "off",
            "max_steps": steps + 5,
            "workspace_config": {"workspace_dir": str(tmp_path / f"ws{steps}")},
            "tool_offload": {"enabled": False},
        },
        telemetry_config={"capture": "full"},
        telemetry_exporters=[exporter] if exporter else None,
    )
    await agent.initialize()
    agent.llm_connection = model
    result = await agent.run("read the report", session_id=f"long-{steps}")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    trajectory = await agent.get_trajectory(result["trace_id"])
    return model, trace, trajectory


@pytest.mark.asyncio
async def test_a_long_run_is_recorded_complete_at_full_capture(tmp_path):
    # 24 tool results of 3 KB: the conversation outgrows the 64 KB cut.
    _, trace, trajectory = await _run(tmp_path, 24)

    assert trajectory["evidence_status"] == "complete", trajectory["capture_gaps"][:3]


@pytest.mark.asyncio
async def test_each_message_is_recorded_once_and_every_request_is_whole(tmp_path):
    model, trace, trajectory = await _run(tmp_path, 8)

    recorded = [e.metadata["message_digest"] for e in trace.events if e.event_type == "context_message"]
    assert len(recorded) == len(set(recorded)), "a message recorded twice"
    assert not any("messages" in (s.input or {}) for s in trace.spans if s.kind == "model.call")
    calls = [c for step in trajectory["steps"] for c in step["model_calls"]]
    assert len(calls) == len(model.calls) == 9
    for call, sent in zip(calls, model.calls):
        assert [m.get("content") for m in call["request"]["messages"]] == [m.get("content") for m in sent]
        assert "report" in [t["function"]["name"] for t in call["request"]["tools"]]


@pytest.mark.asyncio
async def test_a_trace_grows_with_its_messages_not_their_square(tmp_path):
    _, short, _ = await _run(tmp_path, 8)
    _, long, _ = await _run(tmp_path, 24)

    def size(trace):
        return len(json.dumps(trace.model_dump(), default=str))

    assert size(long) < 3.6 * size(short), (size(short), size(long))


@pytest.mark.asyncio
async def test_exporters_see_every_calls_whole_request(tmp_path):
    keep = InMemoryTelemetryExporter(normalize=False)
    await _run(tmp_path, 3, exporter=keep)

    (exported,) = keep.traces
    inputs = [s.input for s in exported.spans if s.kind == "model.call"]
    assert len(inputs) == 4
    assert all(len(i["messages"]) >= 2 and i["tools"] for i in inputs), inputs[0].keys()
    assert "part 2" in json.dumps(inputs[-1]["messages"])


@pytest.mark.asyncio
async def test_a_request_is_rebuilt_whatever_order_the_store_keeps_spans_in(tmp_path):
    from dataclasses import replace

    from omnicoreagent.core.telemetry.context_record import expand_model_contexts

    _, trace, _ = await _run(tmp_path, 4)
    in_order = expand_model_contexts(trace)
    reversed_trace = replace(trace, spans=list(reversed(trace.spans)))

    assert expand_model_contexts(reversed_trace) == in_order
    assert all(entry["complete"] for entry in in_order.values())
