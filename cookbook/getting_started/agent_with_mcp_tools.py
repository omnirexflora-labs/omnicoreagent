#!/usr/bin/env python3
"""
Agent with MCP Tools

Connect to external MCP (Model Context Protocol) servers.
MCP servers provide tools for filesystem, databases, APIs, and more.

Build on: agent_with_local_tools.py
Next: agent_with_all_tools.py

Run:
    python cookbook/getting_started/agent_with_mcp_tools.py
"""

import asyncio
from pathlib import Path


from omnicoreagent import OmniCoreAgent


async def main():


    # Define MCP server configurations
    # Using stdio transport (local process communication)
    mcp_tools = [
        {
            "name": "filesystem",
            "transport_type": "stdio",  # Local process
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                str(Path.home()),  # Access home directory
            ]
        }
    ]

    # Create agent with MCP tools
    agent = OmniCoreAgent(
        name="mcp_tools_agent",
        system_instruction="You are a helpful assistant with access to filesystem tools.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        mcp_tools=mcp_tools  # <- Attach MCP tools here
    )

    # Connect to MCP servers (required before using MCP tools)
    await agent.connect_mcp_servers()

    # List available tools
    tools = await agent.list_all_available_tools()
    print("=" * 50)
    print("AGENT WITH MCP TOOLS")
    print("=" * 50)
    print(f"\nAvailable tools: {[t['name'] for t in tools]}")

    # Use the filesystem tools
    print("\nQuery: List files in home directory")
    result = await agent.run("List the files and folders in my home directory")
    print(f"Response: {result['response'][:500]}...")

    await agent.cleanup()


async def demo_other_transports():
    """
    Other MCP transport types you can use.
    These are for reference - uncomment and configure as needed.
    """

    # HTTP Transport (streamable http remote servers)
    http_config = {
        "name": "remote_api",
        "transport_type": "streamable_http",
        "url": "http://localhost:8080/mcp",
        "headers": {"Authorization": "Bearer your-token"},
        "timeout": 60
    }

    # SSE Transport (Server-Sent Events)
    sse_config = {
        "name": "live_data",
        "transport_type": "sse",
        "url": "http://localhost:3000/sse",
        "headers": {"Authorization": "Bearer token"},
        "sse_read_timeout": 120
    }

    # OAuth Transport (auto-handles OAuth flow)
    oauth_config = {
        "name": "oauth_server",
        "transport_type": "streamable_http",
        "auth": {"method": "oauth"},
        "url": "http://localhost:8000/mcp"
    }

    print("\nOther transport configurations available:")
    print(f"  - HTTP: {http_config['name']}")
    print(f"  - SSE: {sse_config['name']}")
    print(f"  - OAuth: {oauth_config['name']}")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(demo_other_transports())
