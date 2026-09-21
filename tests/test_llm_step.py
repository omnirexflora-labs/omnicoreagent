from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.telemetry import (
    ActorType,
    CaptureState,
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryActor,
    TelemetryRecorder,
)
from omnicoreagent.core.token_usage import Usage, UsageLimits
from omnicoreagent.core.types import AgentState, Message, SessionState
from omnicoreagent.core.agents.loop_detection import NativeLoopDetector


def make_session_state(messages=None):
    return SessionState(
        messages=messages or [Message(role="user", content="hello")],
        state=AgentState.IDLE,
        loop_detector=NativeLoopDetector(),
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
        async def llm_call(self, messages, tools=None):
            return {
                "choices": [
                    {"message": {"content": "done"}}
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

    assert result.response.text == "done"
    assert result.error_result is None
    assert run_usage.requests == 1
    assert run_usage.total_tokens == 7


@pytest.mark.asyncio
async def test_llm_step_manages_context_before_model_call(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    calls = []

    class LlmConnection:
        async def llm_call(self, messages, tools=None):
            calls.append(messages)
            if isinstance(messages[0], dict):
                return "summary"
            return "done"

    session_state = make_session_state()
    result = await make_runner(TriggeringContextManager()).run(
        session_state=session_state,
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat1",
    )

    assert result.response.text == "done"
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
        async def llm_call(self, messages, tools=None):
            if isinstance(messages[0], dict):
                return "summary"
            return "done"

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
    assert result.response.text == "done"
    assert session_state.messages == [Message(role="system", content="summary")]
    assert {span.kind for span in trace.spans} >= {
        "context.compression",
        "model.call",
    }
    event = next(
        event for event in trace.events if event.event_type == "context_compression"
    )
    assert event.input["message_count"] == 1
    assert event.input["context_digest"]
    assert len(event.input["message_digests"]) == 1
    assert event.output["before"]["message_count"] == 1
    assert event.output["after"]["message_count"] == 1
    assert len(event.output["dropped_message_digests"]) == 1
    assert event.output["stats"] == {"compressions": 1}
    assembly = next(
        event for event in trace.events if event.event_type == "context_assembly"
    )
    assert assembly.output["context_digest"]
    assert assembly.output["message_count"] == 1
    assert assembly.output["role_counts"] == {"system": 1}
    assert sum(event.event_type == "model_call" for event in trace.events) == 2


@pytest.mark.asyncio
async def test_llm_step_context_capture_respects_prompt_policy(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(record_model_prompts=True))
    context = await recorder.start_trace(trace_id="trace-context-capture")

    class LlmConnection:
        async def llm_call(self, messages, tools=None):
            return "done"

    result = await make_runner().run(
        session_state=make_session_state(),
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat-context",
        telemetry_recorder=recorder,
        tools=[
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object"}},
            }
        ],
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    assert result.response.text == "done"
    # The prompt is recorded once, on the model call; the context assembly
    # records its digests (telemetry storage plan, T1).
    model_call = next(span for span in trace.spans if span.kind == "model.call")
    assert model_call.input["messages"][0]["content"] == "hello"
    assert model_call.input["tools"][0]["function"]["name"] == "lookup"
    assert model_call.input_capture.state == CaptureState.AVAILABLE
    assembly = next(
        event for event in trace.events if event.event_type == "context_assembly"
    )
    assert "messages" not in assembly.input and assembly.input["message_digests"]


@pytest.mark.asyncio
async def test_llm_step_records_bounded_provider_stream_statistics(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(trace_id="trace-stream-stats")
    delivered = []

    class LlmConnection:
        async def llm_stream(self, messages, tools=None):
            yield {"type": "text_delta", "text": "hello"}
            yield {"type": "text_delta", "text": " world"}
            yield {"type": "turn_complete", "turn": "hello world"}

    async def on_event(event):
        delivered.append(event)

    result = await make_runner().run(
        session_state=make_session_state(),
        llm_connection=LlmConnection(),
        run_usage=Usage(),
        session_id="chat-stream",
        telemetry_recorder=recorder,
        on_event=on_event,
    )
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    model_span = next(span for span in trace.spans if span.kind == "model.call")
    model_call = next(event for event in trace.events if event.event_type == "model_call")
    assert result.response.text == "hello world"
    assert len(delivered) == 2
    assert model_call.metadata["streaming"] is True
    assert model_span.output["stream_stats"] == {
        "streaming": True,
        "delta_count": 2,
        "visible_text_bytes": 11,
        "event_types": {"text_delta": 2},
    }


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
async def test_llm_step_records_usage_limit_as_resource_guard_halt(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(
        trace_id="trace-resource-guard",
        run_id="run-resource-guard",
        session_id="chat-resource",
        actor=TelemetryActor(type=ActorType.AGENT, name="agent"),
    )

    result = await make_runner(limits_enabled=True, request_limit=1).run(
        session_state=make_session_state(),
        llm_connection=SimpleNamespace(llm_call=None),
        run_usage=Usage(requests=1),
        session_id="chat-resource",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace(status="failed")

    trace = await store.get_trace(context.trace_id)
    assert result.response is None
    assert result.error_result["answer"].startswith("Usage limit error:")
    assert any(span.kind == "runtime.control" for span in trace.spans)
    event = next(
        event for event in trace.events if event.event_type == "resource_guard_halt"
    )
    assert event.error.type == "UsageLimitExceeded"


@pytest.mark.asyncio
async def test_llm_step_returns_model_error(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())

    class LlmConnection:
        async def llm_call(self, messages, tools=None):
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
