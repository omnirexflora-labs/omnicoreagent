"""``model_config["base_url"]`` points the model client at another endpoint.

An OpenAI-compatible server (vLLM, LM Studio, a gateway, a proxy) is reached by
naming the provider whose API it speaks and its URL. The URL goes to LiteLLM
as ``api_base`` on every call, streamed or not, and only on this agent's calls:
it is not written into the process environment, so two agents in one process
can use two endpoints.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from omnicoreagent.core.llm import LLMConnection

URL = "http://localhost:8000/v1"


def _connection(**extra) -> LLMConnection:
    return LLMConnection({"provider": "openai", "model": "my-model", **extra}, api_key="k")


def test_base_url_is_sent_as_api_base():
    params = _connection(base_url=URL)._completion_params([])
    assert params["api_base"] == URL
    assert params["model"] == "openai/my-model"


def test_without_base_url_nothing_is_added():
    assert "api_base" not in _connection()._completion_params([])


def test_two_agents_two_endpoints_and_no_environment_change():
    before = dict(os.environ)
    first = _connection(base_url=URL)
    second = _connection(base_url="http://other:9000/v1")
    assert first._completion_params([])["api_base"] == URL
    assert second._completion_params([])["api_base"] == "http://other:9000/v1"
    assert dict(os.environ) == before


def test_base_url_serves_ollama_too():
    connection = LLMConnection(
        {"provider": "ollama", "model": "llama3.1:8b", "base_url": "http://gpu-box:11434"}
    )
    assert connection._completion_params([])["api_base"] == "http://gpu-box:11434"


@pytest.mark.asyncio
async def test_a_streamed_call_goes_to_the_same_endpoint():
    seen: list[dict] = []

    async def acompletion(**params):
        seen.append(params)
        raise RuntimeError("stop here")

    with patch("litellm.acompletion", acompletion):
        with pytest.raises(Exception):
            async for _ in _connection(base_url=URL).llm_stream(
                [{"role": "user", "content": "hi"}]
            ):
                pass
    assert seen and seen[0]["api_base"] == URL


def test_model_config_dataclass_accepts_base_url():
    from omnicoreagent.core.runtime.config import ModelConfig, normalize_model_config

    data = normalize_model_config(ModelConfig(provider="openai", model="m", base_url=URL))
    assert data["base_url"] == URL


def test_the_endpoint_is_not_recorded_with_the_model_settings():
    # A URL can carry a token (a gateway's query string); the run's recorded
    # settings keep generation settings only.
    from omnicoreagent.core.runtime.omnicore_agent import _model_settings

    settings = _model_settings({"provider": "openai", "model": "m", "base_url": URL, "temperature": 0.2})
    assert settings == {"temperature": 0.2}
