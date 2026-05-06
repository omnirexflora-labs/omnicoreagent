from __future__ import annotations

import pytest

from omnicoreagent.core.agents import events as agent_events
from omnicoreagent.core.events.base import EventType


@pytest.mark.asyncio
async def test_emit_agent_thought_only_emits_when_response_has_thought():
    emitted = []

    async def event_router(session_id, event):
        emitted.append({"session_id": session_id, "event": event})

    await agent_events.emit_agent_thought_from_response(
        event_router=event_router,
        session_id="chat1",
        agent_name="agent",
        response="<thought>Need a tool.</thought><final_answer>done</final_answer>",
    )
    await agent_events.emit_agent_thought_from_response(
        event_router=event_router,
        session_id="chat1",
        agent_name="agent",
        response="<final_answer>done</final_answer>",
    )

    assert len(emitted) == 1
    assert emitted[0]["session_id"] == "chat1"
    assert emitted[0]["event"].type == EventType.AGENT_THOUGHT
    assert emitted[0]["event"].payload.message == "Need a tool."


@pytest.mark.asyncio
async def test_emit_user_agent_and_final_answer_events():
    emitted = []

    async def event_router(session_id, event):
        emitted.append((session_id, event))

    await agent_events.emit_user_message(
        event_router=event_router,
        session_id="chat1",
        agent_name="agent",
        message="hello",
    )
    await agent_events.emit_agent_message(
        event_router=event_router,
        session_id="chat1",
        agent_name="agent",
        message="working",
    )
    await agent_events.emit_final_answer(
        event_router=event_router,
        session_id="chat1",
        agent_name="agent",
        message="done",
    )

    assert [event.type for _, event in emitted] == [
        EventType.USER_MESSAGE,
        EventType.AGENT_MESSAGE,
        EventType.FINAL_ANSWER,
    ]
    assert [event.payload.message for _, event in emitted] == [
        "hello",
        "working",
        "done",
    ]


@pytest.mark.asyncio
async def test_emit_event_ignores_missing_router():
    await agent_events.emit_user_message(
        event_router=None,
        session_id="chat1",
        agent_name="agent",
        message="hello",
    )
