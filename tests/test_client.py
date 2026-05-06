from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omnicoreagent.mcp_clients_connection.client import MCPClient

# Mock data for testing
MOCK_MODEL_CONFIG = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "max_tokens": 1000,
    "temperature": 0.5,
    "max_input_tokens": 1000,
    "top_p": 1,
}

MOCK_MCP_SERVERS = [
    {
        "name": "server1",
        "transport_type": "stdio",
        "command": "mock_command",
        "args": ["arg1", "arg2"],
        "env": {"TEST_ENV": "test"},
    },
    {
        "name": "server2",
        "transport_type": "sse",
        "url": "http://test.com",
        "headers": {"Authorization": "Bearer test"},
        "timeout": 5,
        "sse_read_timeout": 300,
    },
]


class MockTool:
    def __init__(self, name, description):
        self.name = name
        self.description = description


MOCK_TOOLS = [
    MockTool("tool1", "Test tool 1"),
    MockTool("tool2", "Test tool 2"),
]


class TestMCPClient:
    @pytest.fixture
    def mock_client(self):
        """Fixture to create a mock MCP client"""
        return MCPClient(
            servers=MOCK_MCP_SERVERS,
            model_config=MOCK_MODEL_CONFIG,
            api_key="test_llm_key",
            debug=True,
        )

    @pytest.fixture
    def mock_session(self):
        """Fixture to create a mock session"""
        session = AsyncMock()
        server_info = MagicMock()
        server_info.name = "test_server"
        session.initialize = AsyncMock(
            return_value=MagicMock(
                serverInfo=server_info,
            )
        )
        session.list_tools = AsyncMock(return_value=MagicMock(tools=MOCK_TOOLS))
        return session

    @pytest.mark.asyncio
    async def test_connect_to_single_server_stdio(self, mock_client, mock_session):
        """Test connecting to a stdio server"""
        with patch(
            "omnicoreagent.mcp_clients_connection.transports.stdio_client"
        ) as mock_stdio_client:
            mock_transport = (AsyncMock(), AsyncMock())
            mock_stdio_client.return_value.__aenter__.return_value = mock_transport

            # Mock stack management
            mock_stack = AsyncMock()
            mock_stack.enter_async_context.side_effect = [mock_transport, mock_session]

            with patch(
                "omnicoreagent.mcp_clients_connection.client.AsyncExitStack",
                return_value=mock_stack,
            ) as mock_exit_stack:
                server_info = MOCK_MCP_SERVERS[0]
                result = await mock_client._connect_to_single_server(
                    server_info, "server1"
                )

                assert result == "test_server connected successfully"
                mock_exit_stack.assert_called_once()
                mock_stack.enter_async_context.assert_called()
                mock_session.list_tools.assert_awaited_once()
                assert mock_client.available_tools["test_server"] == MOCK_TOOLS

    @pytest.mark.asyncio
    async def test_connect_to_single_server_sse(self, mock_client, mock_session):
        """Test connecting to an SSE server"""
        with patch(
            "omnicoreagent.mcp_clients_connection.transports.sse_client"
        ) as mock_sse_client:
            mock_transport = (AsyncMock(), AsyncMock())
            mock_sse_client.return_value.__aenter__.return_value = mock_transport

            # Mock stack management
            mock_stack = AsyncMock()
            mock_stack.enter_async_context.side_effect = [mock_transport, mock_session]

            with patch(
                "omnicoreagent.mcp_clients_connection.client.AsyncExitStack",
                return_value=mock_stack,
            ) as mock_exit_stack:
                server_info = MOCK_MCP_SERVERS[1]
                result = await mock_client._connect_to_single_server(
                    server_info, "server2"
                )

                assert result == "test_server connected successfully"
                mock_exit_stack.assert_called_once()
                mock_exit_stack.assert_called_once()
                mock_stack.enter_async_context.assert_called()
                mock_session.list_tools.assert_awaited_once()
                assert mock_client.available_tools["test_server"] == MOCK_TOOLS

    @pytest.mark.asyncio
    async def test_load_server_tools_not_connected(self, mock_client):
        """Test loading tools requires a connected MCP session."""
        with pytest.raises(ValueError, match="Not connected to server: missing"):
            await mock_client._load_server_tools("missing")

    @pytest.mark.asyncio
    async def test_load_server_tools_handles_unsupported_tools(self, mock_client):
        """Test a server without tools is represented as an empty tool list."""
        session = AsyncMock()
        session.list_tools = AsyncMock(side_effect=Exception("not supported"))
        mock_client.sessions = {
            "server_without_tools": {
                "session": session,
                "connected": True,
            }
        }

        tools = await mock_client._load_server_tools("server_without_tools")

        assert tools == []
        assert mock_client.available_tools["server_without_tools"] == []

    @pytest.mark.asyncio
    async def test_clean_up_server(self, mock_client):
        """Test cleaning up server connections"""
        mock_stack = AsyncMock()
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()

        mock_client.server_names = ["test_server"]
        mock_client.sessions = {
            "test_server": {
                "session": mock_session,
                "stack": mock_stack,
                "connected": True,
                "connection_type": "stdio",
            }
        }

        await mock_client.clean_up_server()

        mock_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup(self, mock_client):
        """Test full client cleanup"""
        mock_stack = AsyncMock()
        mock_session = AsyncMock()

        mock_client.server_names = ["test_server"]
        mock_client.sessions = {
            "test_server": {
                "session": mock_session,
                "stack": mock_stack,
                "connected": True,
                "connection_type": "stdio",
            }
        }

        await mock_client.cleanup()

        mock_stack.aclose.assert_awaited_once()
        assert len(mock_client.server_names) == 0
        assert len(mock_client.sessions) == 0

    @pytest.mark.asyncio
    async def test_add_servers(self, mock_client):
        """Test dynamically adding servers"""
        mock_client._connect_to_single_server = AsyncMock(
            return_value="new_server connected successfully"
        )
        result = await mock_client.add_servers(MOCK_MCP_SERVERS)

        assert "server1 connected successfully" in result
        mock_client._connect_to_single_server.assert_awaited()

    @pytest.mark.asyncio
    async def test_remove_server(self, mock_client):
        """Test removing a server"""
        mock_stack = AsyncMock()
        mock_session = AsyncMock()

        mock_client.server_names = ["test_server"]
        mock_client.sessions = {
            "test_server": {
                "session": mock_session,
                "stack": mock_stack,
                "connected": True,
                "connection_type": "stdio",
            },
            "other_server": {
                "session": AsyncMock(),
                "stack": AsyncMock(),
                "connected": True,
                "connection_type": "stdio",
            },
        }
        mock_client.added_servers_names = {"added_server": "test_server"}

        result = await mock_client.remove_server("added_server")

        assert "disconnected successfully" in result
        mock_stack.aclose.assert_awaited_once()
        assert "test_server" not in mock_client.sessions
