"""Scale plan S3: two agents, separate logs, one archive between them.

What S4 will need of a deployment: each process keeps its own write-ahead log
(a running trace belongs to the process running it), and finished traces go to
an archive both processes share — one index in a database, one set of bodies.
Either process can then answer for a trace the other recorded, and the stream
cursors are one line, so no process hands out a cursor another already used.

Configured the way a deployment configures it: ``archive_index_url`` and
``archive_bodies_path`` on the telemetry config, through the runtime's own
construction path. PostgreSQL takes part when
``OMNICOREAGENT_TEST_POSTGRES_URL`` is set (CI sets it).
"""

from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import pytest

from omnicoreagent.core.runtime.construction import default_telemetry_store
from omnicoreagent.core.telemetry import TelemetryConfig
from omnicoreagent.core.telemetry.archive_index import SqlTelemetryIndex
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryStreamScope,
    TelemetryTrace,
    TraceStatus,
    utc_now,
)

POSTGRES_URL_ENV = "OMNICOREAGENT_TEST_POSTGRES_URL"


@pytest.fixture(params=["sqlite", "postgres"])
def index_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'shared-index.sqlite'}"
    url = os.getenv(POSTGRES_URL_ENV)
    if not url:
        pytest.skip(f"PostgreSQL shared archive tests need {POSTGRES_URL_ENV}")
    prefix = f"t{uuid4().hex[:12]}_"
    yield_url = f"{url}"
    index = SqlTelemetryIndex(url, table_prefix=prefix)
    request.addfinalizer(lambda: (index.drop(), index.close()))
    # The store builds its own index; this one only cleans up afterwards.
    return {"url": yield_url, "prefix": prefix}


def _store(tmp_path, name: str, index_url, bodies):
    """A process's telemetry store: its own log, the shared archive."""
    url = index_url["url"] if isinstance(index_url, dict) else index_url
    store = default_telemetry_store(
        telemetry_config=TelemetryConfig(
            storage="jsonl",
            storage_path=str(tmp_path / name / "traces.jsonl"),
            archive_index_url=url,
            archive_bodies_path=str(bodies),
            retention_days=None,
        )
    )
    if isinstance(index_url, dict):
        # Same table prefix as the fixture's cleaner, so the tables go away.
        store.archive._index = SqlTelemetryIndex(
            index_url["url"], table_prefix=index_url["prefix"]
        )
    return store


async def _finished_trace(store, number: int) -> TelemetryTrace:
    trace_id = f"trace_{number:032x}"
    span = TelemetrySpan(
        trace_id=trace_id,
        span_id=f"span_{number:032x}",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name=f"agent-{number}"),
        started_at=utc_now(),
    )
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=span.span_id,
        run_id=f"run-{number}",
        session_id=f"session-{number}",
        agent_id=f"agent-{number}",
        status=TraceStatus.RUNNING,
        started_at=utc_now(),
        spans=[span],
    )
    await store.upsert_trace(trace)
    await store.append_event(
        trace_id,
        TelemetryEvent(
            trace_id=trace_id,
            event_type="user_message",
            actor=TelemetryActor(type=ActorType.USER),
            span_id=span.span_id,
            event_id=f"event_{number:032x}",
            sequence_number=1,
            input={"message": f"question {number}"},
        ),
    )
    finished = replace(
        await store.get_trace(trace_id),
        status=TraceStatus.COMPLETED,
        ended_at=utc_now(),
    )
    await store.upsert_trace(finished)
    await store.flush()
    return finished


@pytest.mark.asyncio
async def test_each_process_answers_for_the_others_finished_traces(tmp_path, index_url):
    bodies = tmp_path / "shared-bodies"
    first = _store(tmp_path, "first", index_url, bodies)
    second = _store(tmp_path, "second", index_url, bodies)
    try:
        mine = await _finished_trace(first, 1)
        yours = await _finished_trace(second, 2)

        # Each store's own log holds only its own running work; the archive
        # answers for both.
        found = await second.get_trace(mine.trace_id)
        assert found is not None and found.run_id == "run-1"
        assert (await first.get_trace(yours.trace_id)).run_id == "run-2"

        listed = {trace.trace_id for trace in await first.list_traces(None)}
        assert listed == {mine.trace_id, yours.trace_id}

        # One cursor line: the second store's cursors continue past the first's.
        streamed = await first.get_events_after(TelemetryStreamScope(), None)
        assert {event.trace_id for event in streamed} == {
            mine.trace_id,
            yours.trace_id,
        }
        cursors = [int(event.stream_cursor) for event in streamed]
        assert cursors == sorted(cursors)
        assert len(set(cursors)) == len(cursors), "two processes reused a cursor"
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_processes_running_at_the_same_time_do_not_reuse_a_cursor(
    tmp_path, index_url
):
    """The harder case: both processes recording before either finishes.

    A cursor is a stream's position. Each process counts from the highest it
    has seen, and while a trace is still running its events are only in that
    process's own log — so two processes recording at once must not both hand
    out the same number, or a reader resuming after it loses events.
    """
    bodies = tmp_path / "shared-bodies"
    first = _store(tmp_path, "first", index_url, bodies)
    second = _store(tmp_path, "second", index_url, bodies)
    try:
        await _running_trace(first, 1)
        await _running_trace(second, 2)
        await _finish(first, 1)
        await _finish(second, 2)

        streamed = await first.get_events_after(TelemetryStreamScope(), None)
        cursors = [int(event.stream_cursor) for event in streamed]
        assert len(streamed) == 4, [event.event_id for event in streamed]
        assert len(set(cursors)) == len(cursors), (
            f"two processes recording at once reused a cursor: {cursors}"
        )
    finally:
        await first.close()
        await second.close()


async def _running_trace(store, number: int) -> None:
    trace_id = f"trace_{number:032x}"
    span = TelemetrySpan(
        trace_id=trace_id,
        span_id=f"span_{number:032x}",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name=f"agent-{number}"),
        started_at=utc_now(),
    )
    await store.upsert_trace(
        TelemetryTrace(
            trace_id=trace_id,
            root_span_id=span.span_id,
            run_id=f"run-{number}",
            session_id=f"session-{number}",
            agent_id=f"agent-{number}",
            status=TraceStatus.RUNNING,
            started_at=utc_now(),
            spans=[span],
        )
    )
    for index in range(2):
        await store.append_event(
            trace_id,
            TelemetryEvent(
                trace_id=trace_id,
                event_type="user_message",
                actor=TelemetryActor(type=ActorType.USER),
                span_id=span.span_id,
                event_id=f"event_{number:028x}{index:04x}",
                sequence_number=index + 1,
                input={"message": f"question {number}.{index}"},
            ),
        )


async def _finish(store, number: int) -> None:
    trace_id = f"trace_{number:032x}"
    await store.upsert_trace(
        replace(
            await store.get_trace(trace_id),
            status=TraceStatus.COMPLETED,
            ended_at=utc_now(),
        )
    )
    await store.flush()
