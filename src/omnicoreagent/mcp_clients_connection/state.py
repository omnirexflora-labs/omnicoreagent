from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ConnectedServer:
    """One connected server, identified by its configured name.

    ``server_info`` is what the server reported about itself; it is metadata
    only, because the server controls it.
    """

    server_name: str
    session: Any
    read_stream: Any
    write_stream: Any
    transport_type: str
    connection: Any = None
    server_info: dict[str, Any] | None = None
    protocol_version: str | None = None
    call_timeout: float | None = None
    reconnect: Callable[[], Awaitable[None]] | None = None

    def session_info(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "read_stream": self.read_stream,
            "write_stream": self.write_stream,
            "connected": True,
            "transport_type": self.transport_type,
            "connection": self.connection,
            "server_info": self.server_info,
            "protocol_version": self.protocol_version,
            "call_timeout": self.call_timeout,
            "reconnect": self.reconnect,
            "reconnects": 0,
            "last_error": None,
        }


class MCPClientState:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.available_tools: dict[str, list[Any]] = {}
        self.server_names: list[str] = []
        self.added_servers_names: dict[str, str] = {}
        # Servers that failed to connect, with the reason.
        self.failures: dict[str, dict[str, Any]] = {}

    def has_server(self, server_name: str) -> bool:
        return server_name in self.sessions

    def add_server(self, connected_server: ConnectedServer) -> None:
        if self.has_server(connected_server.server_name):
            raise ValueError(
                f"{connected_server.server_name} is already connected. "
                "Disconnect it and try again."
            )

        self.server_names.append(connected_server.server_name)
        self.added_servers_names[connected_server.server_name] = (
            connected_server.server_name
        )
        self.sessions[connected_server.server_name] = connected_server.session_info()

    def set_tools(self, server_name: str, tools: list[Any]) -> None:
        self.available_tools[server_name] = tools

    def resolve_server_name(self, name: str) -> str:
        name_lower = name.lower()
        for server_name in self.server_names:
            if name_lower == server_name.lower():
                return server_name

        raise ValueError(f"Server '{name}' not found.")

    def remove_server(self, server_name: str) -> None:
        self.sessions.pop(server_name, None)
        if server_name in self.server_names:
            self.server_names.remove(server_name)
        self.added_servers_names = {
            requested: actual
            for requested, actual in self.added_servers_names.items()
            if actual != server_name
        }
        self.available_tools.pop(server_name, None)

    def clear(self) -> None:
        self.server_names.clear()
        self.added_servers_names.clear()
        self.sessions.clear()
        self.available_tools.clear()
        self.failures.clear()
