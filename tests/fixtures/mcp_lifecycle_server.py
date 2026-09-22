"""A low-level MCP 2 server for lifecycle and error tests (stdio).

It pages its tool list, raises a real JSON-RPC protocol error, has a slow
tool, and reports its process ID so a test can kill it to drop the session.
Extra command-line arguments are ignored, so a test can tag the process.
"""

from __future__ import annotations

import asyncio
import os

from mcp import MCPError, types
from mcp.server import Server
from mcp.server.stdio import stdio_server

_SCHEMA = {"type": "object", "properties": {}}
PAGES = [
    ["echo", "pid"],
    ["slow", "protocol_error"],
    ["page_three_tool"],
]


def _tool(name: str) -> types.Tool:
    schema = (
        {"type": "object", "properties": {"text": {"type": "string"}}}
        if name == "echo"
        else _SCHEMA
    )
    return types.Tool(name=name, description=f"The {name} tool.", input_schema=schema)


async def list_tools(context, params):
    page = int((params.cursor if params else None) or 0)
    next_cursor = str(page + 1) if page + 1 < len(PAGES) else None
    return types.ListToolsResult(
        tools=[_tool(name) for name in PAGES[page]], next_cursor=next_cursor
    )


async def call_tool(context, params):
    arguments = params.arguments or {}
    if params.name == "echo":
        text = str(arguments.get("text", ""))
    elif params.name == "pid":
        text = str(os.getpid())
    elif params.name == "slow":
        await asyncio.sleep(30)
        text = "finished"
    elif params.name == "protocol_error":
        raise MCPError(code=types.INVALID_PARAMS, message="the server rejected this request")
    else:
        text = params.name
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


async def main():
    server = Server("lifecycle-server", version="0.4.0", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
