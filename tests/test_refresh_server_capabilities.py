import os
from unittest.mock import AsyncMock, Mock, patch

from omnicoreagent.mcp_clients_connection.client import Configuration  # noqa: E402
from omnicoreagent.mcp_clients_connection.refresh_server_capabilities import (
    refresh_capabilities,  # noqa: E402
)

import pytest
from dotenv import load_dotenv

load_dotenv()

llm_api_key = os.getenv("LLM_API_KEY")
if not llm_api_key:
    os.environ["LLM_API_KEY"] = "SKU123"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    os.environ["OPENAI_API_KEY"] = "SKU456"


MOCK_LLM_CONFIG = Configuration()


# Mock data
class MockTool:
    def __init__(self, name, description):
        self.name = name
        self.description = description


class MockResource:
    def __init__(self, name, description):
        self.name = name
        self.description = description


class MockPrompt:
    def __init__(self, name, description):
        self.name = name
        self.description = description


MOCK_TOOLS = [
    MockTool("tool1", "Test tool 1"),
    MockTool("tool2", "Test tool 2"),
]

MOCK_RESOURCES = [
    MockResource("resource1", "Test resource 1"),
    MockResource("resource2", "Test resource 2"),
]

MOCK_PROMPTS = [
    MockPrompt("prompt1", "Test prompt 1"),
    MockPrompt("prompt2", "Test prompt 2"),
]


@pytest.fixture
def mock_sessions():
    """Create mock sessions"""

    async def mock_list_tools():
        return Mock(tools=MOCK_TOOLS)

    async def mock_list_resources():
        return Mock(resources=MOCK_RESOURCES)

    async def mock_list_prompts():
        return Mock(prompts=MOCK_PROMPTS)

    async def mock_list_tools_error():
        raise Exception("No tools support")

    async def mock_list_resources_error():
        raise Exception("No resources support")

    async def mock_list_prompts_error():
        raise Exception("No prompts support")

    return {
        "server1": {
            "connected": True,
            "session": Mock(
                list_tools=AsyncMock(side_effect=mock_list_tools),
                list_resources=AsyncMock(side_effect=mock_list_resources),
                list_prompts=AsyncMock(side_effect=mock_list_prompts),
            ),
        },
        "server2": {
            "connected": True,
            "session": Mock(
                list_tools=AsyncMock(side_effect=mock_list_tools_error),
                list_resources=AsyncMock(side_effect=mock_list_resources_error),
                list_prompts=AsyncMock(side_effect=mock_list_prompts_error),
            ),
        },
    }


@pytest.fixture
def mock_available_dicts():
    """Create mock available dictionaries"""
    return {"tools": {}, "resources": {}, "prompts": {}}


@pytest.mark.asyncio
async def test_refresh_capabilities_success(mock_sessions, mock_available_dicts):
    """Test successful refresh of capabilities"""
    server_names = ["server1", "server2"]

    # Reset dictionaries before the test
    mock_available_dicts["tools"].clear()
    mock_available_dicts["resources"].clear()
    mock_available_dicts["prompts"].clear()

    await refresh_capabilities(
        mock_sessions,
        server_names,
        available_resources=mock_available_dicts["resources"],
        available_prompts=mock_available_dicts["prompts"],
        available_tools=mock_available_dicts["tools"],
        debug=False,
    )

    # Check server1 capabilities
    server1_tools = mock_available_dicts["tools"]["server1"]
    server1_resources = mock_available_dicts["resources"]["server1"]
    server1_prompts = mock_available_dicts["prompts"]["server1"]

    # Compare tool attributes
    assert len(server1_tools) == len(MOCK_TOOLS)
    for actual, expected in zip(server1_tools, MOCK_TOOLS):
        assert isinstance(actual, MockTool), f"Expected MockTool, got {type(actual)}"
        assert actual.name == expected.name
        assert actual.description == expected.description

    # Compare resource attributes
    assert len(server1_resources) == len(MOCK_RESOURCES)
    for actual, expected in zip(server1_resources, MOCK_RESOURCES):
        assert isinstance(actual, MockResource), (
            f"Expected MockResource, got {type(actual)}"
        )
        assert actual.name == expected.name
        assert actual.description == expected.description

    # Compare prompt attributes
    assert len(server1_prompts) == len(MOCK_PROMPTS)
    for actual, expected in zip(server1_prompts, MOCK_PROMPTS):
        assert isinstance(actual, MockPrompt), (
            f"Expected MockPrompt, got {type(actual)}"
        )
        assert actual.name == expected.name
        assert actual.description == expected.description

    # Check server2 capabilities (empty lists due to exceptions)
    assert mock_available_dicts["tools"]["server2"] == []
    assert mock_available_dicts["resources"]["server2"] == []
    assert mock_available_dicts["prompts"]["server2"] == []


@pytest.mark.asyncio
async def test_refresh_capabilities_not_connected():
    """Test refresh when server is not connected"""
    sessions = {"server1": {"connected": False, "session": Mock()}}

    with pytest.raises(ValueError, match="Not connected to server: server1"):
        await refresh_capabilities(
            sessions,
            ["server1"],
            {},
            {},
            {},
            debug=False,
        )


@pytest.mark.asyncio
async def test_refresh_capabilities_with_debug(mock_sessions, mock_available_dicts):
    """Test refresh with debug logging"""
    server_names = ["server1", "server2"]

    with patch(
        "omnicoreagent.mcp_clients_connection.refresh_server_capabilities.logger"
    ) as mock_logger:
        await refresh_capabilities(
            mock_sessions,
            server_names,
            available_resources=mock_available_dicts["resources"],
            available_prompts=mock_available_dicts["prompts"],
            available_tools=mock_available_dicts["tools"],
            debug=True,
        )

        # Verify debug logging
        mock_logger.info.assert_any_call(f"Refreshed capabilities for {server_names}")

        for category, data in {
            "Tools": mock_available_dicts["tools"],
            "Resources": mock_available_dicts["resources"],
            "Prompts": mock_available_dicts["prompts"],
        }.items():
            mock_logger.info.assert_any_call(f"Available {category.lower()} by server:")
            for server_name, items in data.items():
                mock_logger.info.assert_any_call(f"  {server_name}:")
                for item in items:
                    mock_logger.info.assert_any_call(f"    - {item.name}")

        mock_logger.info.assert_called_with(
            "Updated system prompt with new capabilities"
        )
