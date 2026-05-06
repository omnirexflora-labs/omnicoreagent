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
            assert args1["tool_choice"] == "auto"

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
            response = await conn.llm_call(messages)
            assert response is None

    def test_removed_method_is_not_present(self, mock_llm_connection):
        assert not hasattr(mock_llm_connection, "truncate_messages_for_groq")
