import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from omnicoreagent.mcp_clients_connection.client import (
    Configuration,
    MCPClient,
)

# Mock data for testing
MOCK_SERVER_CONFIG = {
    "LLM": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "max_tokens": 1000,
        "temperature": 0.5,
        "max_input_tokens": 1000,
        "top_p": 1,
    },
    "mcpServers": {
        "server1": {
            "transport_type": "stdio",
            "command": "mock_command",
            "args": ["arg1", "arg2"],
            "env": {"TEST_ENV": "test"},
        },
        "server2": {
            "transport_type": "sse",
            "url": "http://test.com",
            "headers": {"Authorization": "Bearer test"},
            "timeout": 5,
            "sse_read_timeout": 300,
        },
    },
}


@pytest.fixture
def mock_env():
    """Fixture to set up mock environment variables"""
    with (
        patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "test_llm_key",
            },
        ),
        patch("dotenv.load_dotenv"),
    ):
        yield


@pytest.fixture
def mock_config_file(tmp_path):
    """Fixture to create a mock config file"""
    config_file = tmp_path / "servers_config.json"
    config_file.write_text(json.dumps(MOCK_SERVER_CONFIG))
    return str(config_file)


class TestConfiguration:
    def test_init(self, mock_env):
        """Test Configuration initialization"""
        config = Configuration()
        assert config.llm_api_key == "test_llm_key"

    def test_load_config(self, mock_config_file):
        """Test loading configuration from file"""
        config = Configuration()
        loaded_config = config.load_config(mock_config_file)
        assert loaded_config == MOCK_SERVER_CONFIG

    def test_load_config_invalid_filename(self, tmp_path):
        """Test loading configuration with incorrect filename"""
        config = Configuration()
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text(json.dumps(MOCK_SERVER_CONFIG))
        with pytest.raises(ValueError):
            config.load_config(str(invalid_file))

    def test_load_config_invalid_json(self, tmp_path):
        """Test loading invalid JSON from correct filename"""
        config = Configuration()
        invalid_file = tmp_path / "servers_config.json"
        invalid_file.write_text("invalid json")
        with pytest.raises(json.JSONDecodeError):
            config.load_config(str(invalid_file))

    def test_llm_api_key_missing(self):
        """Test missing LLM_API_KEY"""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("dotenv.load_dotenv"),
            patch(
                "omnicoreagent.mcp_clients_connection.client.decouple_config",
                return_value=None,
            ),
            pytest.raises(ValueError) as exc_info,
        ):
            Configuration()
        assert "LLM_API_KEY not found in environment variables" in str(exc_info.value)


class TestMCPClient:
    @pytest.fixture
    def mock_client(self, mock_config_file):
        """Fixture to create a mock MCP client"""
        config = Configuration()
        config.load_config = MagicMock(return_value=MOCK_SERVER_CONFIG)
        return MCPClient(config, debug=True)

    @pytest.fixture
    def mock_session(self):
        """Fixture to create a mock session"""
        session = AsyncMock()
        server_info = MagicMock()
        server_info.name = "test_server"
        session.initialize = AsyncMock(
            return_value=MagicMock(
                serverInfo=server_info,
                capabilities={"tools": [], "resources": [], "prompts": []},
            )
        )
        return session

    @pytest.mark.asyncio
    @patch(
        "omnicoreagent.mcp_clients_connection.client.refresh_capabilities",
        new_callable=AsyncMock,
    )
    async def test_connect_to_single_server_stdio(
        self, mock_refresh, mock_client, mock_session
    ):
        """Test connecting to a stdio server"""
        with patch(
            "omnicoreagent.mcp_clients_connection.client.stdio_client"
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
                server_info = {
                    "name": "server1",
                    "srv_config": MOCK_SERVER_CONFIG["mcpServers"]["server1"],
                }
                result = await mock_client._connect_to_single_server(
                    server_info, "server1"
                )

                assert result == "test_server connected succesfully"
                mock_exit_stack.assert_called_once()
                mock_stack.enter_async_context.assert_called()
                mock_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    @patch(
        "omnicoreagent.mcp_clients_connection.client.refresh_capabilities",
        new_callable=AsyncMock,
    )
    async def test_connect_to_single_server_sse(
        self, mock_refresh, mock_client, mock_session
    ):
        """Test connecting to an SSE server"""
        with patch(
            "omnicoreagent.mcp_clients_connection.client.sse_client"
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
                server_info = {
                    "name": "server2",
                    "srv_config": MOCK_SERVER_CONFIG["mcpServers"]["server2"],
                }
                result = await mock_client._connect_to_single_server(
                    server_info, "server2"
                )

                assert result == "test_server connected succesfully"
                mock_exit_stack.assert_called_once()
                mock_exit_stack.assert_called_once()
                mock_stack.enter_async_context.assert_called()
                mock_refresh.assert_awaited_once()

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
        config_file = Path("dummy_config.json")

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(MOCK_SERVER_CONFIG))
        ):
            result = await mock_client.add_servers(config_file)

        # The real implementation iterates over MOCK_SERVER_CONFIG keys
        assert "server1 connected succesfully" in result
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

        assert "diconnected succesfully" in result
        mock_stack.aclose.assert_awaited_once()
        assert "test_server" not in mock_client.sessions
