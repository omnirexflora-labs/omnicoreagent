#!/usr/bin/env python3
"""
Agent with Event Streaming

Stream real-time telemetry events from your agent.
Track: user messages, model calls, tool calls, observations, and final answers.

Build on: agent_with_memory_switching.py
Next: agent_with_metrics.py

Run:
    python cookbook/getting_started/agent_with_events.py
"""

import asyncio


from omnicoreagent import OmniCoreAgent, MemoryRouter

from _bootstrap import model_config, require_llm_api_key, response_text


async def main():
    require_llm_api_key()

    print("=" * 50)
    print("AGENT WITH EVENT STREAMING")
    print("=" * 50)

    agent = OmniCoreAgent(
        name="event_agent",
        system_instruction="You are a helpful assistant.",
        model_config=model_config(max_tokens=700),
        memory_router=MemoryRouter("in_memory"),
    )

    # Run a query
    session_id = "event_demo_session"
    print(f"\nRunning query with session: {session_id}")
    result = await agent.run(
        "What is 2 + 2? Explain step by step.", session_id=session_id
    )
    print(f"Response: {response_text(result)[:200]}...")

    # Get telemetry events after the query
    print("\n" + "=" * 50)
    print("EVENTS FROM SESSION")
    print("=" * 50)

    events = await agent.get_telemetry_events_after(cursor=None, session_id=session_id)
    for event in events:
        print(f"  [{event.event_type}]: {event.model_dump()}")

    await agent.cleanup()


async def demo_streaming():
    """
    Demo real-time event streaming.
    This shows how to build UIs that display agent progress.
    """

    print("\n" + "=" * 50)
    print("REAL-TIME EVENT STREAMING")
    print("=" * 50)

    agent = OmniCoreAgent(
        name="streaming_agent",
        system_instruction="You are a helpful assistant.",
        model_config=model_config(max_tokens=500),
        memory_router=MemoryRouter("in_memory"),
    )

    session_id = "streaming_session"

    run_id = "run_streaming_demo"
    cursor = await agent.get_telemetry_stream_cursor(session_id=session_id)

    # Start the query in background
    async def run_query():
        await agent.run(
            "Tell me a short joke.",
            session_id=session_id,
            run_id=run_id,
        )

    query_task = asyncio.create_task(run_query())

    # Stream events in real-time
    print("\nStreaming events as they happen:")
    try:
        async for event in agent.stream_telemetry_after(
            cursor=cursor,
            session_id=session_id,
            run_id=run_id,
        ):
            print(f"  [{event.event_type}]: {event.model_dump()}")
            if event.event_type == "final_answer":
                break
    except asyncio.CancelledError:
        pass

    await query_task
    await agent.cleanup()


async def show_event_types():
    """Show common telemetry event types."""
    print("\n" + "=" * 50)
    print("COMMON TELEMETRY EVENT TYPES")
    print("=" * 50)
    print("""
| Event Type         | Description                           |
|--------------------|---------------------------------------|
| user_message       | User's input query                    |
| model_call         | Model request started                 |
| model_response     | Model returned content/usage          |
| tool_call          | Tool execution started                |
| tool_result        | Tool returned a result                |
| tool_error         | Tool failed                           |
| observation_pipeline_end | Cleaned observation is ready    |
| final_answer       | Agent's final answer                  |
| subagent_spawn     | Sub-agent began execution             |
| subagent_result    | Sub-agent returned result             |
| subagent_error     | Sub-agent encountered error           |
""")


if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(demo_streaming())  # Uncomment to try streaming
    asyncio.run(show_event_types())
