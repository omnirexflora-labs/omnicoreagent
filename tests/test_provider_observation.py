from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from engineering.validation.provider_observation import observe_litellm
from omnicoreagent.core.llm import LLMConnection


@pytest.mark.asyncio
async def test_observation_uses_production_routing_and_restores_on_failure(monkeypatch):
    original = LLMConnection.llm_call
    create = AsyncMock(return_value={"choices": []})
    provider = SimpleNamespace(acompletion=create, completion=Mock())
    monkeypatch.setattr(
        "engineering.validation.provider_observation._get_litellm", lambda: provider
    )
    monkeypatch.setattr("omnicoreagent.core.llm._get_litellm", lambda: provider)
    connection = LLMConnection(
        {"provider": "openai", "model": "gpt-5.6-luna"}, api_key="test-key"
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        with observe_litellm() as counters:
            assert LLMConnection.llm_call is original
            await connection.llm_call([])
            assert create.call_args.kwargs["model"] == "openai/gpt-5.6-luna"
            assert counters["complete_requests"] == 1
            raise RuntimeError("synthetic")
    assert provider.acompletion is create
    assert LLMConnection.llm_call is original
