import asyncio
from contextlib import AsyncExitStack
from functools import partial
from typing import Any

from mcp import ClientSession, types

from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.core.logging import logger
from omnicoreagent.governance.capabilities import mcp_server_authority_request
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.mcp_clients_connection.connection import ServerConnection
from omnicoreagent.mcp_clients_connection.oauth import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    build_oauth_provider,
    is_oauth_enabled,
)
from omnicoreagent.mcp_clients_connection.state import (
    ConnectedServer,
    MCPClientState,
)
from omnicoreagent.mcp_clients_connection.transports import open_server_transport

SESSION_READ_TIMEOUT_SECONDS = 300.0
# Covers the transport, the handshake, and listing every tool page.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0


def _client_info() -> types.Implementation:
    """How OmniCoreAgent identifies itself in the MCP handshake."""
    from omnicoreagent import __version__

    return types.Implementation(name="omnicoreagent", version=__version__)


async def list_all_tools(session: Any) -> list[Any]:
    """Every tool the server offers, following pagination."""
    tools: list[Any] = []
    cursor = None
    while True:
        params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await session.list_tools(params=params)
        tools.extend(page.tools or [])
        cursor = page.next_cursor
        if not cursor:
            return tools


def root_cause(error: BaseException) -> BaseException:
    """The first real error inside the SDK's (possibly nested) task groups."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


class MCPClient:
    def __init__(
        self,
        servers: list[dict[str, Any]] | None = None,
        model_config: dict[str, Any] | None = None,
        api_key: str | None = None,
        governance_engine: Any = None,
        debug: bool = False,
    ):
        self.servers = self._normalize_servers(servers or [])
        self.state = MCPClientState()
        self.debug = debug
        self.governance_engine = governance_engine
        self.llm_connection = (
            LLMConnection(model_config=model_config, api_key=api_key)
            if model_config
            else None
        )
        self.server_count = 0

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return self.state.sessions

    @sessions.setter
    def sessions(self, value: dict[str, dict[str, Any]]) -> None:
        self.state.sessions = value

    @property
    def available_tools(self) -> dict[str, list[Any]]:
        return self.state.available_tools

    @available_tools.setter
    def available_tools(self, value: dict[str, list[Any]]) -> None:
        self.state.available_tools = value

    @property
    def server_names(self) -> list[str]:
        return self.state.server_names

    @server_names.setter
    def server_names(self, value: list[str]) -> None:
        self.state.server_names = value

    @property
    def added_servers_names(self) -> dict[str, str]:
        return self.state.added_servers_names

    @added_servers_names.setter
    def added_servers_names(self, value: dict[str, str]) -> None:
        self.state.added_servers_names = value

    def _normalize_servers(self, servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for server in servers:
            server_config = dict(server)
            if not server_config.get("name"):
                raise ValueError("Each MCP server config requires a name")
            normalized.append(server_config)
        return normalized

    def _server_config(self, name: str) -> dict[str, Any]:
        for server in self.servers:
            if server["name"] == name:
                return server
        raise ValueError(f"Server '{name}' not found.")

    async def connect_to_servers(self):
        """Connect to configured MCP servers.

        A server that fails to connect is recorded in ``state.failures`` and
        does not stop the others; a governance denial is raised.
        """
        results = await asyncio.gather(
            *(self._connect_to_single_server(server, server["name"]) for server in self.servers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, (GovernanceError, ValueError)):
                raise result
            if isinstance(result, BaseException):
                logger.error(f"Server connection failed: {result}")
            else:
                logger.info(f"Server connection result: {result}")

    async def _connect_to_single_server(self, server, server_added_name):
        # The configured name is the identity: routing, governance, and
        # telemetry all use it. What the server reports is metadata.
        server_name = server_added_name
        if self.state.has_server(server_name):
            return f"{server_name} is already connected. Disconnect it and try again."
        await self._authorize_server_connection(server)
        try:
            connection, exposed = await self._open_connection(server)
        except (GovernanceError, ValueError):
            raise
        except Exception as exc:
            cause = root_cause(exc)
            self.state.failures[server_name] = {
                "error": str(cause) or cause.__class__.__name__,
                "type": cause.__class__.__name__,
            }
            error_message = f"Failed to connect to {server_name}: {cause}"
            logger.error(error_message)
            return error_message

        self.state.failures.pop(server_name, None)
        self.state.add_server(
            ConnectedServer(
                server_name=server_name,
                session=exposed["session"],
                read_stream=exposed["read_stream"],
                write_stream=exposed["write_stream"],
                transport_type=exposed["transport_type"],
                connection=connection,
                server_info=exposed["server_info"],
                protocol_version=exposed["protocol_version"],
                call_timeout=server.get("call_timeout"),
                reconnect=partial(self.reconnect, server_name),
            )
        )
        self.state.set_tools(server_name, exposed["tools"])
        if self.debug:
            logger.info(
                f"Connected to {server_name} via {exposed['transport_type']}; "
                f"{len(exposed['tools'])} tools"
            )
        return f"{server_name} connected successfully"

    async def _open_connection(self, server: dict[str, Any]):
        name = server["name"]
        connection = ServerConnection(
            name,
            opener=partial(self._open, server),
            on_lost=partial(self._mark_lost, name),
        )
        timeout = float(server.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT_SECONDS)
        exposed = await connection.open(timeout=timeout)
        return connection, exposed

    async def _open(self, server: dict[str, Any], stack: AsyncExitStack) -> dict[str, Any]:
        """Runs inside the connection's owner task."""
        oauth_auth = None
        if is_oauth_enabled(server):
            auth = server["auth"]
            oauth_auth = build_oauth_provider(
                server_url=server.get("url", ""),
                callback_port=auth.get("callback_port"),
                callback_timeout=float(
                    auth.get("callback_timeout") or DEFAULT_CALLBACK_TIMEOUT_SECONDS
                ),
            )
        read_stream, write_stream, transport_type = await open_server_transport(
            stack=stack, server=server, oauth_auth=oauth_auth, debug=self.debug
        )
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=SESSION_READ_TIMEOUT_SECONDS,
                client_info=_client_info(),
            )
        )
        await session.initialize()
        reported = session.server_info
        return {
            "session": session,
            "read_stream": read_stream,
            "write_stream": write_stream,
            "transport_type": transport_type,
            "server_info": (
                {"name": reported.name, "version": reported.version} if reported else None
            ),
            "protocol_version": session.protocol_version,
            "tools": await list_all_tools(session),
        }

    def _mark_lost(self, name: str, connection: ServerConnection, error: BaseException) -> None:
        info = self.sessions.get(name)
        # A replaced connection that dies later must not mark its successor.
        if info is not None and info.get("connection") is connection:
            info["connected"] = False
            info["last_error"] = str(error) or error.__class__.__name__

    async def reconnect(self, name: str) -> None:
        """Replace a dropped connection; the session entry is updated in place.

        Raises when the server cannot be reached again, after marking it
        unavailable with the reason.
        """
        info = self.sessions[name]
        info["connected"] = False
        old = info.get("connection")
        if old is not None:
            close_error = await old.close()
            if close_error is not None:
                logger.warning(f"Closing the dropped MCP connection {name}: {close_error!r}")
        try:
            connection, exposed = await self._open_connection(self._server_config(name))
        except Exception as exc:
            cause = root_cause(exc)
            info["last_error"] = str(cause) or cause.__class__.__name__
            raise cause from exc
        info.update(
            session=exposed["session"],
            read_stream=exposed["read_stream"],
            write_stream=exposed["write_stream"],
            connection=connection,
            server_info=exposed["server_info"],
            protocol_version=exposed["protocol_version"],
            connected=True,
            last_error=None,
            reconnects=info.get("reconnects", 0) + 1,
        )
        self.state.set_tools(name, exposed["tools"])
        logger.info(f"Reconnected to MCP server {name}")

    async def _authorize_server_connection(self, server: dict[str, Any]) -> None:
        if self.governance_engine is None:
            return
        request = mcp_server_authority_request(server=server, actor="mcp_client")
        await self.governance_engine.authorize(request)

    async def _load_server_tools(self, server_name: str) -> list[Any]:
        """Reload every tool page for one connected server."""
        session_info = self.sessions.get(server_name)
        if not session_info or not session_info.get("connected", False):
            raise ValueError(f"Not connected to server: {server_name}")

        session = session_info.get("session")
        if not session:
            logger.warning(f"No session found for server: {server_name}")
            self.available_tools[server_name] = []
            return []

        try:
            tools = await list_all_tools(session)
        except Exception as e:
            logger.info(f"{server_name} does not support tools: {e}")
            tools = []

        self.state.set_tools(server_name, tools)
        return tools

    async def add_servers(self, servers: list[dict[str, Any]]) -> list[Any]:
        """Dynamically add servers at runtime."""
        servers = self._normalize_servers(servers)
        self.servers.extend(
            server
            for server in servers
            if all(existing["name"] != server["name"] for existing in self.servers)
        )
        results = await asyncio.gather(
            *(self._connect_to_single_server(server, server["name"]) for server in servers),
            return_exceptions=True,
        )

        responses = []
        for server, result in zip(servers, results, strict=True):
            if isinstance(result, GovernanceError):
                raise result
            if isinstance(result, BaseException):
                logger.error(f"Failed to add server '{server['name']}': {result}")
                responses.append((server["name"], str(result)))
            else:
                responses.append(result)
        return responses

    async def remove_server(self, name: str) -> str:
        """Disconnect and remove a server by name."""
        try:
            server_name = self.state.resolve_server_name(name)
            if len(self.sessions) == 1:
                return (
                    f"Cannot remove {name}: at least one server must remain connected."
                )
            session_info = self.sessions[server_name]
            close_error = await self._close_session(server_name, session_info)
        except ValueError as e:
            error_message = f"Error removing server: {str(e)}"
            logger.error(error_message)
            return error_message

        self.state.remove_server(server_name)
        if close_error is not None:
            return f"{server_name} disconnected with a close error: {close_error}"
        logger.info(f"Server '{server_name}' removed successfully.")
        return f"{server_name} disconnected successfully"

    async def _close_session(self, server_name: str, session_info: dict) -> BaseException | None:
        """Close one connection in its owner task; return (and log) a close error."""
        connection = session_info.get("connection")
        if connection is None:
            logger.warning(f"No connection found for {server_name}")
            return None
        session_info["connected"] = False
        close_error = await connection.close()
        if close_error is not None:
            logger.error(f"Error closing MCP server {server_name}: {close_error!r}")
        else:
            logger.info(f"Server {server_name} has been disconnected.")
        return close_error

    async def clean_up_server(self) -> list[BaseException]:
        """Close every connection; return the close errors (also logged)."""
        names = [name for name in self.server_names if name in self.sessions]
        results = await asyncio.gather(
            *(self._close_session(name, self.sessions[name]) for name in names)
        )
        return [error for error in results if error is not None]

    async def cleanup(self) -> list[BaseException]:
        """Close all MCP connections and clear the client state."""
        logger.info("Starting client shutdown...")
        errors = await self.clean_up_server()
        self.state.clear()
        logger.info("All MCP connections cleared")
        return errors
