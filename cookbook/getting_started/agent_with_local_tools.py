#!/usr/bin/env python3
"""
Agent with Local Tools

Register Python functions as tools that your agent can call.
These are custom functions you write - perfect for business logic.

Build on: first_agent.py
Next: agent_with_mcp_tools.py

Run:
    python cookbook/getting_started/agent_with_local_tools.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, ToolRegistry


def create_tools() -> ToolRegistry:
    """Create a registry of custom local tools."""
    tools = ToolRegistry()

    @tools.register_tool("get_weather")
    def get_weather(city: str) -> str:
        """Get the current weather for a city."""
        # In production, call a real weather API
        return {"status": "success", "data": {"city": city, "weather": "Sunny, 22°C"}, "message": "Weather data retrieved successfully"}

    @tools.register_tool("calculate_area")
    def calculate_area(length: float, width: float) -> str:
        """Calculate the area of a rectangle."""
        try:
            area = length * width
            return {"status": "success", "data": {"length": length, "width": width, "area": area}, "message": "Area calculated successfully"}
        except Exception as e:
            return {"status": "error", "data": None, "message": "Failed to calculate area: " + str(e)}

    @tools.register_tool("get_time")
    def get_time() -> str:
        """Get the current time."""
        import time
        try:
            return {"status": "success", "data": {"time": time.strftime('%Y-%m-%d %H:%M:%S')}, "message": "Current time retrieved successfully"}
        except Exception as e:
            return {"status": "error", "data": None, "message": "Failed to retrieve current time: " + str(e)}

    return tools


async def main():


    # Create tools registry
    tools = create_tools()

    # Create agent with local tools
    agent = OmniCoreAgent(
        name="local_tools_agent",
        system_instruction="You are a helpful assistant with access to weather, math, and time tools.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        local_tools=tools  # <- Attach local tools here
    )

    # Agent automatically uses appropriate tools
    print("=" * 50)
    print("AGENT WITH LOCAL TOOLS")
    print("=" * 50)

    print("list all available tools")
    tools = await agent.list_all_available_tools()
    print(f"Available tools: {[t['name'] for t in tools]}")

    print("\nQuery 1: Weather")
    result = await agent.run("What's the weather in Tokyo?")
    print(f"Response: {result['response']}\n")

    print("Query 2: Math")
    result = await agent.run("Calculate the area of a room that's 5.5 meters by 4 meters")
    print(f"Response: {result['response']}\n")

    print("Query 3: Time")
    result = await agent.run("What time is it?")
    print(f"Response: {result['response']}\n")

    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
