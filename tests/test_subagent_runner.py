import asyncio
import pytest
from omnicoreagent.core.agents.subagent_helpers import build_kwargs
from omnicoreagent.core.agents.subagent_runner import SubAgentCallRunner
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.types import AgentState, SessionState
from omnicoreagent.core.agents.loop_detection import NativeLoopDetector


class FakeAgent:
    def __init__(self, name, result):
        self.name = name
        self.result = result
        self.mcp_tools = []
        self.cleaned = False

    async def run(self, task=None, session_id=None):
        self.kwargs = {"task": task, "session_id": session_id}
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def cleanup_mcp_servers(self):
        self.cleaned = True


class CancellingAgent(FakeAgent):
    async def run(self, task=None, session_id=None):
        self.kwargs = {"task": task, "session_id": session_id}
        raise asyncio.CancelledError


def _session_state():
    return SessionState(
        messages=[],
        state=AgentState.IDLE,
        loop_detector=NativeLoopDetector(),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


@pytest.mark.asyncio
async def test_subagent_runner_records_successful_outputs():
    runner = SubAgentCallRunner(agent_name="parent")
    sub_agent = FakeAgent("research", {"response": "done"})
    history = []
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-subagent-success",
        run_id="run-subagent-success",
        session_id="s1",
        actor=TelemetryActor(type="agent", name="parent"),
    )

    async def add_message_to_history(**kwargs):
        history.append(kwargs)

    await runner.run(
        {"agent": "research", "parameters": {"task": "look"}},
        [sub_agent],
        "s1",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    assert sub_agent.kwargs == {"task": "look", "session_id": "s1"}
    assert sub_agent.cleaned is True
    trace = await store.get_trace(context.trace_id)
    assert [event.event_type for event in trace.events] == [
        "subagent_spawn",
        "subagent_result",
    ]
    subagent_spans = [span for span in trace.spans if span.kind == "subagent.run"]
    assert len(subagent_spans) == 1
    assert subagent_spans[0].status == "ok"
    assert trace.events[0].span_id == subagent_spans[0].span_id
    assert trace.events[1].span_id == subagent_spans[0].span_id
    assert trace.events[0].metadata["subagent_span_id"] == subagent_spans[0].span_id
    assert trace.events[0].metadata["parent_trace_id"] == context.trace_id
    assert trace.events[0].metadata["parent_span_id"] == trace.root_span_id
    assert trace.events[1].metadata["spawn_event_id"] == trace.events[0].event_id
    assert subagent_spans[0].output["terminal_event_id"] == trace.events[1].event_id


@pytest.mark.asyncio
async def test_subagent_runner_returns_error_observation_for_failed_agent():
    runner = SubAgentCallRunner(agent_name="parent")
    failing_agent = FakeAgent("worker", RuntimeError("boom"))
    history = []
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-subagent-error",
        run_id="run-subagent-error",
        session_id="s1",
        actor=TelemetryActor(type="agent", name="parent"),
    )

    async def add_message_to_history(**kwargs):
        history.append(kwargs)

    await runner.run(
        {"agent": "worker", "parameters": {}},
        [failing_agent],
        "s1",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    assert failing_agent.cleaned is True
    trace = await store.get_trace(context.trace_id)
    assert [event.event_type for event in trace.events] == [
        "subagent_spawn",
        "subagent_error",
    ]
    subagent_spans = [span for span in trace.spans if span.kind == "subagent.run"]
    assert len(subagent_spans) == 1
    assert subagent_spans[0].status == "error"
    assert subagent_spans[0].error.message == "boom"
    assert trace.events[0].span_id == subagent_spans[0].span_id
    assert trace.events[1].span_id == subagent_spans[0].span_id


@pytest.mark.asyncio
async def test_subagent_runner_cleans_up_cancelled_agent():
    runner = SubAgentCallRunner(agent_name="parent")
    cancelling_agent = CancellingAgent("worker", None)
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-subagent-cancelled",
        run_id="run-subagent-cancelled",
        session_id="s1",
        actor=TelemetryActor(type="agent", name="parent"),
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.run(
            {"agent": "worker", "parameters": {"task": "stop"}},
            [cancelling_agent],
            "s1",
            telemetry_recorder=recorder,
        )
    await recorder.end_trace(status="cancelled")
    trace = await store.get_trace(context.trace_id)
    subagent_span = next((span for span in trace.spans if span.kind == "subagent.run"))
    assert cancelling_agent.cleaned is True
    assert subagent_span.status == "cancelled"
    assert [event.event_type for event in trace.events] == [
        "subagent_spawn",
        "subagent_error",
    ]
    assert all((event.span_id == subagent_span.span_id for event in trace.events))


def test_build_kwargs_ignores_extra_params_without_mutating_input():
    agent = FakeAgent("worker", "done")
    provided = {"task": "work", "session_id": "s1", "unused": "ignore"}
    kwargs = build_kwargs(agent, provided)
    assert kwargs == {"task": "work", "session_id": "s1"}
    assert provided == {"task": "work", "session_id": "s1", "unused": "ignore"}


@pytest.mark.asyncio
async def test_returned_child_error_marks_delegation_span_failed():
    child = FakeAgent("worker", {"status": "error", "response": "step limit"})
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        session_id="s", actor=TelemetryActor(type="agent", name="parent")
    )
    _, result = await SubAgentCallRunner("parent").run(
        {"agent": "worker", "parameters": {}}, [child], "s", telemetry_recorder=recorder
    )
    await recorder.end_trace()
    assert result["status"] == "error"
    trace = await store.get_trace(context.trace_id)
    assert next(s for s in trace.spans if s.kind == "subagent.run").status == "error"
    assert trace.events[-1].event_type == "subagent_error"
