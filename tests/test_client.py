from unittest.mock import AsyncMock, patch

import pytest

from omnicoreagent.governance import (
    GovernanceEngine,
    UnknownCapabilityError,
    policy_from_mapping,
)
from omnicoreagent.mcp_clients_connection.client import MCPClient
from omnicoreagent.mcp_clients_connection.transports import (
    build_stdio_env,
    normalize_transport_type,
)

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


def _strict_mcp_connection_policy():
    return policy_from_mapping(
        {
            "name": "mcp-connect-policy",
            "mode": "strict",
            "rules": {
                "allow": [
                    {
                        "rule_id": "allow_docs_stdio_server",
                        "capability": "mcp.server.start",
                        "target": {"mcp_server": "server1"},
                    },
                    {
                        "rule_id": "allow_docs_remote_server",
                        "capability": "mcp.server.connect",
                        "target": {"host": "test.com", "mcp_server": "server2"},
                    },
                ]
            },
        }
    )


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
    async def test_add_servers(self, mock_client):
        """Test dynamically adding servers"""
        mock_client._connect_to_single_server = AsyncMock(
            side_effect=[
                "server1 connected successfully",
                "server2 connected successfully",
            ]
        )
        result = await mock_client.add_servers(MOCK_MCP_SERVERS)

        assert result == [
            "server1 connected successfully",
            "server2 connected successfully",
        ]
        assert mock_client._connect_to_single_server.await_count == 2

    @pytest.mark.asyncio
    async def test_governance_denies_mcp_server_connect_before_transport(
        self,
        mock_client,
    ):
        mock_client.governance_engine = GovernanceEngine(
            policy_from_mapping(
                {
                    "name": "deny-mcp-connect",
                    "mode": "strict",
                    "rules": {},
                }
            )
        )

        with patch(
            "omnicoreagent.mcp_clients_connection.client.open_server_transport"
        ) as open_transport:
            with pytest.raises(UnknownCapabilityError):
                await mock_client._connect_to_single_server(
                    MOCK_MCP_SERVERS[0],
                    "server1",
                )

        open_transport.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_mcp_transport_fails_before_transport(self, mock_client):
        mock_client.governance_engine = GovernanceEngine(_strict_mcp_connection_policy())
        server = {
            "name": "server1",
            "transport_type": "http",
            "command": "mock_command",
            "args": [],
        }

        with patch(
            "omnicoreagent.mcp_clients_connection.client.open_server_transport"
        ) as open_transport:
            with pytest.raises(ValueError, match="Unsupported MCP transport_type: http"):
                await mock_client._connect_to_single_server(server, "server1")

        open_transport.assert_not_called()

    def test_transport_normalization_rejects_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported MCP transport_type: http"):
            normalize_transport_type({"transport_type": "http"})

    @pytest.mark.asyncio
    async def test_governance_checks_dynamic_add_servers(self, mock_client):
        mock_client.governance_engine = GovernanceEngine(
            policy_from_mapping(
                {
                    "name": "deny-dynamic-mcp-connect",
                    "mode": "strict",
                    "rules": {},
                }
            )
        )

        with patch(
            "omnicoreagent.mcp_clients_connection.client.open_server_transport"
        ) as open_transport:
            with pytest.raises(UnknownCapabilityError):
                await mock_client.add_servers([MOCK_MCP_SERVERS[1]])

        open_transport.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_to_servers_surfaces_governance_error(self, mock_client):
        mock_client.governance_engine = GovernanceEngine(
            policy_from_mapping(
                {
                    "name": "deny-mcp-connect",
                    "mode": "strict",
                    "rules": {},
                }
            )
        )

        with patch(
            "omnicoreagent.mcp_clients_connection.client.open_server_transport"
        ) as open_transport:
            with pytest.raises(UnknownCapabilityError):
                await mock_client.connect_to_servers()

        open_transport.assert_not_called()

    def test_stdio_env_uses_only_explicit_server_env(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("LANG", "C.UTF-8")
        monkeypatch.setenv("HTTPS_PROXY", "http://secret@example.test")

        env = build_stdio_env({"env": {"SAFE_TOKEN": "explicit", "COUNT": 3}})

        assert env["PATH"] == "/usr/bin"
        assert env["LANG"] == "C.UTF-8"
        assert env["SAFE_TOKEN"] == "explicit"
        assert env["COUNT"] == "3"
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "HTTPS_PROXY" not in env

    def test_stdio_env_uses_scrubbed_baseline_without_explicit_env(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
        monkeypatch.setenv("PATH", "/usr/bin")

        env = build_stdio_env({})

        assert env["PATH"] == "/usr/bin"
        assert "AWS_SECRET_ACCESS_KEY" not in env

