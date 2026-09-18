from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import threading

import pytest

from omnicoreagent.core.telemetry import (
    ActorType,
    JsonlTelemetryStore,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryStreamScope,
    TelemetryTrace,
    TraceEvidenceStatus,
    TraceStatus,
)
from omnicoreagent.core.telemetry import store as store_module
from omnicoreagent.core.telemetry.models import utc_now


def _trace(trace_id: str, *, ended_days_ago: int | None = None) -> TelemetryTrace:
    root = TelemetrySpan(
        trace_id=trace_id,
        name="agent.run",
        kind="agent.run",
        actor=TelemetryActor(type=ActorType.AGENT),
    )
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=root.span_id,
        status=TraceStatus.RUNNING,
        session_id=f"session-{trace_id}",
        spans=[root],
    )
    if ended_days_ago is not None:
        trace.status = TraceStatus.COMPLETED
        trace.started_at = utc_now() - timedelta(days=ended_days_ago, minutes=1)
        trace.ended_at = utc_now() - timedelta(days=ended_days_ago)
    return trace


def _event(trace_id: str, index: int) -> TelemetryEvent:
    return TelemetryEvent(
        trace_id=trace_id,
        event_type="agent_step",
        actor=TelemetryActor(type=ActorType.AGENT),
        output={"index": index},
    )


async def _cursor_map(store, scope=None) -> dict[str, str]:
    events = await store.get_events_after(scope or TelemetryStreamScope(), None)
    return {event.event_id: event.stream_cursor for event in events}


@pytest.mark.asyncio
async def test_jsonl_reload_restores_persisted_stream_cursors(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    first = JsonlTelemetryStore(path)
    await first.upsert_trace(_trace("trace-a"))
    await first.upsert_trace(_trace("trace-b"))
    for index in range(6):
        await first.append_event("trace-a" if index % 2 else "trace-b", _event(
            "trace-a" if index % 2 else "trace-b", index
        ))
    before = await _cursor_map(first)

    reloaded = JsonlTelemetryStore(path)

    assert await _cursor_map(reloaded) == before
    assert await reloaded.get_stream_cursor(TelemetryStreamScope()) == (
        await first.get_stream_cursor(TelemetryStreamScope())
    )


@pytest.mark.asyncio
async def test_jsonl_corrupt_line_does_not_shift_later_cursors(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    first = JsonlTelemetryStore(path)
    await first.upsert_trace(_trace("trace-a"))
    events = [_event("trace-a", index) for index in range(6)]
    for event in events:
        await first.append_event("trace-a", event)
    before = await _cursor_map(first)

    lines = path.read_text().splitlines()
    corrupt_index = next(
        index
        for index, line in enumerate(lines)
        if events[2].event_id in line
    )
    lines[corrupt_index] = lines[corrupt_index][: len(lines[corrupt_index]) // 2]
    path.write_text("\n".join(lines) + "\n")

    reloaded = JsonlTelemetryStore(path)
    after = await _cursor_map(reloaded)

    expected = dict(before)
    expected.pop(events[2].event_id)
    assert after == expected
    resumed = await reloaded.get_events_after(
        TelemetryStreamScope(), before[events[3].event_id]
    )
    assert [event.event_id for event in resumed] == [
        events[4].event_id,
        events[5].event_id,
    ]
    assert reloaded.skipped_records == 1
    trace = await reloaded.get_trace("trace-a")
    assert trace.incomplete is True
    assert trace.evidence_status == TraceEvidenceStatus.PARTIAL


@pytest.mark.asyncio
async def test_jsonl_reload_keeps_cursors_for_events_embedded_in_upserts(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    first = JsonlTelemetryStore(path)
    await first.upsert_trace(_trace("trace-live"))
    await first.append_event("trace-live", _event("trace-live", 0))
    imported = _trace("trace-imported")
    imported.events = [_event("trace-imported", index) for index in range(3)]
    for number, event in enumerate(imported.events, start=1):
        event.sequence_number = number
    await first.upsert_trace(imported)
    await first.append_event("trace-live", _event("trace-live", 1))
    before = await _cursor_map(first)

    # Dropping the first line changes the replay counter for any cursor that
    # is re-derived rather than restored.
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[1:]) + "\n")
    reloaded = JsonlTelemetryStore(path)
    after = await _cursor_map(reloaded)

    for event in imported.events:
        assert after[event.event_id] == before[event.event_id]


@pytest.mark.asyncio
async def test_jsonl_prune_keeps_live_followers_and_monotonic_cursors(tmp_path):
    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    await store.upsert_trace(_trace("trace-old", ended_days_ago=30))
    await store.append_event("trace-old", _event("trace-old", 0))
    await store.upsert_trace(_trace("trace-active"))
    await store.append_event("trace-active", _event("trace-active", 0))
    cursor_before = int(await store.get_stream_cursor(TelemetryStreamScope()))

    scope = TelemetryStreamScope(trace_id="trace-active")
    stream = store.stream_after(scope, str(cursor_before))
    next_event = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)

    assert await store.prune(retention_days=7) == 1
    assert int(await store.get_stream_cursor(TelemetryStreamScope())) >= cursor_before

    live = _event("trace-active", 1)
    await store.append_event("trace-active", live)
    received = await asyncio.wait_for(next_event, timeout=1)
    await stream.aclose()

    assert received.event_id == live.event_id
    assert int(received.stream_cursor) > cursor_before
    assert await store.get_trace("trace-old") is None


@pytest.mark.asyncio
async def test_jsonl_compacted_file_preserves_cursors_and_order(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path)
    await store.upsert_trace(_trace("trace-old", ended_days_ago=30))
    await store.upsert_trace(_trace("trace-a"))
    await store.upsert_trace(_trace("trace-b"))
    await store.append_event("trace-old", _event("trace-old", 0))
    for index in range(4):
        trace_id = "trace-a" if index % 2 else "trace-b"
        await store.append_event(trace_id, _event(trace_id, index))
    old_events = {
        event.event_id
        for event in (await store.get_trace("trace-old")).events
    }
    before = {
        event_id: cursor
        for event_id, cursor in (await _cursor_map(store)).items()
        if event_id not in old_events
    }

    await store.prune(retention_days=7)
    assert await _cursor_map(store) == before

    reloaded = JsonlTelemetryStore(path)
    after = await _cursor_map(reloaded)

    assert after == before
    assert list(after) == list(before)


@pytest.mark.asyncio
async def test_jsonl_timed_out_write_does_not_interleave_with_next_write(
    tmp_path, monkeypatch
):
    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path)
    await store.upsert_trace(_trace("trace-a"))

    release_first = threading.Event()
    original_append = store_module._append_text
    calls = {"count": 0}

    def slow_first_append(target, text):
        calls["count"] += 1
        if calls["count"] == 1:
            release_first.wait(timeout=5)
        original_append(target, text)

    monkeypatch.setattr(store_module, "_append_text", slow_first_append)
    first = _event("trace-a", 0)
    second = _event("trace-a", 1)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(store.append_event("trace-a", first), timeout=0.05)
    second_write = asyncio.ensure_future(store.append_event("trace-a", second))
    await asyncio.sleep(0.05)
    release_first.set()
    await asyncio.wait_for(second_write, timeout=5)
    await asyncio.sleep(0.1)

    records = [json.loads(line) for line in path.read_text().splitlines()]
    event_ids = [
        record["payload"]["event_id"]
        for record in records
        if record["record_type"] == "event"
    ]
    assert event_ids == [first.event_id, second.event_id]


