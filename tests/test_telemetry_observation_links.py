from __future__ import annotations

import pytest

from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import InMemoryTelemetryStore, TelemetryRecorder
from omnicoreagent.core.token_usage import Usage, UsageLimits
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.types import AgentState, Message, SessionState
from omnicoreagent.core.agents.loop_detection import NativeLoopDetector

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "key"}


class ScriptedModel:
    """Plays back a fixed list of turns: tool-call tuples or answer text."""

    def __init__(self, *turns) -> None:
        self.turns = list(turns)

    async def llm_call(self, messages, tools=None, **kwargs):
        turn = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop")
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn),
            finish_reason="tool_calls",
        )


def _tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("lookup", description="Look up a value.")
    def lookup(key: str) -> dict:
        return {"key": key}

    return tools


async def _run(model, **config):
    agent = OmniCoreAgent(
        name="observation-agent",
        system_instruction="You are an observation probe.",
        model_config=_MODEL,
        local_tools=_tools(),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "max_steps": 10,
            **config,
        },
    )
    await agent.initialize()
    agent.llm_connection = model
    result = await agent.run("go", session_id="observation-links")
    return await agent.telemetry_store.get_trace(result["trace_id"])


def _events(trace, event_type):
    return [event for event in trace.events if event.event_type == event_type]


@pytest.mark.asyncio
async def test_observation_links_to_its_tool_result_and_span():
    trace = await _run(ScriptedModel([("call_1", "lookup", '{"key": "a"}')], "done"))

    [observation] = _events(trace, "tool_observation")
    [result] = _events(trace, "tool_result")
    [requested] = _events(trace, "tool_requested")
    [span] = [s for s in trace.spans if s.kind == "tool.call"]
    assert observation.metadata["tool_result_event_id"] == result.event_id
    assert observation.metadata["tool_span_id"] == span.span_id
    assert observation.metadata["tool_requested_event_id"] == requested.event_id


@pytest.mark.asyncio
async def test_next_model_turn_records_the_observations_it_received():
    trace = await _run(
        ScriptedModel(
            [("call_1", "lookup", '{"key": "a"}'), ("call_2", "lookup", '{"key": "b"}')],
            "done",
        )
    )

    observations = {o.metadata["tool_call_id"]: o.event_id for o in _events(trace, "tool_observation")}
    first_context, second_context = _events(trace, "context_assembly")
    first_call, second_call = _events(trace, "model_call")

    assert first_context.metadata["new_observation_event_ids"] == []
    assert first_call.metadata["new_observation_event_ids"] == []
    expected = [observations["call_1"], observations["call_2"]]
    assert second_context.metadata["observation_event_ids"] == expected
    assert second_context.metadata["new_observation_event_ids"] == expected
    assert second_call.metadata["new_observation_event_ids"] == expected


@pytest.mark.asyncio
async def test_observations_stay_in_context_but_are_new_only_once():
    trace = await _run(
        ScriptedModel(
            [("call_1", "lookup", '{"key": "a"}')],
            [("call_2", "lookup", '{"key": "b"}')],
            "done",
        )
    )

    observations = {o.metadata["tool_call_id"]: o.event_id for o in _events(trace, "tool_observation")}
    third_context = _events(trace, "context_assembly")[2]
    assert third_context.metadata["observation_event_ids"] == [
        observations["call_1"],
        observations["call_2"],
    ]
    assert third_context.metadata["new_observation_event_ids"] == [observations["call_2"]]


@pytest.mark.asyncio
async def test_rejected_call_observation_links_to_its_request():
    trace = await _run(ScriptedModel([("call_bad", "lookup", "{broken")], "done"))

    [observation] = _events(trace, "tool_observation")
    [requested] = _events(trace, "tool_requested")
    assert observation.metadata["tool_requested_event_id"] == requested.event_id
    assert observation.metadata["tool_result_event_id"] is None
    second_call = _events(trace, "model_call")[1]
    assert second_call.metadata["new_observation_event_ids"] == [observation.event_id]


def _message_digest_in_next_context(trace, runtime_event):
    later = [
        e
        for e in _events(trace, "context_assembly")
        if e.sequence_number > runtime_event.sequence_number
    ]
    from omnicoreagent.core.telemetry.context_record import context_assembly_digests

    return runtime_event.metadata["message_digest"] in context_assembly_digests(trace)[later[0].event_id]


@pytest.mark.asyncio
async def test_empty_response_retry_is_a_recorded_runtime_message():
    trace = await _run(ScriptedModel("", "done"))

    [runtime] = [
        e
        for e in _events(trace, "runtime_message")
        if e.metadata["kind"] == "empty_response_retry"
    ]
    assert runtime.metadata["role"] == "user"
    assert "previous response was empty" in runtime.metadata["content"]
    assert _message_digest_in_next_context(trace, runtime)


@pytest.mark.asyncio
async def test_loop_recovery_is_a_recorded_runtime_message():
    repeated = [("call_x", "lookup", '{"key": "same"}')]
    trace = await _run(ScriptedModel(*([repeated] * 5), "best answer"))

    [runtime] = [
        e
        for e in _events(trace, "runtime_message")
        if e.metadata["kind"] == "loop_recovery"
    ]
    assert "Tools are disabled" in runtime.metadata["content"]
    assert _message_digest_in_next_context(trace, runtime)


@pytest.mark.asyncio
async def test_datetime_prefix_is_a_recorded_runtime_message():
    trace = await _run(ScriptedModel("done"))

    [runtime] = [
        e
        for e in _events(trace, "runtime_message")
        if e.metadata["kind"] == "current_datetime"
    ]
    assert runtime.metadata["content"].startswith("[CURRENT_DATETIME:")
    assert _message_digest_in_next_context(trace, runtime)


@pytest.mark.asyncio
async def test_model_calls_record_their_purpose(monkeypatch):
    agent_trace = await _run(ScriptedModel("done"))
    assert {e.metadata["purpose"] for e in _events(agent_trace, "model_call")} == {
        "agent_turn"
    }

    monkeypatch.setattr(llm_step, "usage", Usage())

    class Summarizing:
        def should_trigger(self, messages):
            return True

        async def manage_context(self, *, messages, summarize_fn):
            return [Message(role="system", content=await summarize_fn(messages))]

        def get_stats(self):
            return {}

    class Connection:
        async def llm_call(self, messages, tools=None):
            return "summary" if isinstance(messages[0], dict) else "done"

    recorder = TelemetryRecorder(InMemoryTelemetryStore())
    await recorder.start_trace(trace_id="trace-purpose")
    await AgentLlmStepRunner(
        agent_name="agent",
        context_manager=Summarizing(),
        usage_limits=UsageLimits(request_limit=0, total_tokens_limit=0),
        limits_enabled=False,
        request_limit=0,
    ).run(
        session_state=SessionState(
            messages=[Message(role="user", content="hello")],
            state=AgentState.IDLE,
            loop_detector=NativeLoopDetector(),
            assistant_with_tool_calls=None,
            pending_tool_responses=[],
        ),
        llm_connection=Connection(),
        run_usage=Usage(),
        session_id="purpose",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await recorder.store.get_trace("trace-purpose")
    purposes = [e.metadata["purpose"] for e in _events(trace, "model_call")]
    assert purposes == ["context_summary", "agent_turn"]
    spans = [s for s in trace.spans if s.kind == "model.call"]
    assert [s.attributes["purpose"] for s in spans] == ["context_summary", "agent_turn"]
