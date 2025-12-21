import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
import anyio
from dotenv import load_dotenv
from decouple import config as decouple_config
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.mcp_clients_connection.notifications import handle_notifications
from omnicoreagent.mcp_clients_connection.refresh_server_capabilities import (
    refresh_capabilities,
)
from omnicoreagent.mcp_clients_connection.sampling import samplingCallback
from omnicoreagent.core.utils import logger
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
import webbrowser
import threading
import time


class InMemoryTokenStorage(TokenStorage):
    """Simple in-memory token storage implementation."""

    def __init__(self):
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info


class CallbackHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler to capture OAuth callback."""

    def __init__(self, request, client_address, server, callback_data):
        """Initialize with callback data storage."""
        self.callback_data = callback_data
        super().__init__(request, client_address, server)

    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)

        if "code" in query_params:
            self.callback_data["authorization_code"] = query_params["code"][0]
            self.callback_data["state"] = query_params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html>
            <body>
                <h1>Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                <script>setTimeout(() => window.close(), 2000);</script>
            </body>
            </html>
            """)
        elif "error" in query_params:
            self.callback_data["error"] = query_params["error"][0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"""
            <html>
            <body>
                <h1>Authorization Failed</h1>
                <p>Error: {query_params["error"][0]}</p>
                <p>You can close this window and return to the terminal.</p>
            </body>
            </html>
            """.encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class CallbackServer:
    """Simple server to handle OAuth callbacks."""

    def __init__(self, port=3000):
        self.port = port
        self.server = None
        self.thread = None
        self.callback_data = {"authorization_code": None, "state": None, "error": None}

    def _create_handler_with_data(self):
        """Create a handler class with access to callback data."""
        callback_data = self.callback_data

        class DataCallbackHandler(CallbackHandler):
            def __init__(self, request, client_address, server):
                super().__init__(request, client_address, server, callback_data)

        return DataCallbackHandler

    def start(self):
        """Start the callback server in a background thread."""
        handler_class = self._create_handler_with_data()
        self.server = HTTPServer(("localhost", self.port), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"🖥️  Started callback server on http://localhost:{self.port}")

    def stop(self):
        """Stop the callback server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)

    def wait_for_callback(self, timeout=300):
        """Wait for OAuth callback with timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.callback_data["authorization_code"]:
                return self.callback_data["authorization_code"]
            elif self.callback_data["error"]:
                raise Exception(f"OAuth error: {self.callback_data['error']}")
            time.sleep(0.1)
        raise Exception("Timeout waiting for OAuth callback")

    def get_state(self):
        """Get the received state parameter."""
        return self.callback_data["state"]


@dataclass
class Configuration:
    """Manages configuration and environment variables for the MCP client."""

    llm_api_key: str = field(init=False)
    embedding_api_key: str = field(init=False)

    def __post_init__(self) -> None:
        """Initialize configuration with environment variables."""
        self.load_env()
        self.llm_api_key = decouple_config("LLM_API_KEY", default=None)
        self.embedding_api_key = decouple_config("EMBEDDING_API_KEY", default=None)

        if not self.llm_api_key:
            raise ValueError("LLM_API_KEY not found in environment variables")

        # Note: embedding_api_key is not enforced here - only when calling embedding methods

    @staticmethod
    def load_env() -> None:
        """Load environment variables from .env file."""
        load_dotenv()

    def load_config(self, file_path: str) -> dict:
        """Load server configuration from JSON file."""
        config_path = Path(file_path)
        logger.info(f"Loading configuration from: {config_path.name}")

        # First check: filename must start with "servers_config"
        if not config_path.name.startswith("servers_config"):
            raise ValueError("Config file name must start with 'servers_config'")

        # Second check: if it's a full path (agent config), use it directly
        if config_path.is_absolute() or config_path.parent != Path("."):
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    return json.load(f)
            else:
                raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Third check: use as relative path in current directory which is mcpclient
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return json.load(f)

        raise FileNotFoundError(f"Configuration file not found: {config_path}")


class MCPClient:
    def __init__(
        self,
        config: dict[str, Any],
        debug: bool = False,
        config_filename: str = "servers_config.json",
    ):
        # Initialize session and client objects
        self.config = config
        self.config_filename = config_filename
        self.sessions = {}
        self._cleanup_lock = asyncio.Lock()
        self.available_tools = {}
        self.available_resources = {}
        self.available_prompts = {}
        self.server_names = []
        self.added_servers_names = {}  # this to map the name used in the config and the actual server name gotten after initialization
        self.debug = debug
        self.system_prompt = None
        # Create LLM connection only if LLM configuration is available
        self.llm_connection = None
        if self.config and hasattr(self.config, "llm_api_key"):
            try:
                self.llm_connection = LLMConnection(self.config, self.config_filename)
                if self.llm_connection and self.llm_connection.llm_config:
                    logger.debug("LLM connection initialized successfully")
                else:
                    logger.debug(
                        "LLM configuration not available, LLM features will be disabled"
                    )
                    self.llm_connection = None
            except Exception as e:
                logger.warning(f"Failed to initialize LLM connection: {e}")
                self.llm_connection = None
        self.sampling_callback = samplingCallback()
        self.tasks = {}
        self.server_count = 0

    async def connect_to_servers(self, config_filename: str = "servers_config.json"):
        """Connect to an MCP server"""
        server_config = self.config.load_config(config_filename)
        servers = [
            {"name": name, "srv_config": srv_config}
            for name, srv_config in server_config["mcpServers"].items()
        ]
        try:
            connect_tasks = [
                self._connect_to_single_server(server, server["name"])
                for server in servers
            ]
            results = await asyncio.gather(*connect_tasks, return_exceptions=True)

            # Log any failed connections
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Server connection failed: {result}")
                logger.info(f"Server connection result: {result}")
        except Exception as e:
            logger.info(f"start servers task error: {e}")
        # start the notification stream with an asyncio task
        asyncio.create_task(
            handle_notifications(
                sessions=self.sessions,
                debug=self.debug,
                server_names=self.server_names,
                available_tools=self.available_tools,
                available_resources=self.available_resources,
                available_prompts=self.available_prompts,
                refresh_capabilities=refresh_capabilities,
            )
        )

    async def _connect_to_single_server(self, server, server_added_name):
        try:
            # create AsyncExitStack per mcp server to ensure we can remove it safely without cancelling all tasks
            stack = AsyncExitStack()
            transport_type = server["srv_config"].get("transport_type", "stdio")
            read_stream = None
            write_stream = None
            url = server["srv_config"].get("url", "")
            headers = server["srv_config"].get("headers", {})
            timeout = server["srv_config"].get("timeout", 60)
            sse_read_timeout = server["srv_config"].get("sse_read_timeout", 120)
            auth_config = server["srv_config"].get("auth", None)
            use_oauth = auth_config and auth_config.get("method") == "oauth"

            # Set up callback server
            self.server_count += 1
            callback_port = 3000 + self.server_count
            callback_server = CallbackServer(port=callback_port)
            oauth_auth = None
            # start the callback server if use_oauth
            if use_oauth:
                callback_server.start()

                async def callback_handler() -> tuple[str, str | None]:
                    """Wait for OAuth callback and return auth code and state."""
                    logger.info("⏳ Waiting for authorization callback...")
                    try:
                        auth_code = callback_server.wait_for_callback(timeout=300)
                        return auth_code, callback_server.get_state()
                    finally:
                        callback_server.stop()

                async def _default_redirect_handler(authorization_url: str) -> None:
                    """Default redirect handler that opens the URL in a browser."""
                    logger.info(
                        f"Opening browser for authorization: {authorization_url}"
                    )
                    webbrowser.open(authorization_url)

                client_metadata_dict = {
                    "client_name": "mcpomni_connect",
                    "redirect_uris": [f"http://localhost:{callback_port}/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                }
                # Create OAuth authentication handler using the new interface

                oauth_auth = OAuthClientProvider(
                    server_url=url.replace("/mcp", "").replace("/sse", ""),
                    client_metadata=OAuthClientMetadata.model_validate(
                        client_metadata_dict
                    ),
                    storage=InMemoryTokenStorage(),
                    redirect_handler=_default_redirect_handler,
                    callback_handler=callback_handler,
                )
            if transport_type.lower() == "sse":
                if self.debug:
                    logger.info(f"SSE connection to {url} with timeout {timeout}")
                client_kwargs = {
                    "url": url,
                    "headers": headers,
                    "timeout": timeout,
                    "sse_read_timeout": sse_read_timeout,
                }
                if use_oauth:
                    client_kwargs["auth"] = oauth_auth
                transport = await stack.enter_async_context(sse_client(**client_kwargs))
                read_stream, write_stream = transport
            elif transport_type.lower() == "streamable_http":
                if self.debug:
                    logger.info(
                        f"Streamable HTTP connection to {url} with timeout {timeout}"
                    )
                timeout = timedelta(seconds=int(timeout))
                sse_read_timeout = timedelta(seconds=int(sse_read_timeout))
                client_kwargs = {
                    "url": url,
                    "headers": headers,
                    "timeout": timeout,
                    "sse_read_timeout": sse_read_timeout,
                }
                if use_oauth:
                    client_kwargs["auth"] = oauth_auth
                transport = await stack.enter_async_context(
                    streamablehttp_client(**client_kwargs)
                )
                read_stream, write_stream, _ = transport
            else:
                # stdio connection (default)
                args = server["srv_config"]["args"]
                command = server["srv_config"]["command"]
                env = (
                    {**os.environ, **server["srv_config"]["env"]}
                    if server["srv_config"].get("env")
                    else None
                )
                server_params = StdioServerParameters(
                    command=command, args=args, env=env
                )
                transport = await stack.enter_async_context(stdio_client(server_params))

                read_stream, write_stream = transport

            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    sampling_callback=self.sampling_callback._sampling,
                    read_timeout_seconds=timedelta(seconds=300),  # 5 minutes timeout
                )
            )
            init_result = await session.initialize()
            server_name = init_result.serverInfo.name
            capabilities = init_result.capabilities
            if server_name in self.server_names:
                error_message = (
                    f"{server_name} is already connected. disconnect it and try again"
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
                "capabilities": capabilities,
                "transport_type": transport_type,
                "stack": stack,
            }
            if self.debug:
                logger.info(
                    f"Successfully connected to {server_name} via {transport_type}"
                )
            # refresh capabilities to ensure we have the latest tools, resources, and prompts
            await refresh_capabilities(
                sessions=self.sessions,
                server_names=self.server_names,
                available_tools=self.available_tools,
                available_resources=self.available_resources,
                available_prompts=self.available_prompts,
                debug=self.debug,
            )

            return f"{server_name} connected succesfully"
        except Exception as e:
            error_message = f"Failed to connect to server: {str(e)}"
            logger.error(error_message)
            return error_message

    async def add_servers(self, config_file: Path) -> None:
        """Dynamically add servers at runtime."""
        with open(config_file, "r") as f:
            server_config = json.load(f)

        servers = [
            {"name": name, "srv_config": srv_config}
            for name, srv_config in server_config["mcpServers"].items()
        ]
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
                        f"{server_added_name} connected succesfully"
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
            if name not in self.added_servers_names.keys():
                raise ValueError(f"Server '{name}' not found.")
            if len(self.sessions) == 1:
                return (
                    f"Cannot remove {name}: at least one server must remain connected."
                )
            for server_added_name, server_name in self.added_servers_names.items():
                if name.lower() == server_added_name.lower():
                    name = server_name
            session_info = self.sessions[name]
            await self._close_session_resources(
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
        self.available_resources.pop(name, None)
        self.available_prompts.pop(name, None)

        return f"{name} diconnected succesfully"

        logger.info(f"Server '{name}' removed successfully.")

    async def _close_session_resources(self, server_name: str, session_info: dict):
        """Tear down the per-server context stack, which closes streams and session."""

        stack: AsyncExitStack = session_info.get("stack")
        if not stack:
            logger.warning(f"No context stack found for {server_name}")
            return
        try:
            logger.info(f"Closing context stack for {server_name}")
            # Ensure transport and session resources are cleaned up
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
                    await self._close_session_resources(server_name, session_info)

                    if self.debug:
                        logger.info(f"Cleaned up server: {server_name}")

            except Exception as e:
                logger.error(f"Error cleaning up server {server_name}: {e}")

    async def cleanup(self):
        """Clean up all resources"""
        try:
            logger.info("Starting client shutdown...")
            try:
                async with asyncio.timeout(
                    60.0
                ):  # 60 second timeout for server cleanup
                    await self.clean_up_server()
            except asyncio.TimeoutError:
                logger.warning("Server cleanup timed out")
            except Exception as e:
                logger.error(f"Error during server cleanup: {e}")

            # Clear any remaining data structures
            self.server_names.clear()
            self.added_servers_names.clear()
            self.sessions.clear()
            self.available_tools.clear()
            self.available_resources.clear()
            self.available_prompts.clear()

            logger.info("All resources cleared")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
