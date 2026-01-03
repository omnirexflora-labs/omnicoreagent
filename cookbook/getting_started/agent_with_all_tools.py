#!/usr/bin/env python3
"""
Agent with All Tools (Local + MCP)

Combine local Python tools with external MCP tools.
Best of both worlds: custom business logic + external services.

Build on: agent_with_mcp_tools.py
Next: agent_with_memory.py

Run:
    python cookbook/getting_started/agent_with_all_tools.py
"""

import asyncio
from pathlib import Path


from omnicoreagent import OmniCoreAgent, ToolRegistry


def create_local_tools() -> ToolRegistry:
    """Create custom business logic tools."""
    tools = ToolRegistry()

    @tools.register_tool("analyze_file_count")
    def analyze_file_count(count: int) -> str:
        """Analyze the number of files in a directory."""
        try:
            if count < 10:
                return {"status": "success", "data": {"count": count}, "message": "This directory has {count} files - very clean and organized!"}
            elif count < 50:
                return {"status": "success", "data": {"count": count}, "message": "This directory has {count} files - moderate amount."}
            else:
                return {"status": "success", "data": {"count": count}, "message": "This directory has {count} files - consider organizing!"}
        except Exception as e:
            return {"status": "error", "data": None, "message": "Failed to analyze file count: " + str(e)}

    @tools.register_tool("recommend_action")
    def recommend_action(file_type: str, description: str) -> dict:
        """Recommend an action based on file type and description."""
        recommendations = {
            "python": "Consider running tests and linting",
            "javascript": "Check for npm vulnerabilities",
            "config": "Back up configuration files regularly",
        }
        return {"status": "success", "data": recommendations.get(file_type, "Review and organize as needed"), "message": "Recommendation provided successfully"}

    return tools


async def main():


    # Local tools (custom Python functions)
    local_tools = create_local_tools()

    # MCP tools (external services)
    mcp_tools = [
        {
            "name": "filesystem",
            "transport_type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                str(Path.home()),
            ]
        }
    ]

    # Create agent with BOTH local and MCP tools
    agent = OmniCoreAgent(
        name="hybrid_tools_agent",
        system_instruction="""You are a helpful assistant with access to:
        - Filesystem tools (MCP) - to read/write files
        - Analysis tools (Local) - to analyze and recommend actions
        
        Use the filesystem tools to get information, then use local tools to analyze it.""",
        model_config={"provider": "openai", "model": "gpt-4o"},
        local_tools=local_tools,  # <- Local Python tools
        mcp_tools=mcp_tools       # <- MCP server tools
    )

    # Connect to MCP servers
    await agent.connect_mcp_servers()

    # List all available tools (local + MCP combined)
    tools = await agent.list_all_available_tools()
    print("=" * 50)
    print("AGENT WITH ALL TOOLS (LOCAL + MCP)")
    print("=" * 50)
    print(f"\nTotal tools available: {len(tools)}")
    print(f"Tools: {[t['name'] for t in tools]}")

    # Use combined tools
    print("\nQuery: Analyze my home directory")
    result = await agent.run(
        "List the files in my home directory, count how many there are, and give me a recommendation"
    )
    print(f"Response: {result['response'][:600]}...")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
