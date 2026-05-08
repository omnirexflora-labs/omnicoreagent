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

from _bootstrap import model_config, require_llm_api_key, response_text


async def main():
    require_llm_api_key()

    # Create a minimal agent
    agent = OmniCoreAgent(
        name="my_first_agent",
        system_instruction="You are a helpful assistant.",
        model_config=model_config(max_tokens=500),
    )

    # Run a query
    result = await agent.run("Hello! What can you help me with?")
    print(f"Response: {response_text(result)}")
    print(f"Session ID: {result['session_id']}")
    print(f"Metrics: {result['metric']}")

    # Clean up resources
    await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
