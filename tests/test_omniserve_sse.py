import asyncio
import json

import pytest

from omnicoreagent.core.events.base import (
    AgentThoughtPayload,
    Event,
    EventType,
    FinalAnswerPayload,
    UserMessagePayload,
)
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.serve import sse as sse_module
from omnicoreagent.serve.sse import run_agent_stream, stream_session_events


def _event_name(chunk: str) -> str:
    return chunk.splitlines()[0].removeprefix("event: ")


def _event_data(chunk: str) -> dict:
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


class _StreamingRunAgent:
    name = "StreamingRunAgent"

    def __init__(self):
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.subscribed = asyncio.Event()

    async def stream_events(self, session_id: str):
        self.subscribed.set()
        while True:
            yield await self.queue.get()

    async def run(self, query: str, *, session_id: str):
        await self.subscribed.wait()
        await self.queue.put(
            Event(
                type=EventType.AGENT_THOUGHT,
                payload=AgentThoughtPayload(message=f"thinking about {query}"),
                agent_name=self.name,
                event_id="thought-1",
            )
        )
        await self.queue.put(
            Event(
                type=EventType.FINAL_ANSWER,
                payload=FinalAnswerPayload(message="done"),
                agent_name=self.name,
                event_id="final-1",
            )
        )
        return {"response": "done"}


class _EventAgent:
    name = "EventAgent"

    def __init__(self, router: EventRouter):
        self.router = router

    async def get_events(self, session_id: str):
        return await self.router.get_events(session_id)

    async def get_event_stream_cursor(self, session_id: str):
        return await self.router.get_stream_cursor(session_id)

    async def stream_events_after(self, session_id: str, cursor: str | None):
        async for event in self.router.stream_after(session_id, cursor):
            yield event

    async def get_events_after(self, session_id: str, cursor: str | None):
        return await self.router.get_events_after(session_id, cursor)

    async def stream_events(self, session_id: str):
        async for event in self.router.stream(session_id):
            yield event


class _ImmediateEventAgent:
    name = "ImmediateEventAgent"

    def __init__(self):
        self.event_router = EventRouter(event_store_type="in_memory")

    async def get_event_stream_cursor(self, session_id: str):
        return await self.event_router.get_stream_cursor(session_id)

    async def stream_events_after(self, session_id: str, cursor: str | None):
        async for event in self.event_router.stream_after(session_id, cursor):
            yield event

    async def get_events_after(self, session_id: str, cursor: str | None):
        return await self.event_router.get_events_after(session_id, cursor)

    async def run(self, query: str, *, session_id: str):
        await self.event_router.append(
            session_id,
            Event(
                type=EventType.USER_MESSAGE,
                payload=UserMessagePayload(message=query),
                agent_name=self.name,
                event_id="immediate-user",
            ),
        )
        return {"response": "done"}


class _InterleavedSameSessionAgent:
    name = "InterleavedSameSessionAgent"

    def __init__(self):
        self.event_router = EventRouter(event_store_type="in_memory")

    async def get_event_stream_cursor(self, session_id: str):
        return await self.event_router.get_stream_cursor(session_id)

    async def stream_events_after(self, session_id: str, cursor: str | None):
        async for event in self.event_router.stream_after(session_id, cursor):
            yield event

    async def get_events_after(self, session_id: str, cursor: str | None):
        return await self.event_router.get_events_after(session_id, cursor)

    async def run(self, query: str, *, session_id: str):
        await self.event_router.append(
            session_id,
            Event(
                type=EventType.USER_MESSAGE,
                payload=UserMessagePayload(message=f"user:{query}"),
                agent_name=self.name,
                event_id=f"user-{query}",
            ),
        )
        await asyncio.sleep(0.01)
        await self.event_router.append(
            session_id,
            Event(
                type=EventType.AGENT_THOUGHT,
                payload=AgentThoughtPayload(message=f"thought:{query}"),
                agent_name=self.name,
                event_id=f"thought-{query}",
            ),
        )
        return {"response": f"done:{query}"}


class _SlowAgent:
    name = "SlowAgent"

    async def stream_events(self, session_id: str):
        while True:
            await asyncio.sleep(10)
            if False:
                yield None

    async def run(self, query: str, *, session_id: str):
        await asyncio.sleep(10)
        return {"response": "too late"}


class _BrokenStreamAgent:
    name = "BrokenStreamAgent"

    async def get_events(self, session_id: str):
        return []

    async def get_event_stream_cursor(self, session_id: str):
        return "0"

    async def stream_events_after(self, session_id: str, cursor: str | None):
        raise RuntimeError("stream failed")
        if False:
            yield None


class _SlowCatchupAgent:
    name = "SlowCatchupAgent"

    async def get_event_stream_cursor(self, session_id: str):
        return "0"

    async def stream_events_after(self, session_id: str, cursor: str | None):
        while True:
            await asyncio.sleep(10)
            if False:
                yield None

    async def get_events_after(self, session_id: str, cursor: str | None):
        await asyncio.sleep(10)
        return []

    async def run(self, query: str, *, session_id: str):
        return {"response": "done"}


@pytest.mark.asyncio
async def test_run_agent_stream_yields_live_events_before_complete():
    agent = _StreamingRunAgent()

    chunks = [
        chunk
        async for chunk in run_agent_stream(
            agent,
            "hello",
            "session-a",
            timeout_seconds=1,
        )
    ]

    event_names = [_event_name(chunk) for chunk in chunks]

    assert event_names == [
        "session",
        "agent_thought",
        "final_answer",
        "complete",
        "session",
    ]
    assert event_names.index("agent_thought") < event_names.index("complete")
    assert _event_data(chunks[1])["session_id"] == "session-a"
    assert _event_data(chunks[2])["event_id"] == "final-1"
    assert _event_data(chunks[3])["response"] == "done"


