from unittest.mock import Mock, patch, AsyncMock
import pytest
from omnicoreagent.core.llm import LLMConnection


# Shared mock config loader
def make_mock_config(provider="openai", model="gpt-4"):
    return {
        "llm_api_key": "test-api-key",
        "load_config": Mock(
            return_value={
                "LLM": {
                    "provider": provider,
                    "model": model,
                    "temperature": 0.7,
                    "max_tokens": 1000,
                    "top_p": 0.9,
                }
            }
        ),
    }


@pytest.fixture
def mock_llm_connection():
    with patch("omnicoreagent.core.llm.litellm"):
        return LLMConnection(Mock(**make_mock_config()), "test_config.yaml")


class TestLLMConnection:
    def test_initialization(self, mock_llm_connection):
        cfg = mock_llm_connection.llm_config
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "openai/gpt-4"
        assert cfg["temperature"] == 0.7

    def test_llm_configuration_returns_expected_keys(self, mock_llm_connection):
        config = mock_llm_connection.llm_configuration()
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

        with patch(
            "omnicoreagent.core.llm.litellm.acompletion", new_callable=AsyncMock
        ) as mock_completion:
            mock_completion.return_value = {"mocked": "response"}

            conn = LLMConnection(
                Mock(**make_mock_config("groq", "llama-3")), "test_config.yaml"
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

        with patch(
            "omnicoreagent.core.llm.litellm.acompletion", new_callable=AsyncMock
        ) as mock_completion:
            mock_completion.side_effect = Exception("Boom")

            conn = LLMConnection(
                Mock(**make_mock_config("gemini", "gemini-pro")), "test_config.yaml"
            )
            response = await conn.llm_call(messages)
            assert response is None

    def test_removed_method_is_not_present(self, mock_llm_connection):
        assert not hasattr(mock_llm_connection, "truncate_messages_for_groq")
