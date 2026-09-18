"""A real MCP 2 server for client tests: stdio, streamable HTTP, or SSE.

Usage: python mcp_probe_server.py stdio
       python mcp_probe_server.py streamable-http <port>
       python mcp_probe_server.py sse <port>

Its tools report what the client actually sent (headers, working directory,
environment), so tests prove the client's configuration reaches the server.
"""

from __future__ import annotations

import os
import sys

from pydantic import BaseModel

from mcp.server.mcpserver import Context, MCPServer

app = MCPServer("probe-server", version="1.2.3")


class Weather(BaseModel):
    city: str
    temp: int


@app.tool()
def forecast(city: str) -> Weather:
    """Forecast for a city."""
    return Weather(city=city, temp=31)


@app.tool()
def echo(text: str) -> str:
    """Echo text."""
    return text


@app.tool()
def fail(reason: str) -> str:
    """Always fails."""
    raise ValueError(reason)


@app.tool()
def request_header(name: str, ctx: Context) -> str:
    """The value of an HTTP request header the server received."""
    headers = ctx.headers or {}
    return str({key.lower(): value for key, value in headers.items()}.get(name.lower(), ""))


@app.tool()
def working_directory() -> str:
    """The server process's working directory."""
    return os.getcwd()


@app.tool()
def environment(name: str) -> str:
    """An environment variable of the server process."""
    return os.environ.get(name, "")


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "stdio":
        app.run("stdio")
    else:
        app.run(transport, port=int(sys.argv[2]))
