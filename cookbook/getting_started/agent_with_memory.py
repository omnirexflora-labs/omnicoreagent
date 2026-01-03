#!/usr/bin/env python3
"""
Agent with Persistent Memory

Add persistent storage so your agent remembers conversations.
Supports: Redis, MongoDB, PostgreSQL, SQLite, In-Memory.

Build on: agent_with_all_tools.py
Next: agent_with_memory_switching.py

Run:
    python cookbook/getting_started/agent_with_memory.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, MemoryRouter


async def demo_in_memory():
    """Demo with in-memory storage (fastest, but not persistent)."""


    print("=" * 50)
    print("1. IN-MEMORY STORAGE (Development)")
    print("=" * 50)

    # In-memory storage - fast but lost when process ends
    memory_router = MemoryRouter("in_memory")

    agent = OmniCoreAgent(
        name="memory_agent",
        system_instruction="You are a helpful assistant with memory.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router  # <- Add memory router
    )

    # Conversation with memory
    session_id = "user_123"

    print("\nConversation 1: Tell agent my name")
    result = await agent.run("My name is Alice and I'm a software engineer.", session_id=session_id)
    print(f"Response: {result['response']}")

    print("\nConversation 2: Agent remembers!")
    result = await agent.run("What's my name and what do I do?", session_id=session_id)
    print(f"Response: {result['response']}")

    # Get history
    history = await agent.get_session_history(session_id)
    print(f"\nSession history has {len(history)} messages")

    await agent.cleanup()


async def demo_redis():
    """Demo with Redis storage (production-ready)."""

    print("\n" + "=" * 50)
    print("2. REDIS STORAGE (Production)")
    print("=" * 50)

    # Redis - requires REDIS_URL in .env
    # Example: REDIS_URL=redis://localhost:6379/0
    memory_router = MemoryRouter("redis")

    agent = OmniCoreAgent(
        name="redis_agent",
        system_instruction="You are a helpful assistant with Redis memory.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router
    )

    session_id = "redis_session"
    result = await agent.run("Remember this: my favorite color is blue.", session_id=session_id)
    print(f"Response: {result['response']}")

    print("\nConversation 2: Agent remembers!")
    result = await agent.run("What's my favorite color?", session_id=session_id)
    print(f"Response: {result['response']}")

    await agent.cleanup()


async def demo_sql_database():
    """Demo with database storage (PostgreSQL/MySQL/SQLite)."""


    print("\n" + "=" * 50)
    print("3. DATABASE STORAGE (PostgreSQL/SQLite/MySQL)")
    print("=" * 50)

    # Database - requires DATABASE_URL in .env
    # Examples:
    #   DATABASE_URL=postgresql://user:pass@localhost:5432/db
    #   DATABASE_URL=mysql://user:pass@localhost:3306/db
    #   DATABASE_URL=sqlite:///./memory.db
    memory_router = MemoryRouter("database")

    agent = OmniCoreAgent(
        name="db_agent",
        system_instruction="You are a helpful assistant with database memory.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router
    )

    session_id = "db_session"
    result = await agent.run("Store this note: meeting at 3pm tomorrow.", session_id=session_id)
    print(f"Response: {result['response']}")

    print("\nConversation 2: Agent remembers!")
    result = await agent.run("when is meeting tomorrow?", session_id=session_id)
    print(f"Response: {result['response']}")

    await agent.cleanup()

async def demo_mongodb():
    """Demo with MongoDB storage (Document Store)."""

    print("\n" + "=" * 50)
    print("4. MONGODB STORAGE (Document Store)")
    print("=" * 50)

    # MongoDB - requires MONGODB_URI in .env
    # Example: MONGODB_URI=mongodb://localhost:27017/omnicoreagent
    memory_router = MemoryRouter("mongodb")

    agent = OmniCoreAgent(
        name="mongo_agent",
        system_instruction="You are a helpful assistant with MongoDB memory.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=memory_router
    )

    session_id = "mongo_session"
    result = await agent.run("I prefer dark mode themes.", session_id=session_id)
    print(f"Response: {result['response']}")

    print("\nConversation 2: Agent remembers!")
    result = await agent.run("which theme i prefer", session_id=session_id)
    print(f"Response: {result['response']}")

    await agent.cleanup()


async def main():
    """Run in-memory demo (works without external dependencies)."""
    await demo_in_memory()

    # Uncomment these if you have the backends configured:
    # await demo_redis()
    # await demo_sql_database()
    # await demo_mongodb()

    print("\n" + "=" * 50)
    print("MEMORY BACKEND SUMMARY")
    print("=" * 50)
    print("""
| Backend    | Use Case                  | Environment Variable |
|------------|---------------------------|---------------------|
| in_memory  | Development, testing      | (none)              |
| redis      | Production, fast access   | REDIS_URL           |
| database   | SQL systems (Postgres, MySQL, SQLite)    | DATABASE_URL        |
| mongodb    | Document-heavy apps       | MONGODB_URI         |
""")


if __name__ == "__main__":
    asyncio.run(main())
