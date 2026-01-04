import json
from unittest.mock import MagicMock, patch

import pytest

from omnicoreagent.mcp_clients_connection.sampling import samplingCallback
from omnicoreagent.core.types import ContextInclusion


@pytest.mark.asyncio
async def test_load_model():
    # Create an instance of the samplingCallback class
    callback = samplingCallback()

    # Mock the file content for config
    mock_config = {
        "LLM": {
            "provider": "openai",
            "model": ["gpt-3.5-turbo", "gpt-4"],
        }
    }
    with patch("builtins.open", new_callable=MagicMock) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(
            mock_config
        )

        available_models, provider = await callback.load_model()

    # Validate that models and provider are correctly loaded
    assert available_models == ["gpt-3.5-turbo", "gpt-4"]
    assert provider == "openai"


@pytest.mark.asyncio
async def test_select_model_no_preferences():
    callback = samplingCallback()

    # Simulate the available models
    available_models = ["gpt-3.5-turbo", "gpt-4"]

    # No preferences passed
    model = await callback._select_model(None, available_models)

    # Validate that the first model is selected
    assert model == "gpt-3.5-turbo"


@pytest.mark.asyncio
async def test_get_context_no_inclusion():
    callback = samplingCallback()

    # Test with no context inclusion
    context = await callback._get_context(ContextInclusion.NONE)

    # Validate that no context is returned
    assert context == ""


@pytest.mark.asyncio
async def test_get_context_this_server():
    callback = samplingCallback()

    # Simulate session data
    callback.sessions = {"server_1": {"message_history": ["message 1", "message 2"]}}

    # Test with context inclusion for this server
    context = await callback._get_context(ContextInclusion.THIS_SERVER, "server_1")

    # Validate the context returned is from the specific server
    assert context == "message 1\nmessage 2"


@pytest.mark.asyncio
async def test_get_context_all_servers():
    callback = samplingCallback()

    # Simulate session data
    callback.sessions = {
        "server_1": {"message_history": ["message 1"]},
        "server_2": {"message_history": ["message 2"]},
    }

    # Test with context inclusion for all servers
    context = await callback._get_context(ContextInclusion.ALL_SERVERS)

    # Validate the context returned is from all servers
    assert context == "message 1\nmessage 2"
