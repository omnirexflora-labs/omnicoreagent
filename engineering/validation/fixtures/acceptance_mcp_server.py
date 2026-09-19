"""MCP 2 stdio server for the trajectory acceptance scenario.

Its tools produce each outcome an MCP call can have: a structured result, a
tool error (``is_error``), a JSON-RPC protocol error, and a call slower than
the client's per-call timeout.
"""

from __future__ import annotations

import asyncio
import json

from mcp import MCPError, types
from mcp.server import Server
from mcp.server.stdio import stdio_server

_EMPTY = {"type": "object", "properties": {}}
TOOLS = [
    types.Tool(
        name="weather",
        description="Weather for a city.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    ),
    types.Tool(name="tool_error", description="Reports a tool failure.", input_schema=_EMPTY),
    types.Tool(name="protocol_error", description="Rejects the request.", input_schema=_EMPTY),
    types.Tool(name="wait_long", description="Answers after 30 seconds.", input_schema=_EMPTY),
]


async def list_tools(context, params):
    return types.ListToolsResult(tools=TOOLS)


async def call_tool(context, params):
    if params.name == "weather":
        weather = {"city": (params.arguments or {}).get("city"), "temp": 31, "sky": "clear"}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(weather))],
            structured_content=weather,
        )
    if params.name == "tool_error":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="weather service unavailable")],
            is_error=True,
        )
    if params.name == "protocol_error":
        raise MCPError(code=types.INVALID_PARAMS, message="the server rejected this request")
    await asyncio.sleep(30)
    return types.CallToolResult(content=[types.TextContent(type="text", text="late")])


async def main():
    server = Server("acceptance-mcp", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
