from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.token_usage import Usage, UsageLimits
from omnicoreagent.core.types import AgentState, Message, SessionState
from omnicoreagent.core.agents.loop_detection import RobustLoopDetector


def make_session_state(messages=None):
    return SessionState(
        messages=messages or [Message(role="user", content="hello")],
        state=AgentState.IDLE,
        loop_detector=RobustLoopDetector(debug=False),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


class NoContextManager:
    def should_trigger(self, messages):
        return False


class TriggeringContextManager:
    def should_trigger(self, messages):
        return True

    async def manage_context(self, *, messages, summarize_fn):
        summary = await summarize_fn(messages)
        return [Message(role="system", content=summary)]

    def get_stats(self):
        return {"compressions": 1}


def make_runner(context_manager=None, *, limits_enabled=False, request_limit=0):
    return AgentLlmStepRunner(
        agent_name="agent",
        context_manager=context_manager or NoContextManager(),
        usage_limits=UsageLimits(
            request_limit=request_limit,
            total_tokens_limit=1000,
        ),
        limits_enabled=limits_enabled,
        request_limit=request_limit,
    )


@pytest.mark.asyncio
async def test_llm_step_calls_model_and_records_usage(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())

    class LlmConnection:
        async def llm_call(self, messages):
            return {
                "choices": [
                    {"message": {"content": "<final_answer>done</final_answer>"}}
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                },
            }

    run_usage = Usage()
    result = await make_runner().run(
        session_state=make_session_state(),
        llm_connection=LlmConnection(),
        run_usage=run_usage,
        session_id="chat1",
    )

    assert result.response == "<final_answer>done</final_answer>"
    assert result.error_result is None
    assert run_usage.requests == 1
    assert run_usage.total_tokens == 7


@pytest.mark.asyncio
async def test_llm_step_manages_context_before_model_call(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    calls = []

    class LlmConnection:
        async def llm_call(self, messages):
            calls.append(messages)
            if isinstance(messages[0], dict):
                return "summary"
            return "<final_answer>done</final_answer>"

    session_state = make_session_state()
    result = await make_runner(TriggeringContextManager()).run(
        session_state=session_state,
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat1",
    )

    assert result.response == "<final_answer>done</final_answer>"
    assert session_state.messages == [Message(role="system", content="summary")]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_llm_step_records_context_compression_telemetry(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-context-compression",
        run_id="run-context-compression",
        session_id="chat-context",
        actor=TelemetryActor(type=ActorType.AGENT, name="agent"),
    )

    class LlmConnection:
        async def llm_call(self, messages):
            if isinstance(messages[0], dict):
                return "summary"
            return "<final_answer>done</final_answer>"

    session_state = make_session_state()
    result = await make_runner(TriggeringContextManager()).run(
        session_state=session_state,
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat-context",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert result.response == "<final_answer>done</final_answer>"
    assert session_state.messages == [Message(role="system", content="summary")]
    assert {span.kind for span in trace.spans} >= {
        "context.compression",
        "model.call",
    }
    event = next(
        event for event in trace.events if event.event_type == "context_compression"
    )
    assert event.input == {"message_count": 1}
    assert event.output["message_count"] == 1
    assert event.output["stats"] == {"compressions": 1}


@pytest.mark.asyncio
async def test_llm_step_returns_usage_limit_error(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    runner = make_runner(limits_enabled=True, request_limit=1)

    result = await runner.run(
        session_state=make_session_state(),
        llm_connection=SimpleNamespace(llm_call=None),
        run_usage=Usage(requests=1),
        session_id="chat1",
    )

    assert result.response is None
    assert result.error_result["answer"].startswith("Usage limit error:")


@pytest.mark.asyncio
async def test_llm_step_returns_model_error(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())

    class LlmConnection:
        async def llm_call(self, messages):
            raise RuntimeError("provider down")

    result = await make_runner().run(
        session_state=make_session_state(),
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat1",
    )

    assert result.response is None
    assert result.error_result["answer"] == (
        "Model encountered an error, please do retry again"
    )
    assert isinstance(result.error_result["usage"], Usage)
