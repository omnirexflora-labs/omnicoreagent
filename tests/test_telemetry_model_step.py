from __future__ import annotations

import asyncio
import json

import pytest

from omnicoreagent.core import llm as llm_module
from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
)
from omnicoreagent.core.token_usage import Usage, UsageLimits
from omnicoreagent.core.types import AgentState, Message, SessionState
from omnicoreagent.core.agents.loop_detection import NativeLoopDetector

_SETTINGS = {"model": "openai/gpt-test", "temperature": 0.1, "max_tokens": 256}


def _session():
    return SessionState(
        messages=[Message(role="user", content="hello")],
        state=AgentState.IDLE,
        loop_detector=NativeLoopDetector(),
        assistant_with_tool_calls=None,
        pending_tool_responses=[],
    )


class _NoContextManager:
    def should_trigger(self, messages):
        return False


def _runner():
    return AgentLlmStepRunner(
        agent_name="agent",
        context_manager=_NoContextManager(),
        usage_limits=UsageLimits(request_limit=0, total_tokens_limit=0),
        limits_enabled=False,
        request_limit=0,
    )


def _provider_response(*, arguments='{"key": "a"}'):
    """The shape LiteLLM returns for a non-streaming chat completion."""
    return {
        "id": "chatcmpl-test-123",
        "model": "gpt-test-2026-09-01",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 64},
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }


class _Connection:
    def __init__(self, response=None):
        self.response = response or _provider_response()

    def request_settings(self):
        return dict(_SETTINGS)

    async def llm_call(self, messages, tools=None):
        await asyncio.sleep(0.01)
        return self.response


async def _step(recorder, connection, **kwargs):
    await recorder.start_trace(trace_id="trace-model-step")
    await _runner().run(
        session_state=_session(),
        llm_connection=connection,
        run_usage=Usage(),
        session_id="model-step",
        telemetry_recorder=recorder,
        **kwargs,
    )
    await recorder.end_trace()
    trace = await recorder.store.get_trace("trace-model-step")
    response = next(e for e in trace.events if e.event_type == "model_response")
    span = next(s for s in trace.spans if s.kind == "model.call")
    return trace, response, span


