from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest

from omnicoreagent.mcp_clients_connection.state import ConnectedServer, MCPClientState


def make_connected_server(
    requested_name: str = "requested",
    server_name: str = "actual",
) -> ConnectedServer:
    return ConnectedServer(
        requested_name=requested_name,
        server_name=server_name,
        session=AsyncMock(),
        read_stream=AsyncMock(),
        write_stream=AsyncMock(),
        transport_type="stdio",
        stack=AsyncExitStack(),
    )


def test_add_server_tracks_session_alias_and_name():
    state = MCPClientState()
    connected_server = make_connected_server()

    state.add_server(connected_server)

    assert state.server_names == ["actual"]
    assert state.added_servers_names == {"requested": "actual"}
    assert state.sessions["actual"]["session"] is connected_server.session
    assert state.sessions["actual"]["connected"] is True


def test_add_server_rejects_duplicate_actual_server_name():
    state = MCPClientState()
    state.add_server(make_connected_server(requested_name="one", server_name="shared"))

    with pytest.raises(ValueError, match="shared is already connected"):
        state.add_server(make_connected_server(requested_name="two", server_name="shared"))


def test_resolve_server_name_accepts_requested_or_actual_name_case_insensitive():
    state = MCPClientState()
    state.add_server(make_connected_server(requested_name="LocalTools", server_name="mcp"))

    assert state.resolve_server_name("localtools") == "mcp"
    assert state.resolve_server_name("MCP") == "mcp"


def test_resolve_server_name_accepts_actual_name_without_alias():
    state = MCPClientState()
    state.server_names.append("mcp")

    assert state.resolve_server_name("MCP") == "mcp"


def test_remove_server_clears_session_alias_name_and_tools():
    state = MCPClientState()
    state.add_server(make_connected_server(requested_name="local", server_name="mcp"))
    state.set_tools("mcp", ["tool"])

    state.remove_server("mcp")

    assert state.sessions == {}
    assert state.server_names == []
    assert state.added_servers_names == {}
    assert state.available_tools == {}


def test_clear_removes_all_client_state():
    state = MCPClientState()
    state.add_server(make_connected_server(requested_name="local", server_name="mcp"))
    state.set_tools("mcp", ["tool"])

    state.clear()

    assert state.sessions == {}
    assert state.server_names == []
    assert state.added_servers_names == {}
    assert state.available_tools == {}
