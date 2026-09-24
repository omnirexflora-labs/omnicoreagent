"""Scale plan S3: the archive's index, local or shared.

The archive keeps one body per trace through the workspace storage interface
(a directory, S3, R2) and finds them through an index. That index was a SQLite
file in the archive's own directory, so two OmniServe processes could share the
bodies and not the index that finds them.

The index is now an interface with two implementations behind it: the SQLite
one, which is still the default and needs nothing installed, and a SQLAlchemy
one that speaks any database. These are the questions the archive asks of it,
asked of both — and then the property that matters, two archives on one index
and one set of bodies, each finding what the other kept.

The SQLAlchemy cases run on SQLite always and on PostgreSQL when
``OMNICOREAGENT_TEST_POSTGRES_URL`` is set (CI sets it).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from omnicoreagent.core.telemetry.archive import TelemetryArchive
from omnicoreagent.core.telemetry.archive_index import (
    SqliteTelemetryIndex,
    SqlTelemetryIndex,
)
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceFilter,
    TraceStatus,
)

POSTGRES_URL_ENV = "OMNICOREAGENT_TEST_POSTGRES_URL"
T0 = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)


def _trace(number: int, *, run_id: str = "run", status=TraceStatus.COMPLETED, reference: str | None = None):
    trace_id = f"trace_{number:032x}"
    span = TelemetrySpan(
        trace_id=trace_id,
        span_id=f"span_{number:032x}",
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT, name="steward"),
        started_at=T0 + timedelta(minutes=number),
    )
    events = [
        TelemetryEvent(
            trace_id=trace_id,
            event_type="user_message",
            actor=TelemetryActor(type=ActorType.USER),
            span_id=span.span_id,
            event_id=f"event_{number:028x}{index:04x}",
            sequence_number=index + 1,
            input={"message": f"run {number}"},
            metadata={"reference": reference} if reference and index == 0 else {},
        )
        for index in range(3)
    ]
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=span.span_id,
        run_id=run_id,
        session_id="session",
        agent_id="steward",
        status=status,
        started_at=T0 + timedelta(minutes=number),
        ended_at=T0 + timedelta(minutes=number, seconds=30),
        spans=[span],
        events=events,
    )
    cursors = {event.event_id: number * 10 + index for index, event in enumerate(events)}
    return trace, cursors


@pytest.fixture(params=["sqlite_file", "sql_sqlite", "sql_postgres"])
def make_index(request, tmp_path):
    """The same index, each way a deployment can keep it."""
    made = []
    if request.param == "sqlite_file":

        def build(name: str = "index"):
            index = SqliteTelemetryIndex(tmp_path / name)
            made.append(index)
            return index

    elif request.param == "sql_sqlite":

        def build(name: str = "index"):
            index = SqlTelemetryIndex(f"sqlite:///{tmp_path / name}.sqlite")
            made.append(index)
            return index

    else:
        url = os.getenv(POSTGRES_URL_ENV)
        if not url:
            pytest.skip(f"PostgreSQL telemetry index tests need {POSTGRES_URL_ENV}")
        prefix = f"t{uuid4().hex[:12]}_"

        def build(name: str = "index"):
            index = SqlTelemetryIndex(url, table_prefix=prefix)
            made.append(index)
            return index

    yield build
    for index in made:
        if request.param == "sql_postgres":
            index.drop()
        index.close()


@pytest.mark.asyncio
async def test_an_index_answers_what_the_archive_asks(make_index, tmp_path):
    archive = TelemetryArchive(tmp_path / "archive", index=make_index())
    trace, cursors = _trace(1, reference="telemetry://payload/" + "ab" * 32)
    other, other_cursors = _trace(2, run_id="other", status=TraceStatus.FAILED)
    await archive.put(trace, cursors)
    await archive.put(other, other_cursors)

    assert await archive.contains(trace.trace_id)
    assert not await archive.contains("trace_missing")
    kept, kept_cursors = await archive.get(trace.trace_id)
    assert kept.model_dump() == trace.model_dump()
    assert kept_cursors == cursors

    headers = await archive.headers(TraceFilter(run_id="run"))
    assert [header["trace_id"] for header in headers] == [trace.trace_id]
    failed = await archive.list(TraceFilter(status=TraceStatus.FAILED))
    assert [item.trace_id for item in failed] == [other.trace_id]

    assert await archive.max_cursor() == max(other_cursors.values())
    streamed = await archive.events_after(max(cursors.values()))
    assert [event.event_id for event, _ in streamed] == [
        event.event_id for event in other.events
    ]
    assert await archive.payload_references() == {"telemetry://payload/" + "ab" * 32}
    assert await archive.ended_before(T0 + timedelta(minutes=1, seconds=45)) == {
        trace.trace_id
    }

    # Putting the same trace again replaces its row rather than adding one.
    await archive.put(trace, cursors)
    assert len(await archive.headers(None)) == 2

    await archive.remove({other.trace_id})
    assert not await archive.contains(other.trace_id)
    assert [header["trace_id"] for header in await archive.headers(None)] == [
        trace.trace_id
    ]


@pytest.mark.asyncio
async def test_two_archives_on_one_index_find_each_others_traces(make_index, tmp_path):
    """What S4 needs: two processes, one index, one set of bodies."""
    bodies = tmp_path / "shared"
    first = TelemetryArchive(bodies, index=make_index())
    second = TelemetryArchive(bodies, index=make_index())

    mine, my_cursors = _trace(1, run_id="mine")
    yours, your_cursors = _trace(2, run_id="yours")
    await first.put(mine, my_cursors)
    await second.put(yours, your_cursors)

    seen = await second.get(mine.trace_id)
    assert seen is not None and seen[0].run_id == "mine"
    assert await first.contains(yours.trace_id)
    assert {header["trace_id"] for header in await first.headers(None)} == {
        mine.trace_id,
        yours.trace_id,
    }
    # One cursor line across both, so neither hands out a cursor the other used.
    assert await first.max_cursor() == await second.max_cursor()
    assert await first.max_cursor() == max(your_cursors.values())
    streamed = await first.events_after(0)
    assert len(streamed) == 6
    assert [int(event.stream_cursor) for event, _ in streamed] == sorted(
        [*my_cursors.values(), *your_cursors.values()]
    )
