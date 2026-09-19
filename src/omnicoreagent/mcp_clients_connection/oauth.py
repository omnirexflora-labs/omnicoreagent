"""Interactive OAuth for MCP servers (authorization code with PKCE).

The MCP SDK's ``OAuthClientProvider`` runs the protocol: discovery, dynamic
client registration, PKCE, the RFC 9207 issuer check, and token exchange.
This module supplies the two interactive parts: opening the authorization
URL in a browser and receiving the redirect on a loopback callback server.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from omnicoreagent.core.logging import logger

DEFAULT_CALLBACK_TIMEOUT_SECONDS = 300.0
_LOOPBACK = "127.0.0.1"

_SUCCESS_PAGE = b"""<html><body><h1>Authorization successful</h1>
<p>You can close this window and return to your application.</p></body></html>"""
_FAILURE_PAGE = b"""<html><body><h1>Authorization failed</h1>
<p>You can close this window and return to your application.</p></body></html>"""


class OAuthCallbackError(Exception):
    """The authorization server redirected with an error, or no redirect came."""


class InMemoryTokenStorage(TokenStorage):
    """OAuth tokens and client registration kept for the process lifetime."""

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


class OAuthCallbackServer:
    """Loopback HTTP server that receives one authorization redirect.

    The HTTP server runs in a thread; the redirect is handed to the event loop
    through a future, so waiting never blocks the loop.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._result: asyncio.Future[AuthorizationCodeResult] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        result = self._result

        def deliver(outcome: AuthorizationCodeResult | BaseException) -> None:
            if result.done():
                return
            if isinstance(outcome, BaseException):
                result.set_exception(outcome)
            else:
                result.set_result(outcome)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                params = parse_qs(urlparse(self.path).query)
                first = {key: values[0] for key, values in params.items()}
                if "code" in first:
                    outcome: Any = AuthorizationCodeResult(
                        code=first["code"], state=first.get("state"), iss=first.get("iss")
                    )
                    self._reply(200, _SUCCESS_PAGE)
                elif "error" in first:
                    description = first.get("error_description")
                    outcome = OAuthCallbackError(
                        f"Authorization failed: {first['error']}"
                        + (f" ({description})" if description else "")
                    )
                    self._reply(400, _FAILURE_PAGE)
                else:
                    self._reply(404, b"")
                    return
                loop.call_soon_threadsafe(deliver, outcome)

            def _reply(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self._server = HTTPServer((_LOOPBACK, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"OAuth callback listening on http://{_LOOPBACK}:{self.port}/callback")

    async def wait(self, timeout: float) -> AuthorizationCodeResult:
        assert self._result is not None, "start() first"
        try:
            return await asyncio.wait_for(asyncio.shield(self._result), timeout)
        except asyncio.TimeoutError:
            raise OAuthCallbackError(
                f"No authorization redirect within {timeout:g}s"
            ) from None

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None


def is_oauth_enabled(server: dict) -> bool:
    auth_config = server.get("auth", None)
    return bool(auth_config and auth_config.get("method") == "oauth")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((_LOOPBACK, 0))
        return sock.getsockname()[1]


def build_oauth_provider(
    *,
    server_url: str,
    callback_port: int | None = None,
    callback_timeout: float = DEFAULT_CALLBACK_TIMEOUT_SECONDS,
) -> OAuthClientProvider:
    """An httpx2 auth handler that logs in through the browser when needed.

    The callback server starts only when a login is actually required, and
    stops once the redirect arrives.
    """
    port = callback_port or _free_port()
    callback = OAuthCallbackServer(port=port)

    async def redirect_handler(authorization_url: str) -> None:
        await callback.start()
        logger.info(f"Opening browser for MCP authorization: {authorization_url}")
        # Launching a browser can block; keep it off the event loop.
        await asyncio.to_thread(webbrowser.open, authorization_url)

    async def callback_handler() -> AuthorizationCodeResult:
        try:
            return await callback.wait(timeout=callback_timeout)
        finally:
            # shutdown() waits for the server thread's poll; keep it off the loop.
            await asyncio.to_thread(callback.stop)

    client_metadata = OAuthClientMetadata.model_validate(
        {
            "client_name": "omnicoreagent",
            "redirect_uris": [f"http://{_LOOPBACK}:{port}/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=client_metadata,
        storage=InMemoryTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
