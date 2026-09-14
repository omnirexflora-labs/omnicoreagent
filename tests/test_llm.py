from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from omnicoreagent.core.llm import LLMConnection


def make_model_config(provider="openai", model="gpt-4"):
    return {
        "provider": provider,
        "model": model,
        "temperature": 0.7,
        "max_tokens": 1000,
        "top_p": 0.9,
    }


@pytest.fixture
def mock_llm_connection():
    return LLMConnection(make_model_config(), api_key="test-api-key")


class TestLLMConnection:
    def test_initialization(self, mock_llm_connection):
        cfg = mock_llm_connection.llm_config
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "openai/gpt-4"
        assert cfg["temperature"] == 0.7

    def test_llm_configuration_returns_expected_keys(self, mock_llm_connection):
        config = mock_llm_connection.llm_config
        assert set(config) >= {
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "top_p",
        }

    @pytest.mark.asyncio
    async def test_llm_call_with_tools_and_without(self):
        messages = [{"role": "user", "content": "What is AI?"}]
        tools = [{"name": "tool", "description": "desc"}]
        mock_completion = AsyncMock(return_value={"mocked": "response"})
        mock_litellm = SimpleNamespace(acompletion=mock_completion)

        with patch("omnicoreagent.core.llm._get_litellm", return_value=mock_litellm):
            conn = LLMConnection(
                make_model_config("groq", "llama-3"),
                api_key="test-api-key",
            )

            # With tools
            resp1 = await conn.llm_call(messages, tools)
            assert resp1 == {"mocked": "response"}
            mock_completion.assert_awaited_once()
            args1 = mock_completion.call_args.kwargs
            assert args1["model"] == "groq/llama-3"
            assert args1["tools"] == tools
            assert "tool_choice" not in args1

            mock_completion.reset_mock()

            # Without tools
            resp2 = await conn.llm_call(messages)
            assert resp2 == {"mocked": "response"}
            args2 = mock_completion.call_args.kwargs
            assert "tools" not in args2
            assert args2["model"] == "groq/llama-3"

    @pytest.mark.asyncio
    async def test_llm_call_handles_exceptions_gracefully(self):
        messages = [{"role": "user", "content": "Fail please"}]
        mock_completion = AsyncMock(side_effect=Exception("Boom"))
        mock_litellm = SimpleNamespace(acompletion=mock_completion)

        with patch("omnicoreagent.core.llm._get_litellm", return_value=mock_litellm):
            conn = LLMConnection(
                make_model_config("gemini", "gemini-pro"),
                api_key="test-api-key",
            )
            with pytest.raises(Exception, match="Boom"):
                await conn.llm_call(messages)

    def test_removed_method_is_not_present(self, mock_llm_connection):
        assert not hasattr(mock_llm_connection, "truncate_messages_for_groq")


def test_model_config_leaves_sampling_to_provider_unless_explicit():
    from omnicoreagent.core.runtime.config import ModelConfig, normalize_model_config

    config = normalize_model_config(
        ModelConfig(provider="openai", model="gpt-5.6-luna")
    )
    connection = LLMConnection(config, api_key="test-key")
    params = connection._completion_params([{"role": "user", "content": "hello"}])
    assert "temperature" not in params and "top_p" not in params
    explicit = LLMConnection(
        {"provider": "openai", "model": "gpt-4o", "temperature": 0.3, "top_p": 0.9},
        api_key="test-key",
    )
    params = explicit._completion_params([])
    assert params["temperature"] == 0.3 and params["top_p"] == 0.9


@pytest.mark.parametrize("effort", ["none", "high"])
def test_reasoning_effort_is_forwarded_without_overriding_explicit_choice(effort):
    from omnicoreagent.core.runtime.config import ModelConfig, normalize_model_config

    config = normalize_model_config(
        ModelConfig(provider="openai", model="gpt-5.6-luna", reasoning_effort=effort)
    )
    connection = LLMConnection(config, api_key="test-key")
    assert connection._completion_params([])["reasoning_effort"] == effort


def test_cookbook_luna_default_and_explicit_reasoning_override(monkeypatch):
    from cookbook import shared

    monkeypatch.setattr(shared, "load_cookbook_env", lambda: None)
    monkeypatch.delenv("OMNICORE_MODEL", raising=False)
    config = shared.model_config(provider="openai", model="gpt-5.6-luna")
    assert shared.DEFAULT_MODEL == "gpt-5.6-luna"
    assert config["reasoning_effort"] == "none"
    assert "temperature" not in config and "top_p" not in config
    assert (
        shared.model_config(
            provider="openai", model="gpt-5.6-luna", reasoning_effort="high"
        )["reasoning_effort"]
        == "high"
    )


@pytest.mark.asyncio
async def test_openai_production_routes_tools_through_litellm(monkeypatch):
    create = AsyncMock(return_value={"choices": []})
    monkeypatch.setattr(
        "omnicoreagent.core.llm._get_litellm",
        lambda: SimpleNamespace(acompletion=create),
    )
    connection = LLMConnection(
        {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "max_tokens": 200,
            "reasoning_effort": "none",
        },
        api_key="first-key",
    )
    # Another connection must not replace the first connection's request key.
    LLMConnection({"provider": "openai", "model": "gpt-5.6-luna"}, api_key="second-key")
    tools = [
        {
            "type": "function",
            "function": {"name": "probe", "parameters": {"type": "object"}},
        }
    ]
    await connection.llm_call(
        [{"role": "user", "content": "probe", "run_id": "private"}], tools
    )
    params = create.call_args.kwargs
    assert params["model"] == "openai/gpt-5.6-luna"
    assert params["api_key"] == "first-key"
    assert params["tools"] == tools
    assert params["reasoning_effort"] == "none"
    assert params["max_completion_tokens"] == 200
    assert params["drop_params"] is False and params["num_retries"] == 0
    assert "run_id" not in params["messages"][0]


def test_openai_sync_production_routes_through_litellm(monkeypatch):
    from unittest.mock import Mock

    create = Mock(side_effect=ValueError("bad request"))
    monkeypatch.setattr(
        "omnicoreagent.core.llm._get_litellm",
        lambda: SimpleNamespace(completion=create),
    )
    connection = LLMConnection(
        {"provider": "openai", "model": "gpt-5.6-luna"}, api_key="test-key"
    )
    with pytest.raises(ValueError, match="bad request"):
        connection.llm_call_sync([])
    create.assert_called_once()
    assert create.call_args.kwargs["model"] == "openai/gpt-5.6-luna"


@pytest.mark.parametrize("as_dataclass", [False, True])
def test_removed_provider_is_rejected(as_dataclass):
    from omnicoreagent.core.runtime.config import ModelConfig, normalize_model_config

    config = {"provider": "cencori", "model": "test"}
    if as_dataclass:
        config = ModelConfig(**config)
    with pytest.raises(ValueError, match="Unsupported provider"):
        normalize_model_config(config)
    with pytest.raises(ValueError, match="Unsupported provider"):
        LLMConnection({"provider": "cencori", "model": "test"}, api_key="test-key")
