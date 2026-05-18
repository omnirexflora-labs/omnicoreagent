import asyncio
import json

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryConfig,
    TelemetryRecorder,
    TelemetryStream,
)
from omnicoreagent.serve.sse import run_agent_stream, stream_session_events


def _event_name(chunk: str) -> str:
    return chunk.splitlines()[0].removeprefix("event: ")


def _event_data(chunk: str) -> dict:
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


class _TelemetryAgent:
    name = "TelemetryAgent"

    def __init__(self, telemetry_config: TelemetryConfig | None = None):
        self.store = InMemoryTelemetryStore()
        self.telemetry_stream = TelemetryStream(self.store)
        self.telemetry_config = telemetry_config
        self.run_started = asyncio.Event()

    async def get_telemetry_stream_cursor(self, *, session_id: str):
        return await self.telemetry_stream.get_stream_cursor(
            self._scope(session_id=session_id)
        )

    async def stream_telemetry_after(
        self, *, cursor: str | None, session_id: str, run_id: str | None = None
    ):
        async for event in self.telemetry_stream.stream_after(
            self._scope(session_id=session_id, run_id=run_id), cursor
        ):
            yield event

    async def get_telemetry_events_after(
        self, *, cursor: str | None, session_id: str, run_id: str | None = None
    ):
        return await self.telemetry_stream.get_events_after(
            self._scope(session_id=session_id, run_id=run_id), cursor
        )

    async def run(self, query: str, *, session_id: str, run_id: str):
        recorder = TelemetryRecorder(self.store, self.telemetry_config)
        context = await recorder.start_trace(
            name="agent.run",
            kind="agent.run",
            actor=TelemetryActor(type=ActorType.AGENT, name=self.name),
            run_id=run_id,
            session_id=session_id,
            input={"query": query},
        )
        self.run_started.set()
        await recorder.emit_event(
            "user_message",
            actor=TelemetryActor(type=ActorType.USER),
            input={"message": query},
        )
        await asyncio.sleep(0)
        await recorder.emit_event(
            "final_answer",
            actor=TelemetryActor(type=ActorType.AGENT, name=self.name),
            output={"response": f"done:{query}"},
        )
        await recorder.end_trace(output={"response": f"done:{query}"})
        return {
            "response": f"done:{query}",
            "trace_id": context.trace_id,
            "run_id": run_id,
        }

    def _scope(self, *, session_id: str, run_id: str | None = None):
        from omnicoreagent.core.telemetry import TelemetryStreamScope

        return TelemetryStreamScope(session_id=session_id, run_id=run_id)


class _UnfilteredTelemetryAgent(_TelemetryAgent):
    async def stream_telemetry_after(
        self, *, cursor: str | None, session_id: str, run_id: str | None = None
    ):
        async for event in self.telemetry_stream.stream_after(
            self._scope(session_id=session_id), cursor
        ):
            yield event

    async def get_telemetry_events_after(
        self, *, cursor: str | None, session_id: str, run_id: str | None = None
    ):
        return await self.telemetry_stream.get_events_after(
            self._scope(session_id=session_id), cursor
        )


class _NoRunIdAgent:
    name = "NoRunIdAgent"

    async def run(self, query: str, *, session_id: str):
        return {"response": f"done:{session_id}:{query}"}


@pytest.mark.asyncio
async def test_run_agent_stream_yields_telemetry_before_complete():
    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _TelemetryAgent(),
            "hello",
            "session-a",
            timeout_seconds=1,
        )
    ]

    event_names = [_event_name(chunk) for chunk in chunks]

    assert event_names == [
        "session",
        "user_message",
        "final_answer",
        "complete",
        "session",
    ]
    assert event_names.index("final_answer") < event_names.index("complete")
    assert _event_data(chunks[1])["session_id"] == "session-a"
    assert _event_data(chunks[3])["response"] == "done:hello"


@pytest.mark.asyncio
async def test_run_agent_stream_supports_agents_without_run_id_keyword():
    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _NoRunIdAgent(),
            "hello",
            "session-no-run-id",
            timeout_seconds=1,
        )
    ]

    assert [_event_name(chunk) for chunk in chunks] == [
        "session",
        "complete",
        "session",
    ]
    complete = _event_data(chunks[1])
    assert complete["response"] == "done:session-no-run-id:hello"
    assert complete["run_id"].startswith("run_")


@pytest.mark.asyncio
async def test_run_agent_stream_keeps_scoped_events_when_metadata_is_truncated():
    chunks = [
        chunk
        async for chunk in run_agent_stream(
            _TelemetryAgent(TelemetryConfig(max_payload_bytes=1)),
            "hello",
            "session-truncated-metadata",
            timeout_seconds=1,
        )
    ]

    event_names = [_event_name(chunk) for chunk in chunks]
    assert "user_message" in event_names
    assert "final_answer" in event_names
    assert "complete" in event_names


@pytest.mark.asyncio
async def test_concurrent_run_streams_same_session_only_emit_their_run_events():
    agent = _TelemetryAgent()

    async def collect(query: str):
        return [
            chunk
            async for chunk in run_agent_stream(
                agent,
                query,
                "shared-session",
                timeout_seconds=1,
            )
        ]

    first, second = await asyncio.gather(collect("first"), collect("second"))

    first_events = [_event_data(chunk) for chunk in first if _event_name(chunk) != "session"]
    second_events = [
        _event_data(chunk) for chunk in second if _event_name(chunk) != "session"
    ]

    assert {event["run_id"] for event in first_events if event.get("event_id")} == {
        first_events[-1]["run_id"]
    }
    assert {event["run_id"] for event in second_events if event.get("event_id")} == {
        second_events[-1]["run_id"]
    }
    assert first_events[-1]["run_id"] != second_events[-1]["run_id"]


@pytest.mark.asyncio
async def test_concurrent_run_streams_do_not_leak_when_stream_ignores_run_id():
    agent = _UnfilteredTelemetryAgent(TelemetryConfig(max_payload_bytes=1))

    async def collect(query: str):
        return [
            chunk
            async for chunk in run_agent_stream(
                agent,
                query,
                "shared-session",
                timeout_seconds=1,
            )
        ]

    first, second = await asyncio.gather(collect("first"), collect("second"))

    first_events = [
        _event_data(chunk)
        for chunk in first
        if _event_name(chunk) not in {"session", "complete"}
    ]
    second_events = [
        _event_data(chunk)
        for chunk in second
        if _event_name(chunk) not in {"session", "complete"}
    ]

    assert {event["run_id"] for event in first_events} == {
        _event_data(next(chunk for chunk in first if _event_name(chunk) == "complete"))[
            "run_id"
        ]
    }
    assert {event["run_id"] for event in second_events} == {
        _event_data(next(chunk for chunk in second if _event_name(chunk) == "complete"))[
            "run_id"
        ]
    }


@pytest.mark.asyncio
async def test_stream_session_events_replays_existing_telemetry():
    agent = _TelemetryAgent()
    await agent.run("old", session_id="session-b", run_id="run_existing")

    stream = stream_session_events(agent, "session-b")
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
        if _event_name(chunk) == "final_answer":
            break
    await stream.aclose()

    assert [_event_name(chunk) for chunk in chunks] == [
        "session",
        "user_message",
        "final_answer",
    ]
    assert _event_data(chunks[1])["run_id"] == "run_existing"