@pytest.mark.asyncio
async def test_jsonl_store_reads_records_written_in_previous_format(tmp_path):
    from pathlib import Path
    import shutil

    fixture = (
        Path(__file__).resolve().parents[1]
        / "engineering/validation/fixtures/telemetry-evidence-acceptance"
        / "transformed.jsonl"
    )
    path = tmp_path / "telemetry.jsonl"
    shutil.copy(fixture, path)
    event_ids = {
        json.loads(line)["payload"]["event_id"]
        for line in fixture.read_text().splitlines()
        if json.loads(line)["record_type"] == "event"
    }

    store = JsonlTelemetryStore(path)
    events = await store.get_events_after(TelemetryStreamScope(), None)

    assert store.skipped_records == 0
    assert {event.event_id for event in events} == event_ids
    cursors = [int(event.stream_cursor) for event in events]
    assert cursors == sorted(cursors)
    assert len(set(cursors)) == len(cursors)


@pytest.mark.parametrize("store_kind", ["memory", "jsonl"])
def test_a_shared_store_keeps_every_event_across_event_loops(tmp_path, store_kind):
    from omnicoreagent.core.telemetry import InMemoryTelemetryStore

    store = (
        InMemoryTelemetryStore()
        if store_kind == "memory"
        else JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    )

    async def burst(trace_id):
        await store.upsert_trace(_trace(trace_id))
        # Concurrent writes contend for the store lock.
        await asyncio.gather(
            *(store.append_event(trace_id, _event(trace_id, i)) for i in range(25))
        )
        return len((await store.get_trace(trace_id)).events)

    # Each asyncio.run is a new event loop sharing the same store object.
    assert asyncio.run(burst("trace-loop-1")) == 25
    assert asyncio.run(burst("trace-loop-2")) == 25
