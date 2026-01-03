#!/usr/bin/env python3
"""
Agent with Memory Switching

Switch memory backends at RUNTIME without restarting.
Start with Redis, switch to MongoDB, then PostgreSQL - seamlessly.

Build on: agent_with_memory.py
Next: agent_with_events.py

Run:
    python cookbook/getting_started/agent_with_memory_switching.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, MemoryRouter


async def main():


    print("=" * 50)
    print("RUNTIME MEMORY SWITCHING")
    print("=" * 50)

    # Start with in-memory
    memory_router = MemoryRouter("in_memory")

    agent = OmniCoreAgent(
        name="switching_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router
    )

    # Check current memory type
    current_type = await agent.get_memory_store_type()
    print(f"\n1. Started with: {current_type}")

    # Run a query
    result = await agent.run("Hello, I'm testing memory switching!")
    print(f"   Response: {result['response'][:100]}...")

    # === SWITCH TO REDIS (at runtime!) ===
    # Note: Requires REDIS_URL in .env before runtime switching
    print("\n2. Switching to Redis...")
    try:
        await agent.switch_memory_store("redis")
        current_type = await agent.get_memory_store_type()
        print(f"   Now using: {current_type}")
    except Exception as e:
        print(f"   Redis not available: {e}")
        print("   (Set REDIS_URL in .env to enable)")

    # === SWITCH TO MONGODB ===
    # Note: Requires MONGODB_URI in .env before runtime switching
    print("\n3. Switching to MongoDB...")
    try:
        await agent.switch_memory_store("mongodb")
        current_type = await agent.get_memory_store_type()
        print(f"   Now using: {current_type}")
    except Exception as e:
        print(f"   MongoDB not available: {e}")
        print("   (Set MONGODB_URI in .env to enable)")

    # === SWITCH TO SQL DATABASE (PostgreSQL/SQLite/MySQL) ===
    # Note: Requires DATABASE_URL in .env before runtime switching
    print("\n4. Switching to Database...")
    try:
        await agent.switch_memory_store("database")
        current_type = await agent.get_memory_store_type()
        print(f"   Now using: {current_type}")
    except Exception as e:
        print(f"   Database not available: {e}")
        print("   (Set DATABASE_URL in .env to enable)")

    # === SWITCH BACK TO IN-MEMORY ===
    print("\n5. Switching back to in-memory...")
    await agent.switch_memory_store("in_memory")
    current_type = await agent.get_memory_store_type()
    print(f"   Now using: {current_type}")

    await agent.cleanup()

    print("\n" + "=" * 50)
    print("KEY TAKEAWAY")
    print("=" * 50)
    print("""
You can switch memory backends at runtime using:
    await agent.switch_memory_store("backend_name")

This is useful for:
- Starting with in_memory for development
- Switching to redis/mongodb for production
- A/B testing different backends
- Gradual migrations
""")


if __name__ == "__main__":
    asyncio.run(main())
