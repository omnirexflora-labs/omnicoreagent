from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest

from omnicoreagent.mcp_clients_connection.state import ConnectedServer, MCPClientState


def make_connected_server(name: str = "weather") -> ConnectedServer:
    return ConnectedServer(
        server_name=name,
        session=AsyncMock(),
        read_stream=AsyncMock(),
        write_stream=AsyncMock(),
        transport_type="stdio",
        stack=AsyncExitStack(),
        server_info={"name": "probe-server", "version": "1.2.3"},
        protocol_version="2025-11-25",
    )


def test_add_server_stores_it_under_its_configured_name_with_reported_metadata():
    state = MCPClientState()
    connected_server = make_connected_server()

    state.add_server(connected_server)

    assert state.server_names == ["weather"]
    session = state.sessions["weather"]
    assert session["session"] is connected_server.session
    assert session["connected"] is True
    assert session["server_info"] == {"name": "probe-server", "version": "1.2.3"}
    assert session["protocol_version"] == "2025-11-25"


def test_add_server_rejects_a_duplicate_configured_name():
    state = MCPClientState()
    state.add_server(make_connected_server("weather"))

    with pytest.raises(ValueError, match="weather is already connected"):
        state.add_server(make_connected_server("weather"))


def test_resolve_server_name_matches_configured_names_case_insensitively():
    state = MCPClientState()
    state.add_server(make_connected_server("Weather"))

    assert state.resolve_server_name("weather") == "Weather"
    with pytest.raises(ValueError, match="not found"):
        # The reported name is metadata, not an identity.
        state.resolve_server_name("probe-server")


def test_remove_server_clears_session_name_and_tools():
    state = MCPClientState()
    state.add_server(make_connected_server("weather"))
    state.set_tools("weather", ["tool"])

    state.remove_server("weather")

    assert state.sessions == {}
    assert state.server_names == []
    assert state.added_servers_names == {}
    assert state.available_tools == {}


def test_clear_removes_all_client_state():
    state = MCPClientState()
    state.add_server(make_connected_server("weather"))
    state.set_tools("weather", ["tool"])

    state.clear()

    assert state.sessions == {}
    assert state.server_names == []
    assert state.added_servers_names == {}
    assert state.available_tools == {}
