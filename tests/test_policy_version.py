"""Which policy produced a run.

Traces for training plan, R2. Training from production runs reuses
trajectories a slightly older policy produced, so a trainer has to know
which one: rLLM's runs collapsed from 43% to 4% when the mismatch between
the policy that served a run and the one being trained went uncorrected.
The provider's own answer says what served the call: the model it used, its
fingerprint, and the version of the weights when the server reports one.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.agents import llm_step
from omnicoreagent.core.telemetry import InMemoryTelemetryStore, TelemetryRecorder
from omnicoreagent.core.token_usage import Usage
from test_llm_step import make_runner, make_session_state

SERVED = {
    "id": "resp_1",
    "model": "qwen3-coder-30b-instruct",
    "system_fingerprint": "fp_2026_09_22_a1b2",
    "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
}


async def _facts(monkeypatch, response):
    monkeypatch.setattr(llm_step, "usage", Usage())
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store)
    context = await recorder.start_trace(trace_id="trace-policy")

    class Served:
        async def llm_call(self, messages, tools=None):
            return response

    await make_runner().run(
        session_state=make_session_state(),
        llm_connection=Served(),
        run_usage=Usage(),
        session_id="policy",
        telemetry_recorder=recorder,
    )
    await recorder.end_trace()
    trace = await store.get_trace(context.trace_id)
    (event,) = [e for e in trace.events if e.event_type == "model_response"]
    return event.metadata["model_call"]


@pytest.mark.asyncio
async def test_a_model_call_records_what_served_it(monkeypatch):
    facts = await _facts(monkeypatch, SERVED)

    assert facts["policy_version"] == {
        "model": "qwen3-coder-30b-instruct",
        "fingerprint": "fp_2026_09_22_a1b2",
    }


@pytest.mark.asyncio
async def test_a_provider_that_says_less_records_what_it_says(monkeypatch):
    quiet = {k: v for k, v in SERVED.items() if k != "system_fingerprint"}

    facts = await _facts(monkeypatch, quiet)

    assert facts["policy_version"] == {"model": "qwen3-coder-30b-instruct"}
