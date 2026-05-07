from __future__ import annotations

import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from omnicoreagent.core.logging import logger


class InMemoryTokenStorage(TokenStorage):
    """Simple in-memory OAuth token storage."""

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
    """HTTP handler that captures an OAuth redirect callback."""

    def __init__(self, request, client_address, server, callback_data):
        self.callback_data = callback_data
        super().__init__(request, client_address, server)

    def do_GET(self):
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
                <h1>Authorization Successful</h1>
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
        pass


class CallbackServer:
    """Small local callback server for interactive MCP OAuth."""

    def __init__(self, port=3000):
        self.port = port
        self.server = None
        self.thread = None
        self.callback_data = {"authorization_code": None, "state": None, "error": None}

    def _create_handler_with_data(self):
        callback_data = self.callback_data

        class DataCallbackHandler(CallbackHandler):
            def __init__(self, request, client_address, server):
                super().__init__(request, client_address, server, callback_data)

        return DataCallbackHandler

    def start(self):
        handler_class = self._create_handler_with_data()
        self.server = HTTPServer(("localhost", self.port), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Started callback server on http://localhost:{self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=1)

    def wait_for_callback(self, timeout=300):
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.callback_data["authorization_code"]:
                return self.callback_data["authorization_code"]
            if self.callback_data["error"]:
                raise Exception(f"OAuth error: {self.callback_data['error']}")
            time.sleep(0.1)
        raise Exception("Timeout waiting for OAuth callback")

    def get_state(self):
        return self.callback_data["state"]


def is_oauth_enabled(server: dict) -> bool:
    auth_config = server.get("auth", None)
    return bool(auth_config and auth_config.get("method") == "oauth")


def build_oauth_provider(*, server_url: str, callback_port: int) -> OAuthClientProvider:
    callback_server = CallbackServer(port=callback_port)
    callback_server.start()

    async def callback_handler() -> tuple[str, str | None]:
        logger.info("Waiting for authorization callback...")
        try:
            auth_code = callback_server.wait_for_callback(timeout=300)
            return auth_code, callback_server.get_state()
        finally:
            callback_server.stop()

    async def redirect_handler(authorization_url: str) -> None:
        logger.info(f"Opening browser for authorization: {authorization_url}")
        webbrowser.open(authorization_url)

    client_metadata_dict = {
        "client_name": "omnicoreagent",
        "redirect_uris": [f"http://localhost:{callback_port}/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }

    return OAuthClientProvider(
        server_url=server_url.replace("/mcp", "").replace("/sse", ""),
        client_metadata=OAuthClientMetadata.model_validate(client_metadata_dict),
        storage=InMemoryTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
