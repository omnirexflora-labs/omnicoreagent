"""Audit A3: what recording a run costs the store, held to a bound.

Measured before this unit: every event was walked three times on the way into
the durable store, the finished trace was read back (deep-copied) four times
at the end of each run, replaying a stored trace sorted its events after every
single one, and pruning deep-copied every stored trace to read a timestamp.
These tests hold each of those to a number that cannot quietly grow again.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.telemetry import models as telemetry_models
from omnicoreagent.core.telemetry import store as telemetry_store
from omnicoreagent.core.telemetry.models import (
    ActorType,
    TelemetryActor,
    TelemetryEvent,
    TelemetrySpan,
    TelemetryTrace,
    TraceStatus,
    utc_now,
)
from omnicoreagent.core.telemetry.store import InMemoryTelemetryStore, JsonlTelemetryStore
from test_budget_enforcement import PricedModel, _agent


class _Count:
    """Counts calls of one function for the duration of a block."""

    def __init__(self, owner, name):
        self.owner, self.name, self.calls = owner, name, 0

    def __enter__(self):
        original = getattr(self.owner, self.name)
        counter = self

        if _is_async(original):

            async def wrapper(*args, **kwargs):
                counter.calls += 1
                return await original(*args, **kwargs)

        else:

            def wrapper(*args, **kwargs):
                counter.calls += 1
                return original(*args, **kwargs)

        self._original = original
        setattr(self.owner, self.name, wrapper)
        return self

    def __exit__(self, *_):
        setattr(self.owner, self.name, self._original)


def _is_async(function) -> bool:
    import asyncio

    return asyncio.iscoroutinefunction(function)


def _trace(trace_id: str, events: int) -> tuple[TelemetryTrace, list[TelemetryEvent]]:
    trace = TelemetryTrace(
        trace_id=trace_id,
        root_span_id=f"{trace_id}-root",
        status=TraceStatus.COMPLETED,
        ended_at=utc_now(),
    )
    trace.spans.append(
        TelemetrySpan(
            trace_id=trace_id,
            span_id=f"{trace_id}-root",
            name="agent.run",
            kind="agent.run",
            actor=TelemetryActor(type=ActorType.AGENT, name="a"),
        )
    )
    produced = [
        TelemetryEvent(
            trace_id=trace_id,
            span_id=f"{trace_id}-root",
            event_type="runtime_message",
            actor=TelemetryActor(type=ActorType.SYSTEM),
            sequence_number=index + 1,
            metadata={"n": index},
        )
        for index in range(events)
    ]
    return trace, produced


async def _stored(tmp_path, traces: int, events_each: int) -> JsonlTelemetryStore:
    """A durable store on disk holding this many finished traces."""
    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    for index in range(traces):
        trace, events = _trace(f"trace_{index}", events_each)
        await store.upsert_trace(trace)
        for event in events:
            await store.append_event(trace.trace_id, event)
    return store


# --- the live path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_recording_one_event_walks_it_at_most_twice(tmp_path):
    """Once for the line on disk, once to keep a copy in memory: not three."""
    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    trace, [event] = _trace("t", 1)
    await store.upsert_trace(trace)

    with _Count(telemetry_models, "to_plain") as one_walk:
        telemetry_models.to_plain(event)
    with _Count(telemetry_models, "to_plain") as walks:
        await store.append_event(trace.trace_id, event)

    assert walks.calls <= 2 * one_walk.calls, (
        f"recording one event walked {walks.calls} nodes; one walk is {one_walk.calls}"
    )


@pytest.mark.asyncio
async def test_one_request_reads_its_finished_trace_back_at_most_twice():
    """Once to close it, once to total it: not four deep copies."""
    agent = await _agent(PricedModel(), budgets=None)
    await agent.run("warm", session_id="reads-warm")

    with _Count(InMemoryTelemetryStore, "get_trace") as reads:
        await agent.run("go", session_id="reads-1")

    assert reads.calls <= 2, f"one request read its trace back {reads.calls} times"


# --- first use of a durable store ---------------------------------------------


@pytest.mark.asyncio
async def test_loading_a_store_does_not_copy_every_trace_it_holds(tmp_path):
    """Pruning on load reads a timestamp; it does not deep-copy the store."""
    await _stored(tmp_path, traces=40, events_each=5)
    reopened = JsonlTelemetryStore(tmp_path / "telemetry.jsonl", retention_days=7)

    with _Count(TelemetryTrace, "model_dump") as copies:
        await reopened.get_trace("trace_0")

    # The one copy is the read's own: what it hands back is not the store's.
    assert copies.calls <= 1, f"loading the store serialized {copies.calls} traces"


@pytest.mark.asyncio
async def test_replaying_a_trace_sorts_its_events_once_not_once_per_event(tmp_path):
    await _stored(tmp_path, traces=1, events_each=300)
    reopened = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")

    with _Count(telemetry_store, "_sort_events") as sorts:
        loaded = await reopened.get_trace("trace_0")

    assert loaded is not None and len(loaded.events) == 300
    assert [event.sequence_number for event in loaded.events] == list(range(1, 301))
    assert sorts.calls <= 1, f"replaying one trace sorted its events {sorts.calls} times"


@pytest.mark.asyncio
async def test_events_replayed_out_of_order_still_come_back_in_order(tmp_path):
    """The one sort still happens: a file whose lines are shuffled reads in order."""
    path = tmp_path / "telemetry.jsonl"
    await _stored(tmp_path, traces=1, events_each=6)
    lines = path.read_text().splitlines()
    head, events = lines[:1], lines[1:]
    path.write_text("\n".join(head + events[::-1]) + "\n")

    loaded = await JsonlTelemetryStore(path).get_trace("trace_0")

    assert [event.sequence_number for event in loaded.events] == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_what_is_stored_is_unchanged_by_the_cheaper_path(tmp_path):
    """The record on disk is byte-for-byte what it was: only the work changed."""
    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    trace, events = _trace("same", 3)
    await store.upsert_trace(trace)
    for event in events:
        await store.append_event(trace.trace_id, event)

    lines = [json.loads(line) for line in (tmp_path / "telemetry.jsonl").read_text().splitlines()]

    assert [line["record_type"] for line in lines] == ["trace_upsert", "event", "event", "event"]
    assert [line["payload"]["metadata"]["n"] for line in lines[1:]] == [0, 1, 2]
    reopened = await JsonlTelemetryStore(tmp_path / "telemetry.jsonl").get_trace("same")
    assert [event.metadata["n"] for event in reopened.events] == [0, 1, 2]


# --- writing to disk ------------------------------------------------------------
#
# Measured before this unit: the durable store was 61 ms of a 107 ms no-op
# request. Each of a request's ~30 records made its directory, opened the file,
# wrote, and closed it, on a worker thread. The file is now opened once and
# each record is flushed to the operating system as it is written, which is
# what closing did for durability: neither ever called fsync.


@pytest.mark.asyncio
async def test_recording_many_events_opens_the_file_once(tmp_path):
    from pathlib import Path

    store = JsonlTelemetryStore(tmp_path / "telemetry.jsonl")
    trace, events = _trace("t", 20)
    await store.upsert_trace(trace)
    target = str(tmp_path / "telemetry.jsonl")
    opened = 0
    original_open = Path.open

    def counted_open(self, *args, **kwargs):
        nonlocal opened
        if str(self) == target:
            opened += 1
        return original_open(self, *args, **kwargs)

    Path.open = counted_open
    try:
        for event in events:
            await store.append_event(trace.trace_id, event)
    finally:
        Path.open = original_open

    assert opened <= 1, f"20 records opened the file {opened} times"


@pytest.mark.asyncio
async def test_a_record_is_on_disk_before_the_call_returns(tmp_path):
    """Write-ahead means written: another reader sees it at once."""
    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path)
    trace, [event] = _trace("t", 1)
    await store.upsert_trace(trace)

    await store.append_event(trace.trace_id, event)

    lines = path.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["record_type"] == "event"
    other = await JsonlTelemetryStore(path).get_trace("t")
    assert other is not None and len(other.events) == 1


@pytest.mark.asyncio
async def test_compaction_and_later_records_survive_a_reopen(tmp_path):
    """Pruning rewrites the file underneath an open handle; what comes after
    the rewrite lands after it, and a reopen reads all of it."""
    from datetime import timedelta

    path = tmp_path / "telemetry.jsonl"
    store = JsonlTelemetryStore(path, retention_days=1)
    old, old_events = _trace("old", 2)
    old.ended_at = utc_now() - timedelta(days=3)
    await store.upsert_trace(old)
    for event in old_events:
        await store.append_event("old", event)
    kept, kept_events = _trace("kept", 2)
    await store.upsert_trace(kept)
    for event in kept_events:
        await store.append_event("kept", event)

    removed = await store.prune()
    later, later_events = _trace("later", 2)
    await store.upsert_trace(later)
    for event in later_events:
        await store.append_event("later", event)

    assert removed == 1
    reopened = JsonlTelemetryStore(path)
    assert await reopened.get_trace("old") is None
    assert len((await reopened.get_trace("kept")).events) == 2
    assert len((await reopened.get_trace("later")).events) == 2
