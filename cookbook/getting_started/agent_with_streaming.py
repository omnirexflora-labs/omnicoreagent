"""Run with live text; stop early by closing the async iterator."""

import asyncio
from contextlib import aclosing

from omnicoreagent import OmniCoreAgent
from _bootstrap import model_config, require_llm_api_key


async def display_stream(agent, query):
    async with aclosing(agent.stream(query)) as events:
        async for event in events:
            if event["type"] == "text_delta":
                print(event["text"], end="", flush=True)
            elif event["type"] == "complete":
                print(f"\nStatus: {event['status']}")
                return event
            elif event["type"] == "error":
                raise RuntimeError(event["error"])


async def main():
    require_llm_api_key()
    agent = OmniCoreAgent(
        name="streaming_agent",
        system_instruction="Explain concepts clearly.",
        model_config=model_config(),
    )
    try:
        await display_stream(agent, "Explain how tool calling works.")
    finally:
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
