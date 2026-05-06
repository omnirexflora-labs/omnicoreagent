import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any
import anyio
from mcp import ClientSession

from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.core.utils import logger
from omnicoreagent.mcp_clients_connection.oauth import (
    build_oauth_provider,
    is_oauth_enabled,
)
from omnicoreagent.mcp_clients_connection.transports import open_server_transport


class MCPClient:
    def __init__(
        self,
        servers: list[dict[str, Any]] | None = None,
        model_config: dict[str, Any] | None = None,
        api_key: str | None = None,
        debug: bool = False,
    ):
        self.servers = self._normalize_servers(servers or [])
        self.sessions = {}
        self.available_tools = {}
        self.server_names = []
        self.added_servers_names = {}
        self.debug = debug
        self.llm_connection = (
            LLMConnection(model_config=model_config, api_key=api_key)
            if model_config
            else None
        )
        self.server_count = 0

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
                logger.info(f"Server connection result: {result}")
        except Exception as e:
            logger.info(f"start servers task error: {e}")

    async def _connect_to_single_server(self, server, server_added_name):
        try:
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
            if server_name in self.server_names:
                error_message = (
                    f"{server_name} is already connected. Disconnect it and try again."
                )
                if self.debug:
                    logger.error(error_message)
                await stack.aclose()
                return error_message
            self.server_names.append(server_name)
            server_name_data = {server_added_name: server_name}
            self.added_servers_names.update(server_name_data)
            self.sessions[server_name] = {
                "session": session,
                "read_stream": read_stream,
                "write_stream": write_stream,
                "connected": True,
                "transport_type": transport_type,
                "stack": stack,
            }
            if self.debug:
                logger.info(
                    f"Successfully connected to {server_name} via {transport_type}"
                )
            await self._load_server_tools(server_name)

            return f"{server_name} connected successfully"
        except Exception as e:
            error_message = f"Failed to connect to server: {str(e)}"
            logger.error(error_message)
            return error_message

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

        self.available_tools[server_name] = tools
        if self.debug:
            logger.info(f"Loaded {len(tools)} MCP tools from {server_name}")
            for tool in tools:
                logger.info(f"  - {tool.name}")
        return tools

    async def add_servers(self, servers: list[dict[str, Any]]) -> list[Any]:
        """Dynamically add servers at runtime."""
        servers = self._normalize_servers(servers)
        errors = []
        servers_connected_response = []
        try:
            server_added_name = None
            async with anyio.create_task_group() as tg:
                for server in servers:
                    server_added_name = server["name"]
                    tg.start_soon(
                        self._connect_to_single_server, server, server_added_name
                    )
                    servers_connected_response.append(
                        f"{server_added_name} connected successfully"
                    )
        except Exception as e:
            logger.error(f"Failed to add server '{server_added_name}': {e}")
            errors.append((server_added_name, str(e)))
        if errors:
            return errors
        return servers_connected_response

    async def remove_server(self, name: str) -> None:
        """Disconnect and remove a server by name."""
        try:
            old_name = name
            if name not in self.added_servers_names:
                raise ValueError(f"Server '{name}' not found.")
            if len(self.sessions) == 1:
                return (
                    f"Cannot remove {name}: at least one server must remain connected."
                )
            for server_added_name, server_name in self.added_servers_names.items():
                if name.lower() == server_added_name.lower():
                    name = server_name
            session_info = self.sessions[name]
            await self._close_session(
                server_name=old_name, session_info=session_info
            )
        except ValueError as e:
            error_message = f"Error removing server: {str(e)}"
            logger.error(error_message)
            return error_message
        except Exception as e:
            error_message = f"Error cleaning up server '{name}': {e}"
            logger.error(error_message)
            return error_message

        self.sessions.pop(name, None)
        self.server_names.remove(name)
        self.added_servers_names = {
            k: v for k, v in self.added_servers_names.items() if v != name
        }
        self.available_tools.pop(name, None)

        logger.info(f"Server '{name}' removed successfully.")
        return f"{name} disconnected successfully"

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

            self.server_names.clear()
            self.added_servers_names.clear()
            self.sessions.clear()
            self.available_tools.clear()

            logger.info("All MCP connections cleared")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
