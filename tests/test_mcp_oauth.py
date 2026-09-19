"""MCP OAuth against a real MCP 2 server that is its own authorization server.

Only the browser is replaced: a function visits the authorization URL and
follows the redirect to OmniCoreAgent's callback, as a person approving the
request would. Discovery, dynamic registration, PKCE, token exchange, and the
authenticated tool call are all real. The SDK validates the RFC 9207 ``iss``
when the callback supplies one, so a redirect from another issuer fails.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.mcp_clients_connection import oauth
from omnicoreagent.mcp_clients_connection.client import MCPClient

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_oauth_server.py")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def oauth_server():
    yield from _serve("streamable-http")


@pytest.fixture(scope="module", params=["streamable-http", "sse"])
def any_oauth_server(request):
    yield from _serve(request.param)


def _serve(transport: str):
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, SERVER, str(port), transport],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        path = "/mcp" if transport == "streamable-http" else "/sse"
        kind = "streamable_http" if transport == "streamable-http" else "sse"
        yield f"http://127.0.0.1:{port}", path, kind
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def approving_browser(monkeypatch):
    """Stands in for the person: visits the URL and follows the redirect."""
    visited: list[str] = []

    def open_url(url: str) -> bool:
        visited.append(url)

        def visit():
            # The authorization server redirects to OmniCoreAgent's callback.
            urllib.request.urlopen(url, timeout=10).read()

        threading.Thread(target=visit, daemon=True).start()
        return True

    monkeypatch.setattr(oauth.webbrowser, "open", open_url)
    return visited


@pytest.mark.asyncio
async def test_oauth_flow_authorizes_and_the_tool_call_succeeds(any_oauth_server, approving_browser):
    base, path, transport = any_oauth_server
    client = MCPClient(
        servers=[
            {
                "name": "secure",
                "transport_type": transport,
                "url": f"{base}{path}",
                "auth": {"method": "oauth"},
            }
        ]
    )

    gaps: list[float] = []

    async def heartbeat():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.wait_for(client.connect_to_servers(), 60)
    finally:
        beat.cancel()
    try:
        assert client.state.failures == {}
        assert [tool.name for tool in client.available_tools["secure"]] == ["whoami"]
        handler = MCPToolHandler(sessions=client.sessions, server_name="secure")
        result = await ToolExecutor(handler).execute("whoami", {})
    finally:
        await client.cleanup()

    assert result["data"] == "authorized"
    assert len(approving_browser) == 1
    # Waiting for the person must not block the event loop.
    assert max(gaps) < 0.5, max(gaps)



@pytest.mark.asyncio
async def test_a_redirect_from_another_issuer_is_rejected(
    oauth_server, approving_browser, monkeypatch
):
    real = oauth.AuthorizationCodeResult

    def from_attacker(code, state=None, iss=None):
        # A mix-up attack: the redirect claims a different authorization server.
        return real(code=code, state=state, iss="https://attacker.example")

    monkeypatch.setattr(oauth, "AuthorizationCodeResult", from_attacker)
    base, path, transport = oauth_server
    client = MCPClient(
        servers=[
            {
                "name": "secure",
                "transport_type": transport,
                "url": f"{base}{path}",
                "auth": {"method": "oauth"},
            }
        ]
    )
    try:
        await asyncio.wait_for(client.connect_to_servers(), 60)
        assert "secure" not in client.sessions
        failure = client.state.failures["secure"]
    finally:
        await client.cleanup()

    # The recorded reason is the real cause, not a task-group wrapper.
    assert "TaskGroup" not in failure["error"]
    assert "attacker.example" in failure["error"] or "issuer" in failure["error"].lower()

def test_the_provider_keeps_the_configured_server_url():
    provider = oauth.build_oauth_provider(server_url="https://api.example.com/v1/tools/mcp")
    assert provider.context.server_url == "https://api.example.com/v1/tools/mcp"


def test_the_redirect_uses_a_configured_or_free_loopback_port():
    configured = oauth.build_oauth_provider(
        server_url="https://example.com/mcp", callback_port=45123
    )
    free = oauth.build_oauth_provider(server_url="https://example.com/mcp")

    [configured_uri] = configured.context.client_metadata.redirect_uris
    [free_uri] = free.context.client_metadata.redirect_uris
    assert str(configured_uri) == "http://127.0.0.1:45123/callback"
    assert urlparse(str(free_uri)).hostname == "127.0.0.1"
    assert urlparse(str(free_uri)).port not in {3000, 3001, 3002}


@pytest.mark.asyncio
async def test_the_callback_returns_code_state_and_issuer():
    port = _free_port()
    callback = oauth.OAuthCallbackServer(port=port)
    await callback.start()
    try:
        url = f"http://127.0.0.1:{port}/callback?code=abc&state=xyz&iss=https%3A%2F%2Fauth.example.com"
        await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=5).read())
        result = await callback.wait(timeout=5)
    finally:
        callback.stop()

    assert (result.code, result.state, result.iss) == ("abc", "xyz", "https://auth.example.com")


@pytest.mark.asyncio
async def test_an_authorization_error_is_reported():
    port = _free_port()
    callback = oauth.OAuthCallbackServer(port=port)
    await callback.start()
    try:
        url = f"http://127.0.0.1:{port}/callback?error=access_denied"

        def visit():
            try:
                urllib.request.urlopen(url, timeout=5).read()
            except Exception:
                pass

        await asyncio.to_thread(visit)
        with pytest.raises(oauth.OAuthCallbackError, match="access_denied"):
            await callback.wait(timeout=5)
    finally:
        callback.stop()

