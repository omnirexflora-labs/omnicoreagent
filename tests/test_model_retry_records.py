"""A retry the runtime handled must not become a failed run.

Found running a real trial: an agent with ``temperature`` set against a
reasoning model. The provider refuses the parameter, the runtime does the right
thing — drops it, retries, and records the retry — and then the recording
itself raised ``KeyError: 'message'``, because the two places that write a
retry record write different shapes:

    _notify_retry       attempt, error_type, message, delay_seconds
    _notify_unsupported attempt, reason, dropped, error

and the telemetry that reads them asked for ``message``. The run ended as
``provider_error`` with nothing in the trace saying why: a model call that had
in fact succeeded on its second attempt.

So: the records have one shape, and building the facts for a model call cannot
fail the call whatever a record holds.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.llm import MODEL_RETRY_OBSERVER, _notify_retry, _notify_unsupported


def _record(notify) -> dict:
    seen: list[dict] = []
    token = MODEL_RETRY_OBSERVER.set(seen.append)
    try:
        notify()
    finally:
        MODEL_RETRY_OBSERVER.reset(token)
    assert len(seen) == 1
    return seen[0]


def test_every_retry_record_says_what_happened():
    """Both kinds carry the same keys, so a reader never has to ask which."""
    failed = _record(lambda: _notify_retry(1, RuntimeError("rate limited"), 0.5))
    refused = _record(
        lambda: _notify_unsupported(
            "temperature", RuntimeError("model doesn't support temperature=0.2")
        )
    )

    for record in (failed, refused):
        assert record["attempt"] >= 1
        assert record["message"], f"a retry record without a message: {record}"

    assert "rate limited" in failed["message"]
    assert failed["error_type"] == "RuntimeError"
    assert refused["dropped"] == ["temperature"]
    assert refused["reason"] == "unsupported_parameter"
    assert "temperature" in refused["message"]


@pytest.mark.asyncio
async def test_a_refused_parameter_is_dropped_retried_and_recorded(monkeypatch):
    """The whole path: refuse, drop, retry, record — and the run goes on."""
    from omnicoreagent.core import llm as llm_module

    class UnsupportedParamsError(Exception):
        pass

    calls: list[dict] = []

    class FakeLiteLLM:
        async def acompletion(self, **params):
            calls.append(params)
            if "temperature" in params:
                raise UnsupportedParamsError(
                    "gpt-test doesn't support temperature=0.2 while reasoning is active"
                )
            return {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm_module, "_get_litellm", lambda: FakeLiteLLM())
    connection = llm_module.LLMConnection(
        {
            "provider": "openai",
            "model": "gpt-test",
            "api_key": "test-key",
            "temperature": 0.2,
        }
    )

    seen: list[dict] = []
    token = MODEL_RETRY_OBSERVER.set(seen.append)
    try:
        response = await connection.llm_call([{"role": "user", "content": "hello"}])
    finally:
        MODEL_RETRY_OBSERVER.reset(token)

    assert response is not None
    assert len(calls) == 2, "the refused parameter was not retried without it"
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    assert [record["message"] for record in seen] and all(
        record.get("message") for record in seen
    )


@pytest.mark.asyncio
async def test_recording_a_model_call_cannot_fail_the_call(tmp_path):
    """Telemetry does not decide whether a run succeeded.

    A record the facts builder does not understand is described as best it can
    be, and the model call still returns what the model said.
    """
    from test_execute_tool import ScriptedModel, _MODEL
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    class RetryingModel(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            observer = MODEL_RETRY_OBSERVER.get()
            if observer is not None:
                # A shape no reader has seen before.
                observer({"attempt": 1, "something": "unexpected"})
            return await super().llm_call(messages, tools=tools, **kwargs)

    agent = OmniCoreAgent(
        name="retry-facts",
        system_instruction="Answer.",
        model_config=_MODEL,
        agent_config={"enable_workspace_files": False},
        telemetry_config={
            "capture": "full",
            "storage": "jsonl",
            "storage_path": str(tmp_path / "traces.jsonl"),
        },
    )
    await agent.initialize()
    agent.llm_connection = RetryingModel([], "the answer")
    try:
        result = await agent.run("go", session_id="retry-facts")
    finally:
        await agent.cleanup()

    assert result["status"] == "success", result.get("response")
    assert result["response"] == "the answer"

    # And the trace says the description was the thing that went wrong, not
    # the call, if anything did.
    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    calls = [
        event for event in trace.events if event.event_type == "model_call"
    ]
    assert calls, "a model call left no event"
    facts = calls[0].metadata or {}
    assert "facts_error" not in facts, facts.get("facts_error")
