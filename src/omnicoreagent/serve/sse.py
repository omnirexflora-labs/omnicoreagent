"""
OmniServe SSE (Server-Sent Events) Utilities.

Provides utilities for streaming agent events via SSE.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncGenerator

from omnicoreagent.core.logging import logger

from .serialization import normalize_event, normalize_run_result
from .state import get_agent_name

if TYPE_CHECKING:
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent as AgentType
else:
    AgentType = Any


def format_sse_event(event_type: str, data: dict) -> str:
    """
    Format data as an SSE event string.

    Args:
        event_type: The event type (e.g., 'message', 'tool_call', 'complete')
        data: The event data to send

    Returns:
        SSE-formatted string
    """
    json_data = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def run_agent_stream(
    agent: AgentType,
    query: str,
    session_id: str,
    *,
    timeout_seconds: int | None = None,
) -> AsyncGenerator[str, None]:
    """
    Run the agent and stream result via SSE.

    Simplified implementation that just waits for the final result
    without intermediate event streaming, to ensure stability.

    Args:
        agent: The OmniCoreAgent instance to run
        query: The user query
        session_id: Session ID for the conversation

    Yields:
        SSE-formatted event strings
    """
    # Yield session start event
    yield format_sse_event("session", {"session_id": session_id, "status": "started"})

    try:
        run_coro = agent.run(query, session_id=session_id)
        if timeout_seconds and timeout_seconds > 0:
            response = await asyncio.wait_for(run_coro, timeout=timeout_seconds)
        else:
            response = await run_coro

        normalized = normalize_run_result(
            response,
            agent_name=get_agent_name(agent),
        )

        yield format_sse_event(
            "complete",
            {
                "session_id": session_id,
                **normalized,
            },
        )
    except asyncio.TimeoutError:
        logger.error(f"OmniServe SSE: Agent run timed out after {timeout_seconds}s")
        yield format_sse_event(
            "error",
            {
                "error": "Request timed out",
                "session_id": session_id,
            },
        )

    except Exception as e:
        logger.error(f"OmniServe SSE: Agent run error: {e}")
        yield format_sse_event(
            "error",
            {
                "error": str(e),
                "session_id": session_id,
            },
        )

    # Yield session ended event
    yield format_sse_event("session", {"session_id": session_id, "status": "ended"})


async def stream_session_events(
    agent: AgentType,
    session_id: str,
) -> AsyncGenerator[str, None]:
    """
    Stream existing events for a session via SSE.

    Used for reconnecting to an existing session or
    replaying past events.

    Args:
        agent: The agent
        session_id: Session ID to stream events for

    Yields:
        SSE-formatted event strings
    """
    yield format_sse_event("session", {"session_id": session_id, "status": "streaming"})

    try:
        async for event in agent.stream_events(session_id):
            event_data = normalize_event(event)

            event_type = event_data.get("type", "event")
            if hasattr(event_type, "value"):
                event_type = event_type.value

            yield format_sse_event(event_type, event_data)
    except Exception as e:
        logger.error(f"OmniServe SSE: Event replay error: {e}")
        yield format_sse_event("error", {"error": str(e)})

    yield format_sse_event("session", {"session_id": session_id, "status": "ended"})
