import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import ClientSession

from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.core.logging import logger
from omnicoreagent.governance.capabilities import mcp_server_authority_request
from omnicoreagent.governance.errors import GovernanceError
from omnicoreagent.mcp_clients_connection.oauth import (
    build_oauth_provider,
    is_oauth_enabled,
)
from omnicoreagent.mcp_clients_connection.state import (
    ConnectedServer,
    MCPClientState,
)
from omnicoreagent.mcp_clients_connection.transports import open_server_transport


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

    async def connect_to_servers(self):
        """Connect to configured MCP servers."""
        servers = self.servers
        try:
            connect_tasks = [
                self._connect_to_single_server(server, server["name"])
                for server in servers
            ]
            results = await asyncio.gather(*connect_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Server connection failed: {result}")
                    if isinstance(result, GovernanceError):
                        raise result
                logger.info(f"Server connection result: {result}")
        except (GovernanceError, ValueError):
            raise
        except Exception as e:
            logger.info(f"start servers task error: {e}")

    async def _connect_to_single_server(self, server, server_added_name):
        try:
            await self._authorize_server_connection(server)
            stack = AsyncExitStack()
            url = server.get("url", "")

            self.server_count += 1
            callback_port = 3000 + self.server_count
            oauth_auth = None
            if is_oauth_enabled(server):
                oauth_auth = build_oauth_provider(
                    server_url=url,
                    callback_port=callback_port,
                )

            read_stream, write_stream, transport_type = await open_server_transport(
                stack=stack,
                server=server,
                oauth_auth=oauth_auth,
                debug=self.debug,
            )

            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=300),
                )
            )
            init_result = await session.initialize()
            server_name = init_result.serverInfo.name
            if server_name != server_added_name:
                await self._authorize_server_connection(
                    server,
                    resolved_server_name=server_name,
                )
            if self.state.has_server(server_name):
                error_message = (
                    f"{server_name} is already connected. Disconnect it and try again."
                )
                if self.debug:
                    logger.error(error_message)
                await stack.aclose()
                return error_message
            self.state.add_server(
                ConnectedServer(
                    requested_name=server_added_name,
                    server_name=server_name,
                    session=session,
                    read_stream=read_stream,
                    write_stream=write_stream,
                    transport_type=transport_type,
                    stack=stack,
                )
            )
            if self.debug:
                logger.info(
                    f"Successfully connected to {server_name} via {transport_type}"
                )
            await self._load_server_tools(server_name)

            return f"{server_name} connected successfully"
        except (GovernanceError, ValueError):
            stack = locals().get("stack")
            if stack is not None:
                try:
                    await stack.aclose()
                except Exception as cleanup_error:
                    logger.error(f"Error cleaning failed MCP connection: {cleanup_error}")
            raise
        except Exception as e:
            stack = locals().get("stack")
            if stack is not None:
                try:
                    await stack.aclose()
                except Exception as cleanup_error:
                    logger.error(f"Error cleaning failed MCP connection: {cleanup_error}")
            error_message = f"Failed to connect to server: {str(e)}"
            logger.error(error_message)
            return error_message

    async def _authorize_server_connection(
        self,
        server: dict[str, Any],
        *,
        resolved_server_name: str | None = None,
    ) -> None:
        if self.governance_engine is None:
            return
        server_identity = dict(server)
        if resolved_server_name is not None:
            server_identity["name"] = resolved_server_name
            server_identity["requested_name"] = server.get("name")
        request = mcp_server_authority_request(
            server=server_identity,
            actor="mcp_client",
        )
        await self.governance_engine.authorize(request)

    async def _load_server_tools(self, server_name: str) -> list[Any]:
        """Load MCP tools for one connected server."""
        session_info = self.sessions.get(server_name)
        if not session_info or not session_info.get("connected", False):
            raise ValueError(f"Not connected to server: {server_name}")

        session = session_info.get("session")
        if not session:
            logger.warning(f"No session found for server: {server_name}")
            self.available_tools[server_name] = []
            return []

        try:
            tools_response = await session.list_tools()
            tools = tools_response.tools if tools_response else []
        except Exception as e:
            logger.info(f"{server_name} does not support tools: {e}")
            tools = []

        self.state.set_tools(server_name, tools)
        if self.debug:
            logger.info(f"Loaded {len(tools)} MCP tools from {server_name}")
            for tool in tools:
                logger.info(f"  - {tool.name}")
        return tools

    async def add_servers(self, servers: list[dict[str, Any]]) -> list[Any]:
        """Dynamically add servers at runtime."""
        servers = self._normalize_servers(servers)
        connect_tasks = [
            self._connect_to_single_server(server, server["name"]) for server in servers
        ]
        results = await asyncio.gather(*connect_tasks, return_exceptions=True)

        responses = []
        for server, result in zip(servers, results, strict=True):
            if isinstance(result, Exception):
                logger.error(f"Failed to add server '{server['name']}': {result}")
                if isinstance(result, GovernanceError):
                    raise result
                responses.append((server["name"], str(result)))
            else:
                responses.append(result)
        return responses

    async def remove_server(self, name: str) -> str:
        """Disconnect and remove a server by name."""
        try:
            old_name = name
            server_name = self.state.resolve_server_name(name)
            if len(self.sessions) == 1:
                return (
                    f"Cannot remove {name}: at least one server must remain connected."
                )
            session_info = self.sessions[server_name]
            await self._close_session(server_name=old_name, session_info=session_info)
        except ValueError as e:
            error_message = f"Error removing server: {str(e)}"
            logger.error(error_message)
            return error_message
        except Exception as e:
            error_message = f"Error cleaning up server '{name}': {e}"
            logger.error(error_message)
            return error_message

        self.state.remove_server(server_name)

        logger.info(f"Server '{server_name}' removed successfully.")
        return f"{server_name} disconnected successfully"

    async def _close_session(self, server_name: str, session_info: dict):
        """Tear down the per-server context stack, which closes streams and session."""

        stack: AsyncExitStack = session_info.get("stack")
        if not stack:
            logger.warning(f"No context stack found for {server_name}")
            return
        try:
            logger.info(f"Closing context stack for {server_name}")
            await stack.aclose()
            logger.info(f"Server {server_name} has been disconnected and removed.")
        except RuntimeError as e:
            if "cancel scope" in str(e).lower():
                logger.warning(
                    f"Cancel scope error during disconnect from {server_name}, Ignored context task mismatch"
                )
            else:
                raise e
        except Exception as e:
            logger.error(f"Error closing context stack for {server_name}: {e}")
            return e

    async def clean_up_server(self):
        """Clean up server connections individually"""
        for server_name in list(self.server_names):
            try:
                if (
                    server_name in self.sessions
                    and self.sessions[server_name]["connected"]
                ):
                    session_info = self.sessions[server_name]
                    await self._close_session(server_name, session_info)

                    if self.debug:
                        logger.info(f"Cleaned up server: {server_name}")

            except Exception as e:
                logger.error(f"Error cleaning up server {server_name}: {e}")

    async def cleanup(self):
        """Clean up all MCP connections."""
        try:
            logger.info("Starting client shutdown...")
            try:
                async with asyncio.timeout(60.0):
                    await self.clean_up_server()
            except asyncio.TimeoutError:
                logger.warning("Server cleanup timed out")
            except Exception as e:
                logger.error(f"Error during server cleanup: {e}")

            self.state.clear()

            logger.info("All MCP connections cleared")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
