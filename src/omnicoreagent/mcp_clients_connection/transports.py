from __future__ import annotations

import os
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from omnicoreagent.core.logging import logger

STDIO_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_PATH",
    }
)
SUPPORTED_TRANSPORTS = frozenset({"stdio", "sse", "streamable_http"})


async def open_server_transport(
    *,
    stack: AsyncExitStack,
    server: dict[str, Any],
    oauth_auth: Any = None,
    debug: bool = False,
) -> tuple[Any, Any, str]:
    normalized_transport = normalize_transport_type(server)

    if normalized_transport == "sse":
        return await _open_sse_transport(
            stack=stack,
            server=server,
            oauth_auth=oauth_auth,
            debug=debug,
        )

    if normalized_transport == "streamable_http":
        return await _open_streamable_http_transport(
            stack=stack,
            server=server,
            oauth_auth=oauth_auth,
            debug=debug,
        )

    if normalized_transport == "stdio":
        return await _open_stdio_transport(stack=stack, server=server)

    raise ValueError(f"Unsupported MCP transport_type: {normalized_transport}")


def normalize_transport_type(server: dict[str, Any]) -> str:
    transport = str(server.get("transport_type", "stdio")).lower()
    if transport not in SUPPORTED_TRANSPORTS:
        supported = ", ".join(sorted(SUPPORTED_TRANSPORTS))
        raise ValueError(
            f"Unsupported MCP transport_type: {transport}. Supported: {supported}"
        )
    return transport


async def _open_sse_transport(
    *,
    stack: AsyncExitStack,
    server: dict[str, Any],
    oauth_auth: Any = None,
    debug: bool = False,
) -> tuple[Any, Any, str]:
    url = server.get("url", "")
    timeout = server.get("timeout", 60)
    sse_read_timeout = server.get("sse_read_timeout", 120)
    if debug:
        logger.info(f"SSE connection to {url} with timeout {timeout}")

    client_kwargs = {
        "url": url,
        "headers": server.get("headers", {}),
        "timeout": timeout,
        "sse_read_timeout": sse_read_timeout,
    }
    if oauth_auth is not None:
        client_kwargs["auth"] = oauth_auth

    read_stream, write_stream = await stack.enter_async_context(
        sse_client(**client_kwargs)
    )
    return read_stream, write_stream, "sse"


async def _open_streamable_http_transport(
    *,
    stack: AsyncExitStack,
    server: dict[str, Any],
    oauth_auth: Any = None,
    debug: bool = False,
) -> tuple[Any, Any, str]:
    url = server.get("url", "")
    timeout = timedelta(seconds=int(server.get("timeout", 60)))
    sse_read_timeout = timedelta(seconds=int(server.get("sse_read_timeout", 120)))
    if debug:
        logger.info(f"Streamable HTTP connection to {url} with timeout {timeout}")

    client_kwargs = {
        "url": url,
        "headers": server.get("headers", {}),
        "timeout": timeout,
        "sse_read_timeout": sse_read_timeout,
    }
    if oauth_auth is not None:
        client_kwargs["auth"] = oauth_auth

    read_stream, write_stream, _ = await stack.enter_async_context(
        streamable_http_client(**client_kwargs)
    )
    return read_stream, write_stream, "streamable_http"


async def _open_stdio_transport(
    *,
    stack: AsyncExitStack,
    server: dict[str, Any],
) -> tuple[Any, Any, str]:
    server_params = StdioServerParameters(
        command=server["command"],
        args=server["args"],
        env=build_stdio_env(server),
    )
    read_stream, write_stream = await stack.enter_async_context(
        stdio_client(server_params)
    )
    return read_stream, write_stream, "stdio"


def build_stdio_env(server: dict[str, Any]) -> dict[str, str] | None:
    """Return a scrubbed stdio env plus explicit app-owned overrides."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in STDIO_ENV_ALLOWLIST or key.startswith("LC_")
    }
    explicit_env = server.get("env") or {}
    env.update({str(key): str(value) for key, value in explicit_env.items()})
    return env
