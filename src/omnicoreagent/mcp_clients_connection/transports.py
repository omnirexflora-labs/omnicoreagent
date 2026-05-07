from __future__ import annotations

import os
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from omnicoreagent.core.utils import logger


async def open_server_transport(
    *,
    stack: AsyncExitStack,
    server: dict[str, Any],
    oauth_auth: Any = None,
    debug: bool = False,
) -> tuple[Any, Any, str]:
    transport_type = server.get("transport_type", "stdio")
    normalized_transport = transport_type.lower()

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

    return await _open_stdio_transport(stack=stack, server=server)


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
    env = {**os.environ, **server["env"]} if server.get("env") else None
    server_params = StdioServerParameters(
        command=server["command"],
        args=server["args"],
        env=env,
    )
    read_stream, write_stream = await stack.enter_async_context(
        stdio_client(server_params)
    )
    return read_stream, write_stream, "stdio"
