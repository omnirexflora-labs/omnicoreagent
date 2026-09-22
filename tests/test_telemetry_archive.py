"""T4 of the telemetry storage plan: an archive of finished traces.

One body per trace, written through the workspace storage interface (a local
directory here; S3 or R2 the same way), and a SQLite index of one row per
trace. Listing narrows through the index and reads only the bodies that
match; a stream resumed from an old cursor finds the traces whose cursors
are after it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omnicoreagent.core.telemetry.archive import TelemetryArchive
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceFilter,
    TraceStatus,
)

T0 = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def _trace(number: int, *, run_id: str, status=TraceStatus.COMPLETED, parent: str | None = None):
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
            metadata={"reference": "telemetry://payload/" + "ab" * 32} if index == 0 and number == 2 else {},
        )
        for index in range(3)
    ]
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=span.span_id,
        run_id=run_id,
        session_id="session",
        agent_id="steward",
        parent_trace_id=parent,
        status=status,
        started_at=T0 + timedelta(minutes=number),
        ended_at=T0 + timedelta(minutes=number, seconds=30),
        spans=[span],
        events=events,
    )
    cursors = {event.event_id: number * 10 + index for index, event in enumerate(events)}
    return trace, cursors


@pytest.fixture
def archive(tmp_path):
    archive = TelemetryArchive(tmp_path / "archive")
    yield archive
    archive.close()


@pytest.mark.asyncio
async def test_a_trace_is_kept_whole_and_found_by_its_index(archive):
    trace, cursors = _trace(1, run_id="run_a")
    await archive.put(trace, cursors)

    stored, stored_cursors = await archive.get(trace.trace_id)
    assert stored.model_dump() == trace.model_dump()
    assert stored_cursors == cursors
    assert await archive.get("trace_missing") is None
    assert await archive.contains(trace.trace_id)
    assert await archive.max_cursor() == max(cursors.values())


@pytest.mark.asyncio
async def test_listing_reads_only_the_bodies_that_match(archive, monkeypatch):
    for number, run_id, status in [(1, "run_a", TraceStatus.COMPLETED), (2, "run_b", TraceStatus.FAILED), (3, "run_a", TraceStatus.FAILED)]:
        trace, cursors = _trace(number, run_id=run_id, status=status)
        await archive.put(trace, cursors)
    read: list[str] = []
    original = archive._read_body

    def counting(trace_id):
        read.append(trace_id)
        return original(trace_id)

    monkeypatch.setattr(archive, "_read_body", counting)

    failed = await archive.list(TraceFilter(status=TraceStatus.FAILED))
    assert [t.run_id for t in failed] == ["run_b", "run_a"]
    assert len(read) == 2
    headers = await archive.headers(TraceFilter(run_id="run_a"))
    assert [h["trace_id"] for h in headers] == [f"trace_{1:032x}", f"trace_{3:032x}"]
    assert len(read) == 2, "headers come from the index alone"


@pytest.mark.asyncio
async def test_a_stream_resumes_from_an_old_cursor(archive):
    for number in (1, 2, 3):
        trace, cursors = _trace(number, run_id=f"run_{number}")
        await archive.put(trace, cursors)

    events = [event for event, _ in await archive.events_after(21)]

    assert [int(e.stream_cursor) for e in events] == [22, 30, 31, 32]
    assert all(e.trace_id != f"trace_{1:032x}" for e in events)


@pytest.mark.asyncio
async def test_retention_references_and_removal_go_through_the_index(archive):
    for number in (1, 2, 3):
        trace, cursors = _trace(number, run_id=f"run_{number}")
        await archive.put(trace, cursors)

    old = await archive.ended_before(T0 + timedelta(minutes=2))
    assert old == {f"trace_{1:032x}"}
    assert await archive.payload_references() == {"telemetry://payload/" + "ab" * 32}

    await archive.remove(old)
    assert await archive.get(f"trace_{1:032x}") is None
    assert [h["trace_id"] for h in await archive.headers(None)] == [f"trace_{2:032x}", f"trace_{3:032x}"]


@pytest.mark.asyncio
async def test_putting_a_trace_again_replaces_it(archive):
    trace, cursors = _trace(1, run_id="run_a", status=TraceStatus.RUNNING)
    await archive.put(trace, cursors)
    trace.status = TraceStatus.COMPLETED
    await archive.put(trace, cursors)

    (header,) = await archive.headers(None)
    assert header["status"] == "completed"
    stored, _ = await archive.get(trace.trace_id)
    assert stored.status == TraceStatus.COMPLETED


@pytest.mark.asyncio
async def test_the_archive_survives_a_restart(tmp_path):
    first = TelemetryArchive(tmp_path / "archive")
    trace, cursors = _trace(1, run_id="run_a")
    await first.put(trace, cursors)
    first.close()

    second = TelemetryArchive(tmp_path / "archive")
    try:
        stored, _ = await second.get(trace.trace_id)
        assert stored.run_id == "run_a"
        assert await second.max_cursor() == max(cursors.values())
    finally:
        second.close()
