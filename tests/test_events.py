from datetime import datetime, timezone, timedelta

import pytest

from omnicoreagent.core.events.base import (
    AgentMessagePayload,
    BackgroundAgentStatusPayload,
    Event,
    EventType,
    FinalAnswerPayload,
    SubAgentCallErrorPayload,
    SubAgentCallStartedPayload,
    ToolCallErrorPayload,
    ToolCallStartedPayload,
    UserMessagePayload,
)
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.core.events.in_memory import InMemoryEventStore
from omnicoreagent.core.events.trace import build_event_trace


def test_event_payloads_keep_model_dump_compatibility():
    payload = UserMessagePayload(message="hello")

    assert payload.model_dump() == {"message": "hello"}
    assert payload.dict() == {"message": "hello"}
    assert payload.json() == '{"message": "hello"}'


def test_background_status_payload_keeps_positional_compatibility():
    payload = BackgroundAgentStatusPayload(
        "agent",
        "background_run_completed",
        "2026-05-12T00:00:00+00:00",
        "session",
        "run_1",
        3,
        1,
        "failed",
    )

    assert payload.last_run == "run_1"
    assert payload.run_count == 3
    assert payload.error_count == 1
    assert payload.error == "failed"
    assert payload.task_id is None
    assert payload.run_id is None


def test_event_serializes_and_parses_without_pydantic():
    event = Event(
        type=EventType.TOOL_CALL_STARTED,
        payload=ToolCallStartedPayload(tool_name="search", tool_args={"q": "test"}),
        agent_name="agent",
    )

    parsed = Event.parse_raw(event.json())

    assert parsed.type == EventType.TOOL_CALL_STARTED
    assert parsed.agent_name == "agent"
    assert parsed.payload.tool_name == "search"
    assert parsed.payload.tool_args == {"q": "test"}
    assert parsed.model_dump()["payload"] == {
        "tool_name": "search",
        "tool_args": {"q": "test"},
        "tool_call_id": None,
    }


def test_build_event_trace_orders_events_and_summarizes_runtime():
    start = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
    events = [
        Event(
            type=EventType.FINAL_ANSWER,
            payload=FinalAnswerPayload(message="done"),
            agent_name="agent",
            timestamp=start + timedelta(milliseconds=30),
            event_id="final",
        ),
        Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(message="hello"),
            agent_name="agent",
            timestamp=start,
            event_id="user",
        ),
        Event(
            type=EventType.TOOL_CALL_STARTED,
            payload=ToolCallStartedPayload(
                tool_name="search",
                tool_args={"query": "runtime"},
                tool_call_id="tool-call-1",
            ),
            agent_name="agent",
            timestamp=start + timedelta(milliseconds=10),
            event_id="tool-start",
        ),
        Event(
            type=EventType.TOOL_CALL_ERROR,
            payload=ToolCallErrorPayload(
                tool_name="search",
                error_message="failed",
            ),
            agent_name="agent",
            timestamp=start + timedelta(milliseconds=20),
            event_id="tool-error",
        ),
        Event(
            type=EventType.SUB_AGENT_CALL_STARTED,
            payload=SubAgentCallStartedPayload(
                agent_name="worker",
                session_id="sub-session",
                timestamp=start.isoformat(),
                run_count=1,
                kwargs={"task": "inspect"},
            ),
            agent_name="agent",
            timestamp=start + timedelta(milliseconds=25),
            event_id="sub-start",
        ),
        Event(
            type=EventType.SUB_AGENT_CALL_ERROR,
            payload=SubAgentCallErrorPayload(
                agent_name="worker",
                session_id="sub-session",
                timestamp=start.isoformat(),
                error="failed",
                error_count=1,
            ),
            agent_name="agent",
            timestamp=start + timedelta(milliseconds=26),
            event_id="sub-error",
        ),
    ]

    trace = build_event_trace(session_id="chat1", events=events)
    dumped = trace.model_dump()

    assert [step.event_id for step in trace.steps] == [
        "user",
        "tool-start",
        "tool-error",
        "sub-start",
        "sub-error",
        "final",
    ]
    assert trace.steps[0].elapsed_ms == 0
    assert trace.steps[1].since_previous_ms == 10
    assert trace.summary.total_events == 6
    assert trace.summary.duration_ms == 30
    assert trace.summary.tool_calls == 1
    assert trace.summary.tool_errors == 1
    assert trace.summary.sub_agent_calls == 1
    assert trace.summary.sub_agent_errors == 1
    assert trace.summary.final_answer == "done"
    assert dumped["summary"]["event_counts"]["tool_call_error"] == 1


@pytest.mark.asyncio
async def test_in_memory_event_store_returns_snapshot():
    store = InMemoryEventStore()
    event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="hello"),
        agent_name="agent",
    )

    await store.append("chat2", event)
    first_read = await store.get_events("chat2")
    first_read.clear()
    second_read = await store.get_events("chat2")

    assert second_read == [event]


@pytest.mark.asyncio
async def test_event_router_builds_trace_from_store():
    router = EventRouter(event_store_type="in_memory")
    await router.append(
        "chat3",
        Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(message="hello"),
            agent_name="agent",
        ),
    )
    await router.append(
        "chat3",
        Event(
            type=EventType.FINAL_ANSWER,
            payload=FinalAnswerPayload(message="done"),
            agent_name="agent",
        ),
    )

    trace = await router.get_trace("chat3")

    assert trace.summary.total_events == 2
    assert trace.summary.final_answer == "done"
    assert [step.event_type for step in trace.steps] == [
        "user_message",
        "final_answer",
    ]
