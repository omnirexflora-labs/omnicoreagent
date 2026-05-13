from datetime import datetime, timezone, timedelta
from collections import defaultdict
import asyncio

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
async def test_in_memory_event_stream_is_live_and_session_scoped():
    store = InMemoryEventStore()
    session_event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="session event"),
        agent_name="agent",
        event_id="session-event",
    )
    other_event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="other event"),
        agent_name="agent",
        event_id="other-event",
    )

    stream = store.stream("session-a")
    next_event = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    await store.append("session-b", other_event)
    await asyncio.sleep(0)
    assert not next_event.done()

    await store.append("session-a", session_event)

    assert await asyncio.wait_for(next_event, timeout=1) == session_event
    assert session_event.sequence == 1
    await stream.aclose()


@pytest.mark.asyncio
async def test_in_memory_stream_after_cursor_replays_events_written_before_consume():
    store = InMemoryEventStore()
    cursor = await store.get_stream_cursor("session-a")
    event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="after cursor"),
        agent_name="agent",
    )

    await store.append("session-a", event)

    events_after = await store.get_events_after("session-a", cursor)
    assert [item.event_id for item in events_after] == [event.event_id]

    stream = store.stream_after("session-a", cursor)
    try:
        streamed = await asyncio.wait_for(stream.__anext__(), timeout=1)
        assert streamed.event_id == event.event_id
        assert streamed.sequence == 1
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_in_memory_stream_overflow_raises_instead_of_hanging(monkeypatch):
    from omnicoreagent.core.events import in_memory

    monkeypatch.setattr(in_memory, "_SUBSCRIBER_QUEUE_SIZE", 1)
    store = InMemoryEventStore()
    stream = store.stream("session-a")
    next_event = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    first = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="first"),
        agent_name="agent",
    )
    second = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="second"),
        agent_name="agent",
    )
    await store.append("session-a", first)
    await store.append("session-a", second)

    with pytest.raises(RuntimeError, match="subscriber overflow"):
        await asyncio.wait_for(next_event, timeout=1)
    await stream.aclose()


class _FakeRedisStreamClient:
    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self.waiters: dict[str, list[asyncio.Future]] = defaultdict(list)

    async def eval(self, script: str, numkeys: int, stream_name: str, event_json: str):
        sequence = len(self.streams[stream_name]) + 1
        entry_id = await self.xadd(
            stream_name,
            {"event": event_json, "sequence": str(sequence)},
        )
        return [entry_id, str(sequence)]

    async def xadd(self, stream_name: str, data: dict[str, str]):
        entry_id = f"{len(self.streams[stream_name]) + 1}-0"
        self.streams[stream_name].append((entry_id, data))
        waiters = self.waiters.pop(stream_name, [])
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result([(stream_name, [(entry_id, data)])])
        return entry_id

    async def xrange(self, stream_name: str, min: str = "-", max: str = "+"):
        entries = list(self.streams[stream_name])
        if min.startswith("("):
            after_id = min[1:]
            entries = [
                (entry_id, data)
                for entry_id, data in entries
                if _redis_entry_index(entry_id) > _redis_entry_index(after_id)
            ]
        return entries

    async def xrevrange(
        self,
        stream_name: str,
        max: str = "+",
        min: str = "-",
        count: int = 1,
    ):
        return list(reversed(self.streams[stream_name]))[:count]

    async def xread(self, streams: dict[str, str], block: int = 0, count: int = 1):
        stream_name, last_id = next(iter(streams.items()))
        entries = self.streams[stream_name]

        if last_id != "$":
            next_entries = [
                (entry_id, data)
                for entry_id, data in entries
                if _redis_entry_index(entry_id) > _redis_entry_index(last_id)
            ][:count]
            if next_entries:
                return [(stream_name, next_entries)]

        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self.waiters[stream_name].append(waiter)
        return await waiter


def _redis_entry_index(entry_id: str) -> int:
    return int(entry_id.split("-", 1)[0])


@pytest.mark.asyncio
async def test_redis_event_store_replays_history_and_streams_live_session_only(
    monkeypatch,
):
    from omnicoreagent.core.events import redis_stream

    fake_redis = _FakeRedisStreamClient()
    monkeypatch.setattr(
        redis_stream.redis,
        "from_url",
        lambda url, decode_responses: fake_redis,
    )

    store = redis_stream.RedisStreamEventStore()
    session_event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="session event"),
        agent_name="agent",
        event_id="redis-session-event",
    )
    other_event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="other event"),
        agent_name="agent",
        event_id="redis-other-event",
    )

    await store.append("session-a", session_event)
    await store.append("session-b", other_event)

    assert await store.get_events("session-a") == [session_event]
    assert session_event.sequence == 1

    stream = store.stream("session-a")
    next_event = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    await store.append("session-b", other_event)
    await asyncio.sleep(0)
    assert not next_event.done()

    live_event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="live session event"),
        agent_name="agent",
        event_id="redis-live-event",
    )
    await store.append("session-a", live_event)

    assert await asyncio.wait_for(next_event, timeout=1) == live_event
    assert live_event.sequence == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_redis_stream_after_cursor_replays_events_written_before_consume(
    monkeypatch,
):
    from omnicoreagent.core.events import redis_stream

    fake_redis = _FakeRedisStreamClient()
    monkeypatch.setattr(
        redis_stream.redis,
        "from_url",
        lambda url, decode_responses: fake_redis,
    )

    store = redis_stream.RedisStreamEventStore()
    cursor = await store.get_stream_cursor("session-a")
    event = Event(
        type=EventType.AGENT_MESSAGE,
        payload=AgentMessagePayload(message="after cursor"),
        agent_name="agent",
    )

    await store.append("session-a", event)

    events_after = await store.get_events_after("session-a", cursor)
    assert [item.event_id for item in events_after] == [event.event_id]

    stream = store.stream_after("session-a", cursor)
    try:
        streamed = await asyncio.wait_for(stream.__anext__(), timeout=1)
        assert streamed.event_id == event.event_id
        assert streamed.sequence == 1
    finally:
        await stream.aclose()


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
    assert [step.sequence for step in trace.steps] == [1, 2]


def test_event_router_unknown_backend_reports_in_memory_fallback():
    router = EventRouter(event_store_type="unknown")

    assert router.get_event_store_type() == "in_memory"
    assert router.get_event_store_info() == {"type": "in_memory", "available": True}
