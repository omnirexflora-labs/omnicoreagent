#!/usr/bin/env python3
"""
Agent with Event Streaming

Stream real-time events from your agent.
Track: user messages, agent thoughts, tool calls, final answers.

Build on: agent_with_memory_switching.py
Next: agent_with_event_switching.py

Run:
    python cookbook/getting_started/agent_with_events.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, MemoryRouter, EventRouter


async def main():


    print("=" * 50)
    print("AGENT WITH EVENT STREAMING")
    print("=" * 50)

    # Create event router (in-memory for development)
    event_router = EventRouter("in_memory")

    agent = OmniCoreAgent(
        name="event_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=MemoryRouter("in_memory"),
        event_router=event_router  # <- Add event router
    )

    # Run a query
    session_id = "event_demo_session"
    print(f"\nRunning query with session: {session_id}")
    result = await agent.run("What is 2 + 2? Explain step by step.", session_id=session_id)
    print(f"Response: {result['response'][:200]}...")

    # Get events after the query
    print("\n" + "=" * 50)
    print("EVENTS FROM SESSION")
    print("=" * 50)

    events = await agent.get_events(session_id)
    for event in events:
        print(f"  [{event.type}]: {str(event.payload)[:80]}...")

    await agent.cleanup()


async def demo_streaming():
    """
    Demo real-time event streaming.
    This shows how to build UIs that display agent progress.
    """


    print("\n" + "=" * 50)
    print("REAL-TIME EVENT STREAMING")
    print("=" * 50)

    event_router = EventRouter("in_memory")

    agent = OmniCoreAgent(
        name="streaming_agent",
        system_instruction="You are a helpful assistant.",
        model_config={"provider": "openai", "model": "gpt-4o"},
        memory_router=MemoryRouter("in_memory"),
        event_router=event_router
    )

    session_id = "streaming_session"

    # Start the query in background
    async def run_query():
        await agent.run("Tell me a short joke.", session_id=session_id)

    query_task = asyncio.create_task(run_query())

    # Stream events in real-time
    print("\nStreaming events as they happen:")
    try:
        async for event in agent.stream_events(session_id):
            print(f"  [{event.type}]: {str(event.payload)[:60]}...")
    except asyncio.CancelledError:
        pass

    await query_task
    await agent.cleanup()


async def show_event_types():
    """Show all available event types."""
    print("\n" + "=" * 50)
    print("AVAILABLE EVENT TYPES")
    print("=" * 50)
    print("""
| Event Type         | Description                           |
|--------------------|---------------------------------------|
| user_message       | User's input query                    |
| agent_message      | Agent's response text                 |
| agent_thought      | Agent's reasoning/thinking            |
| tool_call_started  | Tool execution started                |
| tool_call_result   | Tool returned a result                |
| final_answer       | Agent's final answer                  |
| sub_agent_started  | Sub-agent began execution             |
| sub_agent_result   | Sub-agent returned result             |
| sub_agent_error    | Sub-agent encountered error           |
""")


if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(demo_streaming())  # Uncomment to try streaming
    asyncio.run(show_event_types())
