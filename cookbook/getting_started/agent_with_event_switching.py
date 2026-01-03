#!/usr/bin/env python3
"""
Agent with Event Router Switching

Switch event backends at RUNTIME without restarting.
Start with in-memory, switch to Redis Streams for production.

Build on: agent_with_events.py
This completes the Getting Started progression!

Run:
    python cookbook/getting_started/agent_with_event_switching.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, MemoryRouter, EventRouter


async def main():


    print("=" * 50)
    print("RUNTIME EVENT ROUTER SWITCHING")
    print("=" * 50)

    # Start with in-memory events
    event_router = EventRouter("in_memory")

    agent = OmniCoreAgent(
        name="event_switching_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=MemoryRouter("in_memory"),
        event_router=event_router
    )

    # Check current event store type
    current_type = await agent.get_event_store_type()
    print(f"\n1. Started with: {current_type}")

    # Run a query
    result = await agent.run("Hello, testing event switching!")
    print(f"   Response: {result['response'][:80]}...")

    # === SWITCH TO REDIS STREAMS ===
    # Note: Requires REDIS_URL in .env before the agent is initialized
    print("\n2. Switching to Redis Streams...")
    try:
        await agent.switch_event_store("redis_stream")
        current_type = await agent.get_event_store_type()
        print(f"   Now using: {current_type}")
    except Exception as e:
        print(f"   Redis Streams not available: {e}")
        print("   (Set REDIS_URL in .env to enable)")

    # === SWITCH BACK TO IN-MEMORY ===
    print("\n3. Switching back to in-memory...")
    await agent.switch_event_store("in_memory")
    current_type = await agent.get_event_store_type()
    print(f"   Now using: {current_type}")

    await agent.cleanup()

    print("\n" + "=" * 50)
    print("EVENT STORE BACKENDS")
    print("=" * 50)
    print("""
| Backend      | Use Case                     | Environment Variable |
|--------------|------------------------------|---------------------|
| in_memory    | Development, testing         | (none)              |
| redis_stream | Production, distributed      | REDIS_URL           |

Switch at runtime:
    await agent.switch_event_store("redis_stream")
    await agent.switch_event_store("in_memory")

Get current type:
    await agent.get_event_store_type()
""")


async def demo_complete_setup():
    """
    Demo a complete production-ready setup.
    Combines all the concepts from Getting Started.

    """
    print("\n" + "=" * 50)
    print("COMPLETE PRODUCTION SETUP")
    print("=" * 50)

    # For production, use Redis for both memory and events
    # memory_router = MemoryRouter("redis")
    # event_router = EventRouter("redis_stream")

    # For development, use in-memory
    memory_router = MemoryRouter("in_memory")
    event_router = EventRouter("in_memory")

    agent = OmniCoreAgent(
        name="production_ready_agent",
        system_instruction="You are a production-ready assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router,
        event_router=event_router,
        agent_config={
            "max_steps": 15,
            "tool_call_timeout": 60,
        }
    )

    print("\nAgent configured with:")
    print(f"  Memory: {await agent.get_memory_store_type()}")
    print(f"  Events: {await agent.get_event_store_type()}")

    result = await agent.run("What is OmniCoreAgent?")
    print(f"\nResponse: {result['response'][:200]}...")

    await agent.cleanup()
    print("\n✅ Complete setup working!")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(demo_complete_setup())
