"""Synthetic MCP 2 server for the native-runtime audit (no external services)."""

import asyncio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


async def list_tools(context, params):
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="remote_echo",
                description="Return a synthetic value",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            )
        ]
    )


async def call_tool(context, params):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=params.arguments["value"])]
    )


async def main():
    server = Server("native-audit", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
