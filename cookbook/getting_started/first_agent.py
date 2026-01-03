#!/usr/bin/env python3
"""
First Agent Example

Create a minimal OmniCoreAgent in ~20 lines.
This is the simplest possible agent - just query and response.

Run:
    python cookbook/getting_started/first_agent.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent


async def main():
    
    # Create a minimal agent
    agent = OmniCoreAgent(
        name="my_first_agent",
        system_instruction="You are a helpful assistant.",
        model_config={
            "provider": "openai",
            "model": "gpt-4o"
        }
    )

    # Run a query
    result = await agent.run("Hello! What can you help me with?")
    print(f"Response: {result['response']}")
    print(f"Session ID: {result['session_id']}")
    print(f"Metrics: {result['metric']}")

    # Clean up resources
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
