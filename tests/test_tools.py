from unittest.mock import AsyncMock

import pytest

from omnicoreagent.mcp_clients_connection.tools import list_tools

# Mock data for testing
MOCK_TOOLS = {
    "server1": [
        {"name": "tool1", "description": "Tool 1 description"},
        {"name": "tool2", "description": "Tool 2 description"},
    ],
    "server2": [
        {"name": "tool3", "description": "Tool 3 description"},
    ],
}

MOCK_SESSIONS = {
    "server1": {
        "session": None,
        "connected": True,
    },
    "server2": {
        "session": None,
        "connected": True,
    },
}


@pytest.mark.asyncio
async def test_list_tools():
    """Test listing tools from servers"""

    # Create mock response
    class MockResponse:
        def __init__(self, tools):
            self.tools = tools

    # Update mock sessions with mock method
    test_sessions = MOCK_SESSIONS.copy()

    # Create async mock for server1
    mock_session1 = AsyncMock()
    mock_session1.list_tools.return_value = MockResponse(MOCK_TOOLS["server1"])
    test_sessions["server1"]["session"] = mock_session1

    # Create async mock for server2 that raises an exception
    mock_session2 = AsyncMock()
    mock_session2.list_tools.side_effect = Exception("Not supported")
    test_sessions["server2"]["session"] = mock_session2

    # Test successful tool listing
    tools = await list_tools(
        server_names=["server1", "server2"], sessions=test_sessions
    )
    assert len(tools) == 2
    assert all(tool["name"].startswith("tool") for tool in tools)