@pytest.mark.asyncio
async def test_run_agent_stream_catches_events_emitted_before_stream_consumes():
    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _ImmediateEventAgent(),
            "hello",
            "session-a",
            timeout_seconds=1,
        )
    ]

    event_names = [_event_name(chunk) for chunk in chunks]

    assert event_names == ["session", "user_message", "complete", "session"]
    assert _event_data(chunks[1])["event_id"] == "immediate-user"
    assert _event_data(chunks[1])["sequence"] == 1


@pytest.mark.asyncio
async def test_concurrent_run_streams_same_session_only_emit_their_run_events():
    agent = _InterleavedSameSessionAgent()

    async def collect(query: str):
        chunks = [
            chunk
            async for chunk in run_agent_stream(
                agent,
                query,
                "shared-session",
                timeout_seconds=1,
            )
        ]
        runtime_events = [
            _event_data(chunk)
            for chunk in chunks
            if _event_data(chunk).get("type") is not None
        ]
        complete = next(
            _event_data(chunk) for chunk in chunks if _event_name(chunk) == "complete"
        )
        return runtime_events, complete

    (first_events, first_complete), (second_events, second_complete) = (
        await asyncio.gather(collect("first"), collect("second"))
    )

    assert [event["payload"]["message"] for event in first_events] == [
        "user:first",
        "thought:first",
    ]
    assert [event["payload"]["message"] for event in second_events] == [
        "user:second",
        "thought:second",
    ]
    assert {event["run_id"] for event in first_events} == {first_complete["run_id"]}
    assert {event["run_id"] for event in second_events} == {second_complete["run_id"]}
    assert first_complete["run_id"] != second_complete["run_id"]


@pytest.mark.asyncio
async def test_stream_session_events_replays_history_then_streams_live_session_only():
    router = EventRouter(event_store_type="in_memory")
    agent = _EventAgent(router)

    await router.append(
        "session-a",
        Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(message="stored"),
            agent_name=agent.name,
            event_id="stored-a",
        ),
    )
    await router.append(
        "session-b",
        Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(message="other session"),
            agent_name=agent.name,
            event_id="stored-b",
        ),
    )

    stream = stream_session_events(agent, "session-a")
    try:
        session_chunk = await asyncio.wait_for(stream.__anext__(), timeout=1)
        replay_chunk = await asyncio.wait_for(stream.__anext__(), timeout=1)

        assert _event_name(session_chunk) == "session"
        assert _event_name(replay_chunk) == "user_message"
        assert _event_data(replay_chunk)["event_id"] == "stored-a"
        assert _event_data(replay_chunk)["session_id"] == "session-a"

        await router.append(
            "session-b",
            Event(
                type=EventType.AGENT_THOUGHT,
                payload=AgentThoughtPayload(message="do not stream"),
                agent_name=agent.name,
                event_id="live-b",
            ),
        )
        await router.append(
            "session-a",
            Event(
                type=EventType.AGENT_THOUGHT,
                payload=AgentThoughtPayload(message="stream me"),
                agent_name=agent.name,
                event_id="live-a",
            ),
        )

        live_chunk = await asyncio.wait_for(stream.__anext__(), timeout=1)

        assert _event_name(live_chunk) == "agent_thought"
        assert _event_data(live_chunk)["event_id"] == "live-a"
        assert _event_data(live_chunk)["session_id"] == "session-a"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_run_agent_stream_timeout_yields_error_and_ended():
    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _SlowAgent(),
            "slow",
            "timeout-session",
            timeout_seconds=0.01,
        )
    ]

    assert [_event_name(chunk) for chunk in chunks] == ["session", "error", "session"]
    error_data = _event_data(chunks[1])
    assert error_data["error"] == "Request timed out"
    assert error_data["session_id"] == "timeout-session"
    assert error_data["run_id"].startswith("run_")
    assert _event_data(chunks[-1]) == {
        "session_id": "timeout-session",
        "status": "ended",
    }


@pytest.mark.asyncio
async def test_run_agent_stream_catchup_timeout_still_yields_complete(monkeypatch):
    monkeypatch.setattr(sse_module, "_EVENT_REPLAY_TIMEOUT_SECONDS", 0.01)

    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _SlowCatchupAgent(),
            "hello",
            "catchup-session",
            timeout_seconds=1,
        )
    ]

    assert [_event_name(chunk) for chunk in chunks] == [
        "session",
        "error",
        "complete",
        "session",
    ]
    assert _event_data(chunks[1])["session_id"] == "catchup-session"
    assert _event_data(chunks[1])["run_id"].startswith("run_")
    assert _event_data(chunks[2])["response"] == "done"
    assert _event_data(chunks[2])["run_id"] == _event_data(chunks[1])["run_id"]


@pytest.mark.asyncio
async def test_stream_session_events_yields_error_when_live_stream_fails():
    stream = stream_session_events(_BrokenStreamAgent(), "broken-session")
    try:
        chunks = [
            await asyncio.wait_for(stream.__anext__(), timeout=1),
            await asyncio.wait_for(stream.__anext__(), timeout=1),
            await asyncio.wait_for(stream.__anext__(), timeout=1),
        ]
    finally:
        await stream.aclose()

    assert [_event_name(chunk) for chunk in chunks] == ["session", "error", "session"]
    assert _event_data(chunks[1]) == {
        "error": "stream failed",
        "session_id": "broken-session",
    }
