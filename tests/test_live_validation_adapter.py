"""The opt-in adapter must not bypass LiteLLM or leak process-wide patches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engineering.validation.litellm_adapter import use_litellm
from omnicoreagent.core.llm import LLMConnection


@pytest.mark.asyncio
async def test_validation_routes_calls_and_restores_runtime_after_failure(monkeypatch):
    original = LLMConnection.llm_call
    create = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(
        "engineering.validation.litellm_adapter._get_litellm",
        lambda: SimpleNamespace(acompletion=create),
    )
    connection = LLMConnection(
        {"provider": "openai", "model": "gpt-5.6-luna", "max_tokens": 100},
        api_key="test-key",
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with use_litellm() as counters:
            await connection.llm_call([{"role": "user", "content": "hello"}])
            params = create.call_args.kwargs
            assert params["model"] == "openai/gpt-5.6-luna"
            assert params["api_key"] == "test-key"
            assert params["max_completion_tokens"] == 100
            assert params["drop_params"] is False
            assert params["num_retries"] == 0
            assert counters["complete_requests"] == 1
            from omnicoreagent.core import llm

            with pytest.raises(AssertionError, match="bypassed LiteLLM"):
                llm._get_openai()
            raise RuntimeError("synthetic failure")
    assert LLMConnection.llm_call is original


@pytest.mark.asyncio
async def test_validation_stream_is_live_and_closes_upstream(monkeypatch):
    closed = []

    async def chunks():
        try:
            yield {"choices": [{"delta": {"content": "first"}}]}
            raise AssertionError("consumer should have closed after first text")
        finally:
            closed.append(True)

    monkeypatch.setattr(
        "engineering.validation.litellm_adapter._get_litellm",
        lambda: SimpleNamespace(acompletion=AsyncMock(return_value=chunks())),
    )
    connection = LLMConnection(
        {"provider": "openai", "model": "gpt-5.6-luna"}, api_key="test-key"
    )
    with use_litellm() as counters:
        stream = connection.llm_stream([])
        assert await anext(stream) == {"type": "text_delta", "text": "first"}
        await stream.aclose()
        assert counters["stream_requests"] == counters["closed_streams"] == 1
    assert closed == [True]
