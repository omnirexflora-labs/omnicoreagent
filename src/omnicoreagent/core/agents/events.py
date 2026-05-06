from __future__ import annotations

from collections.abc import Callable

from omnicoreagent.core.agents.xml_parser import extract_thought
from omnicoreagent.core.events.base import (
    AgentMessagePayload,
    AgentThoughtPayload,
    Event,
    EventType,
    FinalAnswerPayload,
    UserMessagePayload,
)


async def emit_event(
    *,
    event_router: Callable | None,
    session_id: str,
    event: Event,
):
    if event_router:
        await event_router(session_id=session_id, event=event)


async def emit_user_message(
    *,
    event_router: Callable | None,
    session_id: str,
    agent_name: str,
    message: str,
):
    await emit_event(
        event_router=event_router,
        session_id=session_id,
        event=Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(message=message),
            agent_name=agent_name,
        ),
    )


async def emit_agent_message(
    *,
    event_router: Callable | None,
    session_id: str,
    agent_name: str,
    message: str,
):
    await emit_event(
        event_router=event_router,
        session_id=session_id,
        event=Event(
            type=EventType.AGENT_MESSAGE,
            payload=AgentMessagePayload(message=message),
            agent_name=agent_name,
        ),
    )


async def emit_agent_thought_from_response(
    *,
    event_router: Callable | None,
    session_id: str,
    agent_name: str,
    response: str,
):
    thought = extract_thought(response)
    if not thought:
        return

    await emit_event(
        event_router=event_router,
        session_id=session_id,
        event=Event(
            type=EventType.AGENT_THOUGHT,
            payload=AgentThoughtPayload(message=thought),
            agent_name=agent_name,
        ),
    )


async def emit_final_answer(
    *,
    event_router: Callable | None,
    session_id: str,
    agent_name: str,
    message: str,
):
    await emit_event(
        event_router=event_router,
        session_id=session_id,
        event=Event(
            type=EventType.FINAL_ANSWER,
            payload=FinalAnswerPayload(message=message),
            agent_name=agent_name,
        ),
    )