@pytest.mark.asyncio
async def test_model_step_records_tokens_identity_settings_and_latency(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    _, response, span = await _step(recorder, _Connection())
    facts = response.metadata["model_call"]

    assert facts["tokens"] == {
        "input": 120,
        "output": 30,
        "total": 150,
        "cached_input": 64,
        "reasoning": 12,
    }
    assert facts["provider_response_id"] == "chatcmpl-test-123"
    assert facts["provider_model"] == "gpt-test-2026-09-01"
    assert facts["request_settings"] == _SETTINGS
    assert facts["finish_reason"] == "tool_calls"
    assert facts["refused"] is False
    assert facts["attempts"] == 1
    assert facts["retries"] == []
    assert facts["latency_ms"] >= 10
    assert facts["time_to_first_delta_ms"] is None
    assert span.output["tokens"] == facts["tokens"]
    assert span.output["latency_ms"] == facts["latency_ms"]


@pytest.mark.asyncio
async def test_model_step_facts_survive_when_outputs_are_not_recorded(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(
        InMemoryTelemetryStore(), TelemetryConfig(record_outputs=False)
    )

    _, response, span = await _step(recorder, _Connection())

    assert response.output is None
    assert span.output is None
    assert response.metadata["model_call"]["tokens"]["total"] == 150
    assert response.metadata["model_call"]["provider_response_id"] == "chatcmpl-test-123"


@pytest.mark.asyncio
async def test_raw_tool_call_arguments_are_kept_exactly_under_full_capture(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(InMemoryTelemetryStore(), TelemetryConfig(capture="full"))
    raw = '{"key": "a",  "limit": 5}'

    _, response, _ = await _step(recorder, _Connection(_provider_response(arguments=raw)))

    [call] = response.output["tool_calls"]
    assert call["function"]["arguments"] == raw


@pytest.mark.asyncio
async def test_refusal_text_stays_behind_the_response_capture_policy(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    refusal = _provider_response()
    refusal["choices"][0] = {
        "finish_reason": "stop",
        "message": {"content": None, "refusal": "I cannot share that private detail."},
    }
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    trace, response, span = await _step(recorder, _Connection(refusal))

    assert response.metadata["model_call"]["refused"] is True
    assert span.output["refused"] is True
    assert "private detail" not in json.dumps(trace.model_dump(), default=str)


@pytest.mark.asyncio
async def test_streaming_step_records_time_to_first_streamed_delta(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    class StreamingConnection(_Connection):
        async def llm_stream(self, messages, tools=None):
            from omnicoreagent.core.model_stream import ModelStreamAssembler

            assembler = ModelStreamAssembler()
            chunks = [
                {"id": "chatcmpl-stream-9", "model": "gpt-test-2026-09-01",
                 "choices": [{"index": 0, "delta": {"content": "hel"}}]},
                {"id": "chatcmpl-stream-9", "model": "gpt-test-2026-09-01",
                 "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
                {"id": "chatcmpl-stream-9", "model": "gpt-test-2026-09-01", "choices": [],
                 "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
            ]
            await asyncio.sleep(0.02)
            for chunk in chunks:
                for event in assembler.feed(chunk):
                    yield event
                await asyncio.sleep(0.01)
            yield {"type": "turn_complete", "turn": assembler.finish()}

    async def on_event(event):
        return None

    _, response, _ = await _step(recorder, StreamingConnection(), on_event=on_event)
    facts = response.metadata["model_call"]

    assert facts["tokens"]["total"] == 9
    assert facts["provider_response_id"] == "chatcmpl-stream-9"
    assert facts["provider_model"] == "gpt-test-2026-09-01"
    assert 20 <= facts["time_to_first_delta_ms"] <= facts["latency_ms"]


@pytest.mark.asyncio
async def test_provider_retries_are_recorded_on_the_model_call(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    monkeypatch.setattr(llm_module, "_retry_delay", lambda *args, **kwargs: 0)
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    class FlakyConnection(_Connection):
        def __init__(self):
            super().__init__()
            self.failures = 2

        @llm_module.retry_with_backoff(max_retries=3, base_delay=0)
        async def llm_call(self, messages, tools=None):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("429 rate limit reached for api_key=sk-hidden")
            return self.response

    _, response, span = await _step(recorder, FlakyConnection())
    facts = response.metadata["model_call"]

    assert facts["attempts"] == 3
    assert [retry["error_type"] for retry in facts["retries"]] == [
        "RuntimeError",
        "RuntimeError",
    ]
    assert all("sk-hidden" not in retry["message"] for retry in facts["retries"])
    assert span.output["attempts"] == 3


@pytest.mark.asyncio
async def test_failed_model_call_records_its_attempts(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    monkeypatch.setattr(llm_module, "_retry_delay", lambda *args, **kwargs: 0)
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    class DownConnection(_Connection):
        @llm_module.retry_with_backoff(max_retries=1, base_delay=0)
        async def llm_call(self, messages, tools=None):
            raise RuntimeError("connection reset by provider")

    await recorder.start_trace(trace_id="trace-model-down")
    await _runner().run(
        session_state=_session(),
        llm_connection=DownConnection(),
        run_usage=Usage(),
        session_id="model-down",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await recorder.store.get_trace("trace-model-down")
    error = next(e for e in trace.events if e.event_type == "model_error")

    assert error.metadata["model_call"]["attempts"] == 2
    assert len(error.metadata["model_call"]["retries"]) == 1
    assert error.metadata["model_call"]["request_settings"] == _SETTINGS


@pytest.mark.asyncio
async def test_provider_cost_and_standard_usage_fields_are_populated(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    priced = _provider_response()
    priced["_hidden_params"] = {"response_cost": 0.00042}
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    _, response, span = await _step(recorder, _Connection(priced))
    facts = response.metadata["model_call"]

    assert facts["estimated_cost_usd"] == 0.00042
    assert facts["cost_source"] == "provider_response"
    for record in (span, response):
        assert record.token_usage.prompt_tokens == 120
        assert record.token_usage.completion_tokens == 30
        assert record.token_usage.total_tokens == 150
        assert record.estimated_cost_usd == 0.00042


@pytest.mark.asyncio
async def test_price_table_cost_is_used_when_the_provider_reports_none(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    class PricedConnection(_Connection):
        def estimate_cost(self, usage):
            return round(usage.request_tokens * 1e-6 + usage.response_tokens * 4e-6, 9)

    _, response, span = await _step(recorder, PricedConnection())
    facts = response.metadata["model_call"]

    assert facts["cost_source"] == "price_table"
    assert facts["estimated_cost_usd"] == pytest.approx(120e-6 + 120e-6)
    assert span.estimated_cost_usd == facts["estimated_cost_usd"]


@pytest.mark.asyncio
async def test_unknown_cost_stays_unknown(monkeypatch):
    monkeypatch.setattr(llm_step, "usage", Usage())
    recorder = TelemetryRecorder(InMemoryTelemetryStore())

    _, response, span = await _step(recorder, _Connection())

    assert response.metadata["model_call"]["estimated_cost_usd"] is None
    assert response.metadata["model_call"]["cost_source"] is None
    assert span.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_standard_usage_fields_survive_jsonl_reload_and_reach_otel(
    monkeypatch, tmp_path
):
    from omnicoreagent.core.telemetry import JsonlTelemetryStore, OTelTraceMapper

    monkeypatch.setattr(llm_step, "usage", Usage())
    priced = _provider_response()
    priced["_hidden_params"] = {"response_cost": 0.00042}
    path = tmp_path / "traces.jsonl"
    recorder = TelemetryRecorder(JsonlTelemetryStore(path))

    await _step(recorder, _Connection(priced))
    reloaded = await JsonlTelemetryStore(path).get_trace("trace-model-step")
    span = next(s for s in reloaded.spans if s.kind == "model.call")
    assert span.token_usage.total_tokens == 150
    assert span.estimated_cost_usd == 0.00042

    [model_span] = [
        record
        for record in OTelTraceMapper().map_trace(reloaded)
        if record.name == "model.call"
    ]
    assert model_span.attributes["gen_ai.usage.input_tokens"] == 120
    assert model_span.attributes["gen_ai.usage.output_tokens"] == 30
    assert model_span.attributes["omnicoreagent.estimated_cost_usd"] == 0.00042


def test_price_table_estimate_uses_the_cached_input_rate():
    from omnicoreagent.core.llm import LLMConnection

    connection = LLMConnection.__new__(LLMConnection)
    connection.llm_config = {"model": "openai/gpt-5.4-mini"}
    uncached = Usage(request_tokens=1553, response_tokens=11, total_tokens=1564)
    cached = Usage(
        request_tokens=1553,
        response_tokens=11,
        total_tokens=1564,
        details={"cached_input_tokens": 1024},
    )

    assert connection.estimate_cost(cached) < connection.estimate_cost(uncached)
