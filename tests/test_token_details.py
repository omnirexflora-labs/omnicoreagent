"""The tokens the model chose, and how likely they were.

Traces for training plan, R3. Reusing a production run to train the policy
that produced it needs the probability of each token it chose: rLLM's async
runs collapsed to 4% without that correction. Providers return it only when
asked, and a hosted reasoning model usually refuses, so this is a model
setting (``logprobs``) and an opt-in recording
(``telemetry_config.record_token_details``): the arrays are large.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.telemetry import (
    InMemoryTelemetryStore,
    TelemetryConfig,
    TelemetryRecorder,
)
from omnicoreagent.core.token_usage import Usage
from test_llm_step import make_runner, make_session_state

WITH_LOGPROBS = {
    "id": "resp_1",
    "model": "qwen3-coder-30b",
    "choices": [
        {
            "message": {"content": "done"},
            "finish_reason": "stop",
            "logprobs": {
                "content": [
                    {"token": "token_id:2375", "logprob": -0.031, "bytes": [100, 111]},
                    {"token": "token_id:1", "logprob": -1.2, "bytes": [110, 101]},
                ]
            },
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


def test_logprobs_are_a_model_setting_the_provider_is_asked_for():
    from omnicoreagent.core.llm import LLMConnection

    connection = LLMConnection(
        {"provider": "openai", "model": "qwen3-coder-30b", "api_key": "k", "logprobs": True, "top_logprobs": 5}
    )

    params = connection._completion_params([{"role": "user", "content": "hi"}])

    assert params["logprobs"] is True and params["top_logprobs"] == 5


async def _response_event(monkeypatch, **telemetry):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(**telemetry))
    context = await recorder.start_trace(trace_id="trace-tokens")

    class Served:
        async def llm_call(self, messages, tools=None):
            return WITH_LOGPROBS

    await make_runner().run(
        session_state=make_session_state(),
        llm_connection=Served(),
        run_usage=Usage(),
        session_id="tokens",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    return next(e for e in trace.events if e.event_type == "model_response")


@pytest.mark.asyncio
async def test_token_details_are_recorded_when_asked_for(monkeypatch):
    event = await _response_event(monkeypatch, record_token_details=True)

    tokens = event.output["token_details"]["content"]
    assert [t["token"] for t in tokens] == ["token_id:2375", "token_id:1"]
    assert tokens[0]["logprob"] == -0.031


@pytest.mark.asyncio
async def test_token_details_are_left_out_by_default(monkeypatch):
    event = await _response_event(monkeypatch)

    assert "token_details" not in (event.output or {})
    assert TelemetryConfig().record_token_details is False
