from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ConnectedServer:
    requested_name: str
    server_name: str
    session: Any
    read_stream: Any
    write_stream: Any
    transport_type: str
    stack: AsyncExitStack

    def session_info(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "read_stream": self.read_stream,
            "write_stream": self.write_stream,
            "connected": True,
            "transport_type": self.transport_type,
            "stack": self.stack,
        }


class MCPClientState:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.available_tools: dict[str, list[Any]] = {}
        self.server_names: list[str] = []
        self.added_servers_names: dict[str, str] = {}

    def has_server(self, server_name: str) -> bool:
        return server_name in self.sessions

    def add_server(self, connected_server: ConnectedServer) -> None:
        if self.has_server(connected_server.server_name):
            raise ValueError(
                f"{connected_server.server_name} is already connected. "
                "Disconnect it and try again."
            )

        self.server_names.append(connected_server.server_name)
        self.added_servers_names[connected_server.requested_name] = (
            connected_server.server_name
        )
        self.sessions[connected_server.server_name] = connected_server.session_info()

    def set_tools(self, server_name: str, tools: list[Any]) -> None:
        self.available_tools[server_name] = tools

    def resolve_server_name(self, name: str) -> str:
        name_lower = name.lower()
        for requested_name, server_name in self.added_servers_names.items():
            if name_lower in {requested_name.lower(), server_name.lower()}:
                return server_name
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
